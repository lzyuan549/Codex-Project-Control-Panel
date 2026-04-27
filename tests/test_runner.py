from __future__ import annotations

import asyncio
import io
import sys
import zipfile
from pathlib import Path

from app.runner import JobManager, PlanError, parse_plan_tasks, select_pending_batch


def make_zip(entries: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in entries.items():
            archive.writestr(name, content)
    return buffer.getvalue()


def make_fake_codex(path: Path) -> Path:
    path.write_text(
        """
import pathlib
import sys

args = sys.argv[1:]
workspace = pathlib.Path(args[args.index('-C') + 1])
final = pathlib.Path(args[args.index('-o') + 1])
prompt = sys.stdin.read()
plan = workspace / 'PLAN.md'
handoff = workspace / 'HANDOFF.md'
report = workspace / 'TEST_REPORT.md'

if 'PROMPT_STAGE: execution' in prompt:
    text = plan.read_text(encoding='utf-8')
    for _ in range(10):
        if '- [ ]' not in text:
            break
        text = text.replace('- [ ]', '- [x]', 1)
    plan.write_text(text, encoding='utf-8')
    handoff.write_text('执行阶段交接', encoding='utf-8')
    report.write_text('执行阶段测试记录', encoding='utf-8')
    final.write_text('execution finished', encoding='utf-8')
elif 'PROMPT_STAGE: revision' in prompt:
    text = plan.read_text(encoding='utf-8')
    plan.write_text(text + '\\n\\n## 修订记录\\n- 根据用户反馈调整规划。\\n', encoding='utf-8')
    handoff.write_text('等待开始，规划已修订', encoding='utf-8')
    report.write_text('未执行 + 原因：规划修订阶段', encoding='utf-8')
    final.write_text('revision finished', encoding='utf-8')
else:
    plan.write_text('# Project Plan\\n\\n## Execution Checklist\\n' + '\\n'.join(f'- [ ] {i}. 中文任务{i}' for i in range(12)) + '\\n', encoding='utf-8')
    handoff.write_text('等待开始，规划已生成', encoding='utf-8')
    report.write_text('未执行 + 原因：当前仅规划阶段', encoding='utf-8')
    final.write_text('planning finished', encoding='utf-8')
print('{"event":"done"}')
""",
        encoding="utf-8",
    )
    return path


def test_parse_plan_tasks_and_batch() -> None:
    plan = """# Plan
- [ ] first
- [x] second
- [X] third
  - [ ] nested still works
- [ ] fourth
"""
    tasks = parse_plan_tasks(plan)

    assert len(tasks) == 5
    assert [task.completed for task in tasks] == [False, True, True, False, False]
    assert [task.text for task in select_pending_batch(tasks, 2)] == ["first", "nested still works"]


def test_create_job_from_zip_extracts_auth_only_and_constraints(tmp_path: Path) -> None:
    manager = JobManager(tmp_path / "data")
    job = manager.create_job_from_zip(
        make_zip(
            {
                "auth-only/README.md": "# 基础权限说明\n",
                "auth-only/backend/src/App.java": "class App {}\n",
                "auth-only/node_modules/ignored.js": "ignored\n",
            }
        ),
        "auth-only.zip",
        "校园综合服务网页",
        {"rules.md": "# 规则\n"},
    )

    assert job.state == "uploaded"
    assert job.project_goal == "校园综合服务网页"
    assert (job.workspace_dir / "auth-only" / "README.md").exists()
    assert not (job.workspace_dir / "auth-only" / "node_modules" / "ignored.js").exists()
    assert (job.constraints_dir / "rules.md").exists()
    assert (job.inputs_dir / "source.zip").exists()


def test_create_job_from_zip_rejects_empty_goal_and_unsafe_zip(tmp_path: Path) -> None:
    manager = JobManager(tmp_path / "data")

    try:
        manager.create_job_from_zip(make_zip({"README.md": "# ok\n"}), "project.zip", " ", {})
    except PlanError as exc:
        assert "goal" in str(exc)
    else:
        raise AssertionError("expected PlanError")

    try:
        manager.create_job_from_zip(make_zip({"../evil.txt": "bad"}), "project.zip", "校园综合服务网页", {})
    except PlanError as exc:
        assert "Unsafe ZIP path" in str(exc)
    else:
        raise AssertionError("expected PlanError")


def test_create_job_from_documents_imports_docs_and_optional_zip(tmp_path: Path) -> None:
    manager = JobManager(tmp_path / "data")
    plan = "# Imported Plan\n\n- [ ] direct task\n- [x] finished task\n"
    job = manager.create_job_from_documents(
        plan,
        "handoff ready\n",
        "test report ready\n",
        {"rules.txt": "follow the plan\n"},
        project_zip=make_zip({"README.md": "# base\n", "PLAN.md": "# old\n- [ ] old task\n"}),
        project_zip_filename="base.zip",
        project_goal="  Direct import  ",
    )

    assert job.state == "awaiting_start"
    assert job.project_goal == "Direct import"
    assert job.total_tasks == 2
    assert job.pending_tasks == 1
    assert job.completed_tasks == 1
    assert (job.workspace_dir / "README.md").exists()
    assert (job.workspace_dir / "PLAN.md").read_text(encoding="utf-8") == plan
    assert (job.workspace_dir / "HANDOFF.md").read_text(encoding="utf-8") == "handoff ready\n"
    assert (job.workspace_dir / "TEST_REPORT.md").read_text(encoding="utf-8") == "test report ready\n"
    assert (job.inputs_dir / "PLAN.md").read_text(encoding="utf-8") == plan
    assert (job.inputs_dir / "source.zip").exists()
    assert (job.constraints_dir / "rules.txt").exists()


def test_create_job_from_documents_rejects_empty_context_docs(tmp_path: Path) -> None:
    manager = JobManager(tmp_path / "data")

    try:
        manager.create_job_from_documents("- [ ] task\n", " ", "report\n", {})
    except PlanError as exc:
        assert "HANDOFF.md" in str(exc)
    else:
        raise AssertionError("expected PlanError")


def test_planning_and_revision_stay_awaiting_start(tmp_path: Path) -> None:
    fake_codex = make_fake_codex(tmp_path / "fake_codex.py")
    manager = JobManager(tmp_path / "data", codex_bin=f'"{sys.executable}" "{fake_codex}"')
    job = manager.create_job_from_zip(make_zip({"auth-only/README.md": "# auth\n"}), "auth-only.zip", "校园综合服务网页", {})

    asyncio.run(manager.run_planning_job(job))
    status = manager.status()
    assert status["state"] == "awaiting_start"
    assert status["job"]["pending_tasks"] == 12
    assert (job.workspace_dir / "PLAN.md").exists()
    assert (job.workspace_dir / "HANDOFF.md").exists()
    assert (job.workspace_dir / "TEST_REPORT.md").exists()

    asyncio.run(manager.run_revision_job(job, "课程表拆成后台和学生端"))
    status = manager.status()
    assert status["state"] == "awaiting_start"
    assert "修订记录" in (job.workspace_dir / "PLAN.md").read_text(encoding="utf-8")


def test_start_rejects_before_plan_is_generated(tmp_path: Path) -> None:
    manager = JobManager(tmp_path / "data")
    manager.create_job_from_zip(make_zip({"auth-only/README.md": "# auth\n"}), "auth-only.zip", "校园综合服务网页", {})

    try:
        manager.start_current_job()
    except PlanError as exc:
        assert "PLAN.md" in str(exc)
    else:
        raise AssertionError("expected PlanError")


def test_fake_codex_execution_advances_ten_tasks_per_round(tmp_path: Path) -> None:
    fake_codex = make_fake_codex(tmp_path / "fake_codex.py")
    manager = JobManager(tmp_path / "data", codex_bin=f'"{sys.executable}" "{fake_codex}"')
    manager.create_job_from_content("\n".join(f"- [ ] task {i}" for i in range(12)), {})

    asyncio.run(manager.run_current_job())

    status = manager.status()
    assert status["state"] == "completed"
    assert status["job"]["pending_tasks"] == 0
    assert status["job"]["current_round"] == 2
    first_prompt = next((tmp_path / "data" / "jobs").glob("*/logs/round-001-execution/prompt.md")).read_text(
        encoding="utf-8"
    )
    selected = first_prompt.split("【执行规则】", 1)[0]
    assert "task 0" in selected
    assert "task 9" in selected
    assert "task 10" not in selected


def test_fake_codex_no_progress_fails(tmp_path: Path) -> None:
    fake_codex = tmp_path / "fake_codex.py"
    fake_codex.write_text(
        """
import pathlib
import sys

args = sys.argv[1:]
final = pathlib.Path(args[args.index('-o') + 1])
sys.stdin.read()
final.write_text('no changes', encoding='utf-8')
print('{"event":"done"}')
""",
        encoding="utf-8",
    )
    manager = JobManager(tmp_path / "data", codex_bin=f'"{sys.executable}" "{fake_codex}"')
    manager.create_job_from_content("- [ ] task 1\n- [ ] task 2\n", {})

    asyncio.run(manager.run_current_job())

    status = manager.status()
    assert status["state"] == "failed"
    assert "did not decrease" in status["job"]["failure_reason"]
