from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, Response, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .auth import AuthConfig, clear_session_cookie, require_session, set_session_cookie
from .runner import JobManager, PlanError, PROMPT_TEMPLATE_DIR
from .settings import load_settings, write_codex_config


APP_DIR = Path(__file__).resolve().parent
STATIC_DIR = APP_DIR.parent / "static"


class LoginRequest(BaseModel):
    password: str


class RevisionRequest(BaseModel):
    feedback: str


class PromptTemplateUpdate(BaseModel):
    content: str


PROMPT_TEMPLATE_DEFINITIONS = {
    "planning": {
        "filename": "planning_prompt.md",
        "label": "规划阶段",
        "stage": "planning",
        "required_placeholders": ["PROJECT_GOAL", "CONSTRAINTS", "WORKSPACE_FILES"],
    },
    "revision": {
        "filename": "revision_prompt.md",
        "label": "修订阶段",
        "stage": "revision",
        "required_placeholders": [
            "PROJECT_GOAL",
            "FEEDBACK",
            "PLAN_MD",
            "HANDOFF_MD",
            "TEST_REPORT_MD",
            "CONSTRAINTS",
        ],
    },
    "execution": {
        "filename": "continuation_prompt.md",
        "label": "执行阶段",
        "stage": "execution",
        "required_placeholders": [
            "PROJECT_GOAL",
            "SELECTED_BATCH",
            "PLAN_MD",
            "HANDOFF_MD",
            "TEST_REPORT_MD",
            "CONSTRAINTS",
        ],
    },
}


def prompt_template_path(template_id: str) -> Path:
    definition = PROMPT_TEMPLATE_DEFINITIONS.get(template_id)
    if definition is None:
        raise HTTPException(status_code=404, detail="Invalid prompt template.")
    return PROMPT_TEMPLATE_DIR / definition["filename"]


def prompt_template_payload(template_id: str) -> dict:
    path = prompt_template_path(template_id)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Prompt template file does not exist.")
    definition = PROMPT_TEMPLATE_DEFINITIONS[template_id]
    stat = path.stat()
    return {
        "id": template_id,
        "label": definition["label"],
        "stage": definition["stage"],
        "filename": definition["filename"],
        "required_placeholders": definition["required_placeholders"],
        "content": path.read_text(encoding="utf-8"),
        "size": stat.st_size,
        "updated_at": stat.st_mtime,
    }


def validate_prompt_template_content(template_id: str, content: str) -> None:
    definition = PROMPT_TEMPLATE_DEFINITIONS[template_id]
    if not content.strip():
        raise HTTPException(status_code=400, detail="Prompt template must not be empty.")
    stage_marker = f"PROMPT_STAGE: {definition['stage']}"
    if stage_marker not in content:
        raise HTTPException(status_code=400, detail=f"Prompt template must include '{stage_marker}'.")
    missing = [
        "{{" + placeholder + "}}"
        for placeholder in definition["required_placeholders"]
        if "{{" + placeholder + "}}" not in content
    ]
    if missing:
        raise HTTPException(status_code=400, detail=f"Missing required placeholders: {', '.join(missing)}.")


async def read_text_upload(upload: UploadFile | None, label: str, *, require_content: bool = False) -> str:
    if upload is None or not upload.filename:
        raise HTTPException(status_code=400, detail=f"{label} is required")
    if Path(upload.filename).suffix.lower() != ".md":
        raise HTTPException(status_code=400, detail=f"{label} must be a .md file")
    try:
        content = (await upload.read()).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise HTTPException(status_code=400, detail=f"{label} must be UTF-8 text") from exc
    if require_content and not content.strip():
        raise HTTPException(status_code=400, detail=f"{label} must not be empty")
    return content


async def read_constraint_uploads(constraints: list[UploadFile] | None) -> dict[str, str]:
    constraint_map: dict[str, str] = {}
    for item in constraints or []:
        if not item.filename:
            continue
        suffix = Path(item.filename).suffix.lower()
        if suffix not in {".md", ".txt"}:
            raise HTTPException(status_code=400, detail=f"Constraint '{item.filename}' must be .md or .txt")
        try:
            constraint_map[item.filename] = (await item.read()).decode("utf-8")
        except UnicodeDecodeError as exc:
            raise HTTPException(status_code=400, detail=f"Constraint '{item.filename}' must be UTF-8 text") from exc
    return constraint_map


@asynccontextmanager
async def lifespan(fastapi_app: FastAPI):
    settings = load_settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    write_codex_config(settings)
    fastapi_app.state.settings = settings
    fastapi_app.state.auth_config = AuthConfig(
        admin_password=settings.admin_password,
        session_secret=settings.session_secret,
    )
    fastapi_app.state.job_manager = JobManager(settings.data_dir, codex_bin=settings.codex_bin)
    yield


app = FastAPI(title="Docker Web Codex Plan Runner", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.post("/api/login")
async def login(payload: LoginRequest, response: Response) -> dict:
    config: AuthConfig = app.state.auth_config
    if payload.password != config.admin_password:
        raise HTTPException(status_code=401, detail="Invalid password")
    set_session_cookie(response, config)
    return {"ok": True}


@app.post("/api/logout")
async def logout(response: Response) -> dict:
    clear_session_cookie(response)
    return {"ok": True}


@app.get("/api/session")
async def session(_: None = Depends(require_session)) -> dict:
    settings = app.state.settings
    return {
        "authenticated": True,
        "gateway_configured": settings.gateway_configured,
        "model": settings.codex_model,
        "base_url": settings.gateway_base_url,
    }


@app.get("/api/prompt-templates")
async def prompt_templates(_: None = Depends(require_session)) -> dict:
    return {
        "templates": [
            prompt_template_payload(template_id)
            for template_id in PROMPT_TEMPLATE_DEFINITIONS
        ]
    }


@app.get("/api/prompt-templates/{template_id}")
async def prompt_template(template_id: str, _: None = Depends(require_session)) -> dict:
    return {"template": prompt_template_payload(template_id)}


@app.put("/api/prompt-templates/{template_id}")
async def update_prompt_template(
    template_id: str,
    payload: PromptTemplateUpdate,
    _: None = Depends(require_session),
) -> dict:
    path = prompt_template_path(template_id)
    validate_prompt_template_content(template_id, payload.content)
    path.write_text(payload.content, encoding="utf-8")
    return {"ok": True, "template": prompt_template_payload(template_id)}


@app.post("/api/upload")
async def upload(
    project_zip: UploadFile = File(...),
    project_goal: str = Form(...),
    constraints: list[UploadFile] | None = File(None),
    _: None = Depends(require_session),
) -> dict:
    manager: JobManager = app.state.job_manager
    if manager.current_job and manager.current_job.state in {"planning", "revising", "running", "stopping"}:
        raise HTTPException(status_code=409, detail="A job is already active")

    constraint_map = await read_constraint_uploads(constraints)

    try:
        job = manager.create_job_from_zip(
            await project_zip.read(),
            project_zip.filename or "project.zip",
            project_goal,
            constraint_map,
        )
    except PlanError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "job": job.to_dict()}


@app.post("/api/upload-documents")
async def upload_documents(
    plan: UploadFile | None = File(None),
    handoff: UploadFile | None = File(None),
    test_report: UploadFile | None = File(None),
    project_zip: UploadFile | None = File(None),
    project_goal: str = Form(""),
    constraints: list[UploadFile] | None = File(None),
    _: None = Depends(require_session),
) -> dict:
    manager: JobManager = app.state.job_manager
    if manager.current_job and manager.current_job.state in {"planning", "revising", "running", "stopping"}:
        raise HTTPException(status_code=409, detail="A job is already active")

    plan_text = await read_text_upload(plan, "PLAN.md")
    handoff_text = await read_text_upload(handoff, "HANDOFF.md", require_content=True)
    test_report_text = await read_text_upload(test_report, "TEST_REPORT.md", require_content=True)
    constraint_map = await read_constraint_uploads(constraints)

    project_zip_bytes: bytes | None = None
    project_zip_filename = "project.zip"
    if project_zip is not None and project_zip.filename:
        project_zip_bytes = await project_zip.read()
        project_zip_filename = project_zip.filename

    try:
        job = manager.create_job_from_documents(
            plan_text=plan_text,
            handoff_text=handoff_text,
            test_report_text=test_report_text,
            constraints=constraint_map,
            project_zip=project_zip_bytes,
            project_zip_filename=project_zip_filename,
            project_goal=project_goal,
        )
    except PlanError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "job": job.to_dict()}


@app.post("/api/job/plan")
async def plan_job(_: None = Depends(require_session)) -> dict:
    manager: JobManager = app.state.job_manager
    try:
        job = manager.start_planning_job()
    except PlanError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "job": job.to_dict()}


@app.post("/api/job/revise-plan")
async def revise_plan(payload: RevisionRequest, _: None = Depends(require_session)) -> dict:
    manager: JobManager = app.state.job_manager
    try:
        job = manager.revise_current_plan(payload.feedback)
    except PlanError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "job": job.to_dict()}


@app.post("/api/job/start")
async def start_job(_: None = Depends(require_session)) -> dict:
    manager: JobManager = app.state.job_manager
    try:
        job = manager.start_current_job()
    except PlanError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "job": job.to_dict()}


@app.post("/api/job/stop")
async def stop_job(_: None = Depends(require_session)) -> dict:
    manager: JobManager = app.state.job_manager
    await manager.stop_current_job()
    return {"ok": True}


@app.get("/api/job/status")
async def job_status(_: None = Depends(require_session)) -> dict:
    manager: JobManager = app.state.job_manager
    return manager.status()


@app.get("/api/job/logs")
async def job_logs(_: None = Depends(require_session)) -> dict:
    manager: JobManager = app.state.job_manager
    return {"logs": manager.logs()}


@app.get("/api/documents/{document_name}")
async def document(document_name: str, _: None = Depends(require_session)) -> dict:
    manager: JobManager = app.state.job_manager
    try:
        return manager.document(document_name)
    except PlanError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/files")
async def files(_: None = Depends(require_session)) -> dict:
    manager: JobManager = app.state.job_manager
    return {"files": manager.file_tree()}


@app.get("/api/files/download")
async def download(_: None = Depends(require_session)) -> FileResponse:
    manager: JobManager = app.state.job_manager
    try:
        zip_path = manager.build_workspace_zip()
    except PlanError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return FileResponse(zip_path, filename=zip_path.name, media_type="application/zip")


@app.get("/api/history")
async def history(_: None = Depends(require_session)) -> dict:
    manager: JobManager = app.state.job_manager
    return {"jobs": manager.history()}


@app.get("/api/history/{job_id}")
async def history_detail(job_id: str, _: None = Depends(require_session)) -> dict:
    manager: JobManager = app.state.job_manager
    try:
        return {"job": manager.history_detail(job_id)}
    except PlanError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/history/{job_id}/plan")
async def history_plan(
    job_id: str,
    version: str = Query("current", pattern="^(uploaded|current)$"),
    _: None = Depends(require_session),
) -> dict:
    manager: JobManager = app.state.job_manager
    try:
        return manager.history_plan(job_id, version)
    except PlanError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/history/{job_id}/documents/{document_name}")
async def history_document(job_id: str, document_name: str, _: None = Depends(require_session)) -> dict:
    manager: JobManager = app.state.job_manager
    try:
        return manager.history_document(job_id, document_name)
    except PlanError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/history/{job_id}/logs")
async def history_logs(job_id: str, _: None = Depends(require_session)) -> dict:
    manager: JobManager = app.state.job_manager
    try:
        return {"logs": manager.history_logs(job_id)}
    except PlanError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/history/{job_id}/files")
async def history_files(job_id: str, _: None = Depends(require_session)) -> dict:
    manager: JobManager = app.state.job_manager
    try:
        return {"files": manager.history_file_tree(job_id)}
    except PlanError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/history/{job_id}/download")
async def history_download(job_id: str, _: None = Depends(require_session)) -> FileResponse:
    manager: JobManager = app.state.job_manager
    try:
        zip_path = manager.build_history_workspace_zip(job_id)
    except PlanError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return FileResponse(zip_path, filename=zip_path.name, media_type="application/zip")
