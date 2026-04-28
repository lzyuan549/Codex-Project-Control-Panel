from __future__ import annotations

import io
import sys
import time
import zipfile
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app


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


def wait_for_state(client: TestClient, expected: str, timeout: float = 5.0) -> dict:
    deadline = time.time() + timeout
    last: dict | None = None
    while time.time() < deadline:
        response = client.get("/api/job/status")
        assert response.status_code == 200
        last = response.json()
        if last["state"] == expected:
            return last
        time.sleep(0.05)
    raise AssertionError(f"expected state {expected}, got {last}")


def test_login_upload_plan_revise_execute_history_and_download(tmp_path: Path, monkeypatch) -> None:
    fake_codex = make_fake_codex(tmp_path / "fake_codex.py")
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("ADMIN_PASSWORD", "secret")
    monkeypatch.setenv("SESSION_SECRET", "test-secret")
    monkeypatch.setenv("CODEX_BIN", f'"{sys.executable}" "{fake_codex}"')

    with TestClient(app) as client:
        assert client.get("/api/job/status").status_code == 401

        bad = client.post("/api/login", json={"password": "wrong"})
        assert bad.status_code == 401

        ok = client.post("/api/login", json={"password": "secret"})
        assert ok.status_code == 200

        early_start = client.post("/api/job/start", json={})
        assert early_start.status_code == 400

        files = {
            "project_zip": ("auth-only.zip", make_zip({"auth-only/README.md": "# 基础权限\n"}), "application/zip"),
            "constraints": ("rules.md", b"# Rules\nKeep it tidy.\n", "text/markdown"),
        }
        uploaded = client.post(
            "/api/upload",
            data={"project_goal": "校园综合服务网页，课程表/失物/二手/公告等全整合，适配手机端"},
            files=files,
        )
        assert uploaded.status_code == 200
        job_id = uploaded.json()["job"]["id"]
        assert uploaded.json()["job"]["state"] == "uploaded"

        status = client.get("/api/job/status")
        assert status.status_code == 200
        assert status.json()["state"] == "uploaded"

        start_before_plan = client.post("/api/job/start", json={})
        assert start_before_plan.status_code == 400

        plan_started = client.post("/api/job/plan", json={})
        assert plan_started.status_code == 200
        planned = wait_for_state(client, "awaiting_start")
        assert planned["job"]["pending_tasks"] == 12

        plan_doc = client.get("/api/documents/plan")
        assert plan_doc.status_code == 200
        assert "Project Plan" in plan_doc.json()["content"]

        handoff_doc = client.get("/api/documents/handoff")
        assert handoff_doc.status_code == 200
        assert handoff_doc.json()["available"] is True

        revised = client.post("/api/job/revise-plan", json={"feedback": "课程表拆成后台和学生端"})
        assert revised.status_code == 200
        wait_for_state(client, "awaiting_start")
        revised_doc = client.get("/api/documents/plan")
        assert "修订记录" in revised_doc.json()["content"]

        started = client.post("/api/job/start", json={})
        assert started.status_code == 200
        final_status = wait_for_state(client, "completed")
        assert final_status["job"]["pending_tasks"] == 0

        history = client.get("/api/history")
        assert history.status_code == 200
        assert history.json()["jobs"][0]["id"] == job_id
        assert history.json()["jobs"][0]["documents"]["plan"] is True

        history_detail = client.get(f"/api/history/{job_id}")
        assert history_detail.status_code == 200
        assert history_detail.json()["job"]["constraint_count"] == 1

        history_plan = client.get(f"/api/history/{job_id}/documents/plan")
        assert history_plan.status_code == 200
        assert "Project Plan" in history_plan.json()["content"]

        history_report = client.get(f"/api/history/{job_id}/documents/test_report")
        assert history_report.status_code == 200
        assert history_report.json()["available"] is True

        tree = client.get("/api/files")
        assert tree.status_code == 200
        paths = {item["path"] for item in tree.json()["files"]}
        assert "PLAN.md" in paths
        assert "auth-only/README.md" in paths
        assert "constraints/rules.md" in paths

        zip_response = client.get("/api/files/download")
        assert zip_response.status_code == 200
        assert zip_response.headers["content-type"] == "application/zip"

        history_logs = client.get(f"/api/history/{job_id}/logs")
        assert history_logs.status_code == 200
        assert len(history_logs.json()["logs"]) >= 4

        history_tree = client.get(f"/api/history/{job_id}/files")
        assert history_tree.status_code == 200
        history_paths = {item["path"] for item in history_tree.json()["files"]}
        assert "PLAN.md" in history_paths

        history_zip = client.get(f"/api/history/{job_id}/download")
        assert history_zip.status_code == 200
        assert history_zip.headers["content-type"] == "application/zip"

        bad_history = client.get("/api/history/../bad")
        assert bad_history.status_code in {404, 422}


def test_document_import_uploads_and_starts_without_planning(tmp_path: Path, monkeypatch) -> None:
    fake_codex = make_fake_codex(tmp_path / "fake_codex.py")
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("ADMIN_PASSWORD", "secret")
    monkeypatch.setenv("SESSION_SECRET", "test-secret")
    monkeypatch.setenv("CODEX_BIN", f'"{sys.executable}" "{fake_codex}"')

    with TestClient(app) as client:
        assert client.post("/api/upload-documents").status_code == 401
        assert client.post("/api/login", json={"password": "secret"}).status_code == 200

        files = [
            ("plan", ("PLAN.md", b"# Imported\n\n- [ ] task 1\n- [ ] task 2\n", "text/markdown")),
            ("handoff", ("HANDOFF.md", "handoff ready\n".encode("utf-8"), "text/markdown")),
            ("test_report", ("TEST_REPORT.md", "report ready\n".encode("utf-8"), "text/markdown")),
            (
                "project_zip",
                (
                    "base.zip",
                    make_zip({"README.md": "# base\n", "PLAN.md": "# Old\n- [ ] old task\n"}),
                    "application/zip",
                ),
            ),
            ("constraints", ("rules.txt", b"keep it tidy\n", "text/plain")),
        ]
        uploaded = client.post("/api/upload-documents", data={"project_goal": "直接导入"}, files=files)

        assert uploaded.status_code == 200
        job_id = uploaded.json()["job"]["id"]
        assert uploaded.json()["job"]["state"] == "awaiting_start"
        assert uploaded.json()["job"]["pending_tasks"] == 2

        plan_doc = client.get("/api/documents/plan")
        assert plan_doc.status_code == 200
        assert "# Imported" in plan_doc.json()["content"]
        assert "# Old" not in plan_doc.json()["content"]

        tree = client.get("/api/files")
        assert tree.status_code == 200
        paths = {item["path"] for item in tree.json()["files"]}
        assert {"PLAN.md", "HANDOFF.md", "TEST_REPORT.md", "README.md", "constraints/rules.txt"} <= paths

        started = client.post("/api/job/start", json={})
        assert started.status_code == 200
        final_status = wait_for_state(client, "completed")
        assert final_status["job"]["id"] == job_id
        assert final_status["job"]["pending_tasks"] == 0


def test_document_import_rejects_invalid_documents(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("ADMIN_PASSWORD", "secret")
    monkeypatch.setenv("SESSION_SECRET", "test-secret")

    with TestClient(app) as client:
        assert client.post("/api/login", json={"password": "secret"}).status_code == 200

        missing = client.post(
            "/api/upload-documents",
            files=[
                ("handoff", ("HANDOFF.md", b"handoff\n", "text/markdown")),
                ("test_report", ("TEST_REPORT.md", b"report\n", "text/markdown")),
            ],
        )
        assert missing.status_code == 400

        bad_encoding = client.post(
            "/api/upload-documents",
            files=[
                ("plan", ("PLAN.md", b"\xff", "text/markdown")),
                ("handoff", ("HANDOFF.md", b"handoff\n", "text/markdown")),
                ("test_report", ("TEST_REPORT.md", b"report\n", "text/markdown")),
            ],
        )
        assert bad_encoding.status_code == 400

        no_tasks = client.post(
            "/api/upload-documents",
            files=[
                ("plan", ("PLAN.md", b"# no tasks\n", "text/markdown")),
                ("handoff", ("HANDOFF.md", b"handoff\n", "text/markdown")),
                ("test_report", ("TEST_REPORT.md", b"report\n", "text/markdown")),
            ],
        )
        assert no_tasks.status_code == 400

        blank_handoff = client.post(
            "/api/upload-documents",
            files=[
                ("plan", ("PLAN.md", b"- [ ] task\n", "text/markdown")),
                ("handoff", ("HANDOFF.md", b" \n", "text/markdown")),
                ("test_report", ("TEST_REPORT.md", b"report\n", "text/markdown")),
            ],
        )
        assert blank_handoff.status_code == 400

        blank_report = client.post(
            "/api/upload-documents",
            files=[
                ("plan", ("PLAN.md", b"- [ ] task\n", "text/markdown")),
                ("handoff", ("HANDOFF.md", b"handoff\n", "text/markdown")),
                ("test_report", ("TEST_REPORT.md", b" \n", "text/markdown")),
            ],
        )
        assert blank_report.status_code == 400

        not_markdown = client.post(
            "/api/upload-documents",
            files=[
                ("plan", ("PLAN.txt", b"- [ ] task\n", "text/plain")),
                ("handoff", ("HANDOFF.md", b"handoff\n", "text/markdown")),
                ("test_report", ("TEST_REPORT.md", b"report\n", "text/markdown")),
            ],
        )
        assert not_markdown.status_code == 400


def test_prompt_templates_can_be_viewed_and_updated(tmp_path: Path, monkeypatch) -> None:
    prompt_dir = tmp_path / "prompt_templates"
    prompt_dir.mkdir()
    (prompt_dir / "planning_prompt.md").write_text(
        "PROMPT_STAGE: planning\n{{PROJECT_GOAL}}\n{{CONSTRAINTS}}\n{{WORKSPACE_FILES}}\n",
        encoding="utf-8",
    )
    (prompt_dir / "revision_prompt.md").write_text(
        (
            "PROMPT_STAGE: revision\n"
            "{{PROJECT_GOAL}}\n{{FEEDBACK}}\n{{PLAN_MD}}\n{{HANDOFF_MD}}\n{{TEST_REPORT_MD}}\n{{CONSTRAINTS}}\n"
        ),
        encoding="utf-8",
    )
    (prompt_dir / "continuation_prompt.md").write_text(
        (
            "PROMPT_STAGE: execution\n"
            "{{PROJECT_GOAL}}\n{{SELECTED_BATCH}}\n{{PLAN_MD}}\n{{HANDOFF_MD}}\n{{TEST_REPORT_MD}}\n{{CONSTRAINTS}}\n"
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("app.main.PROMPT_TEMPLATE_DIR", prompt_dir)
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("ADMIN_PASSWORD", "secret")
    monkeypatch.setenv("SESSION_SECRET", "test-secret")

    with TestClient(app) as client:
        assert client.get("/api/prompt-templates").status_code == 401
        assert client.post("/api/login", json={"password": "secret"}).status_code == 200

        listing = client.get("/api/prompt-templates")
        assert listing.status_code == 200
        templates = listing.json()["templates"]
        assert {template["id"] for template in templates} == {"planning", "revision", "execution"}
        assert next(template for template in templates if template["id"] == "planning")["filename"] == "planning_prompt.md"

        updated_content = (
            "PROMPT_STAGE: planning\n"
            "{{PROJECT_GOAL}}\n{{CONSTRAINTS}}\n{{WORKSPACE_FILES}}\n"
            "请在规划里补充风险说明。\n"
        )
        updated = client.put("/api/prompt-templates/planning", json={"content": updated_content})
        assert updated.status_code == 200
        assert updated.json()["template"]["content"] == updated_content
        assert (prompt_dir / "planning_prompt.md").read_text(encoding="utf-8") == updated_content

        missing_placeholder = client.put(
            "/api/prompt-templates/planning",
            json={"content": "PROMPT_STAGE: planning\n{{PROJECT_GOAL}}\n"},
        )
        assert missing_placeholder.status_code == 400

        invalid_template = client.get("/api/prompt-templates/unknown")
        assert invalid_template.status_code == 404
