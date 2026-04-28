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
    workspace_root = tmp_path / "wwwroot"
    manager = JobManager(tmp_path / "data", workspace_root=workspace_root)
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
        workspace_name="campus-service",
    )

    assert job.state == "uploaded"
    assert job.project_goal == "校园综合服务网页"
    assert job.workspace_dir == workspace_root / "campus-service"
    assert job.to_dict()["workspace_path"] == str(workspace_root / "campus-service")
    assert (job.workspace_dir / "auth-only" / "README.md").exists()
    assert not (job.workspace_dir / "auth-only" / "node_modules" / "ignored.js").exists()
    assert (job.constraints_dir / "rules.md").exists()
    assert (job.inputs_dir / "source.zip").exists()
    assert manager.history_detail(job.id)["workspace_path"] == str(workspace_root / "campus-service")


def test_create_job_from_zip_rejects_empty_goal_and_unsafe_zip(tmp_path: Path) -> None:
    manager = JobManager(tmp_path / "data", workspace_root=tmp_path / "wwwroot")

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


def test_workspace_name_defaults_and_rejects_unsafe_names(tmp_path: Path) -> None:
    workspace_root = tmp_path / "wwwroot"
    manager = JobManager(tmp_path / "data", workspace_root=workspace_root)

    job = manager.create_job_from_content("- [ ] task\n", {})
    assert job.workspace_dir.parent == workspace_root
    assert job.workspace_dir.name == f"codex-{job.id}"

    for unsafe_name in ["   ", "../x", "a/b", "a\\b", "/abs", "bad..name", "C:drive"]:
        try:
            manager.create_job_from_zip(
                make_zip({"README.md": "# ok\n"}),
                "project.zip",
                "校园综合服务网页",
                {},
                workspace_name=unsafe_name,
            )
        except PlanError:
            pass
        else:
            raise AssertionError(f"expected PlanError for {unsafe_name!r}")

    (workspace_root / "taken").mkdir()
    try:
        manager.create_job_from_zip(
            make_zip({"README.md": "# ok\n"}),
            "project.zip",
            "校园综合服务网页",
            {},
            workspace_name="taken",
        )
    except PlanError as exc:
        assert "already exists" in str(exc)
    else:
        raise AssertionError("expected PlanError")


def test_create_job_from_documents_imports_docs_and_optional_zip(tmp_path: Path) -> None:
    workspace_root = tmp_path / "wwwroot"
    manager = JobManager(tmp_path / "data", workspace_root=workspace_root)
    plan = "# Imported Plan\n\n- [ ] direct task\n- [x] finished task\n"
    job = manager.create_job_from_documents(
        plan,
        "handoff ready\n",
        "test report ready\n",
        {"rules.txt": "follow the plan\n"},
        project_zip=make_zip({"README.md": "# base\n", "PLAN.md": "# old\n- [ ] old task\n"}),
        project_zip_filename="base.zip",
        project_goal="  Direct import  ",
        workspace_name="direct-import",
    )

    assert job.state == "awaiting_start"
    assert job.project_goal == "Direct import"
    assert job.workspace_dir == workspace_root / "direct-import"
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
    manager = JobManager(tmp_path / "data", workspace_root=tmp_path / "wwwroot")

    try:
        manager.create_job_from_documents("- [ ] task\n", " ", "report\n", {})
    except PlanError as exc:
        assert "HANDOFF.md" in str(exc)
    else:
        raise AssertionError("expected PlanError")


def test_legacy_history_uses_job_workspace_when_metadata_has_no_workspace_path(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    job_id = "20260101-000000-abc12345"
    workspace_dir = data_dir / "jobs" / job_id / "workspace"
    workspace_dir.mkdir(parents=True)
    (workspace_dir / "PLAN.md").write_text("- [ ] legacy task\n", encoding="utf-8")
    (workspace_dir / "README.md").write_text("# legacy\n", encoding="utf-8")

    manager = JobManager(data_dir, workspace_root=tmp_path / "wwwroot")

    detail = manager.history_detail(job_id)
    assert detail["workspace_path"] == str(workspace_dir)

    document = manager.history_document(job_id, "plan")
    assert document["available"] is True
    assert "legacy task" in document["content"]

    paths = {item["path"] for item in manager.history_file_tree(job_id)}
    assert {"PLAN.md", "README.md"} <= paths


def test_planning_and_revision_stay_awaiting_start(tmp_path: Path) -> None:
    fake_codex = make_fake_codex(tmp_path / "fake_codex.py")
    manager = JobManager(
        tmp_path / "data",
        codex_bin=f'"{sys.executable}" "{fake_codex}"',
        workspace_root=tmp_path / "wwwroot",
    )
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
    manager = JobManager(tmp_path / "data", workspace_root=tmp_path / "wwwroot")
    manager.create_job_from_zip(make_zip({"auth-only/README.md": "# auth\n"}), "auth-only.zip", "校园综合服务网页", {})

    try:
        manager.start_current_job()
    except PlanError as exc:
        assert "PLAN.md" in str(exc)
    else:
        raise AssertionError("expected PlanError")


def test_fake_codex_execution_advances_ten_tasks_per_round(tmp_path: Path) -> None:
    fake_codex = make_fake_codex(tmp_path / "fake_codex.py")
    manager = JobManager(
        tmp_path / "data",
        codex_bin=f'"{sys.executable}" "{fake_codex}"',
        workspace_root=tmp_path / "wwwroot",
    )
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
    manager = JobManager(
        tmp_path / "data",
        codex_bin=f'"{sys.executable}" "{fake_codex}"',
        workspace_root=tmp_path / "wwwroot",
    )
    manager.create_job_from_content("- [ ] task 1\n- [ ] task 2\n", {})

    asyncio.run(manager.run_current_job())

    status = manager.status()
    assert status["state"] == "failed"
    assert "did not decrease" in status["job"]["failure_reason"]


def test_codex_subprocess_timeout_marks_job_failed(tmp_path: Path, monkeypatch) -> None:
    fake_codex = tmp_path / "slow_codex.py"
    fake_codex.write_text(
        """
import sys
import time

sys.stdin.read()
time.sleep(30)
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("CODEX_RUN_TIMEOUT_SECONDS", "1")
    manager = JobManager(
        tmp_path / "data",
        codex_bin=f'"{sys.executable}" "{fake_codex}"',
        workspace_root=tmp_path / "wwwroot",
    )
    manager.create_job_from_content("- [ ] task 1\n", {})

    asyncio.run(manager.run_current_job())

    status = manager.status()
    assert status["state"] == "failed"
    assert "timed out after 1 seconds" in status["job"]["failure_reason"]
