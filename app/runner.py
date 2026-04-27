from __future__ import annotations

import asyncio
import io
import json
import os
import re
import shlex
import shutil
import subprocess
import time
import uuid
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Mapping


CHECKBOX_RE = re.compile(r"^(?P<indent>\s*)-\s+\[(?P<mark>[ xX])\]\s+(?P<text>.+?)\s*$")
JOB_ID_RE = re.compile(r"^\d{8}-\d{6}-[A-Za-z0-9]{8}$")
ALLOWED_CONSTRAINT_SUFFIXES = {".md", ".txt"}
DOCUMENT_FILES = {
    "plan": "PLAN.md",
    "handoff": "HANDOFF.md",
    "test_report": "TEST_REPORT.md",
}
SKIPPED_ZIP_PARTS = {
    ".git",
    ".hg",
    ".svn",
    ".idea",
    ".vscode",
    "__pycache__",
    ".pytest_cache",
    "node_modules",
    "dist",
    "build",
    "target",
    "out",
    ".next",
    ".nuxt",
    "coverage",
}
PROMPT_TEMPLATE_DIR = Path(__file__).resolve().parent / "prompt_templates"
MAX_LOG_TAIL_CHARS = 24_000
MAX_FILE_TREE_ITEMS = 1_000


class PlanError(ValueError):
    pass


@dataclass(frozen=True)
class PlanTask:
    line_no: int
    text: str
    completed: bool
    raw: str


@dataclass
class Job:
    id: str
    job_dir: Path
    workspace_dir: Path
    logs_dir: Path
    project_goal: str
    state: str = "uploaded"
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    finished_at: float | None = None
    current_round: int = 0
    total_tasks: int = 0
    pending_tasks: int = 0
    completed_tasks: int = 0
    failure_reason: str | None = None
    last_message: str = ""
    active_process: asyncio.subprocess.Process | None = field(default=None, repr=False)
    stop_requested: bool = False
    background_task: asyncio.Task | None = field(default=None, repr=False)

    @property
    def plan_path(self) -> Path:
        return self.workspace_dir / DOCUMENT_FILES["plan"]

    @property
    def handoff_path(self) -> Path:
        return self.workspace_dir / DOCUMENT_FILES["handoff"]

    @property
    def test_report_path(self) -> Path:
        return self.workspace_dir / DOCUMENT_FILES["test_report"]

    @property
    def constraints_dir(self) -> Path:
        return self.workspace_dir / "constraints"

    @property
    def inputs_dir(self) -> Path:
        return self.job_dir / "inputs"

    @property
    def metadata_path(self) -> Path:
        return self.job_dir / "job.json"

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "state": self.state,
            "project_goal": self.project_goal,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "current_round": self.current_round,
            "total_tasks": self.total_tasks,
            "pending_tasks": self.pending_tasks,
            "completed_tasks": self.completed_tasks,
            "failure_reason": self.failure_reason,
            "last_message": self.last_message,
            "documents": {
                key: (self.workspace_dir / filename).exists()
                for key, filename in DOCUMENT_FILES.items()
            },
        }


def parse_job_created_at(job_id: str, fallback: float) -> float:
    try:
        return time.mktime(time.strptime(job_id[:15], "%Y%m%d-%H%M%S"))
    except ValueError:
        return fallback


def parse_plan_tasks(plan_text: str) -> list[PlanTask]:
    tasks: list[PlanTask] = []
    for index, line in enumerate(plan_text.splitlines(), start=1):
        match = CHECKBOX_RE.match(line)
        if not match:
            continue
        mark = match.group("mark")
        tasks.append(
            PlanTask(
                line_no=index,
                text=match.group("text"),
                completed=mark in {"x", "X"},
                raw=line,
            )
        )
    return tasks


def require_valid_plan(plan_text: str) -> list[PlanTask]:
    tasks = parse_plan_tasks(plan_text)
    if not tasks:
        raise PlanError("PLAN.md must contain Markdown checkbox tasks like '- [ ] task'.")
    return tasks


def select_pending_batch(tasks: list[PlanTask], batch_size: int) -> list[PlanTask]:
    return [task for task in tasks if not task.completed][:batch_size]


def summarize_tasks(tasks: list[PlanTask]) -> tuple[int, int, int]:
    total = len(tasks)
    pending = sum(1 for task in tasks if not task.completed)
    return total, pending, total - pending


def safe_constraint_name(filename: str, fallback_index: int) -> str:
    raw_name = Path(filename or f"constraint-{fallback_index}.md").name
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", raw_name).strip("._")
    if not cleaned:
        cleaned = f"constraint-{fallback_index}.md"
    suffix = Path(cleaned).suffix.lower()
    if suffix not in ALLOWED_CONSTRAINT_SUFFIXES:
        raise PlanError(f"Constraint file '{raw_name}' must be .md or .txt.")
    return cleaned


def load_constraints(constraints_dir: Path) -> list[tuple[str, str]]:
    if not constraints_dir.exists():
        return []

    constraints: list[tuple[str, str]] = []
    for path in sorted(constraints_dir.iterdir(), key=lambda item: item.name.lower()):
        if path.is_file() and path.suffix.lower() in ALLOWED_CONSTRAINT_SUFFIXES:
            constraints.append((path.name, path.read_text(encoding="utf-8", errors="replace")))
    return constraints


def constraints_block(constraints: list[tuple[str, str]]) -> str:
    if not constraints:
        return "No extra constraint files were uploaded."
    return "\n\n".join(f"### {name}\n```text\n{content}\n```" for name, content in constraints)


def render_template(template_name: str, replacements: Mapping[str, str]) -> str:
    template_path = PROMPT_TEMPLATE_DIR / template_name
    template = template_path.read_text(encoding="utf-8")
    for key, value in replacements.items():
        template = template.replace("{{" + key + "}}", value)
    return template


def selected_batch_block(batch: list[PlanTask]) -> str:
    return "\n".join(f"- line {task.line_no}: {task.text}" for task in batch)


def tail_text(path: Path, max_chars: int = MAX_LOG_TAIL_CHARS) -> str:
    if not path.exists():
        return ""
    text = path.read_text(encoding="utf-8", errors="replace")
    if len(text) <= max_chars:
        return text
    return text[-max_chars:]


def read_json_file(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def read_document(path: Path) -> str:
    if not path.exists():
        return f"{path.name} does not exist yet."
    return path.read_text(encoding="utf-8", errors="replace")


def validate_document_name(document_name: str) -> str:
    if document_name not in DOCUMENT_FILES:
        raise PlanError("Invalid document name.")
    return DOCUMENT_FILES[document_name]


def _safe_zip_parts(raw_name: str) -> list[str] | None:
    normalized = raw_name.replace("\\", "/")
    path = PurePosixPath(normalized)
    if path.is_absolute():
        raise PlanError(f"Unsafe ZIP path '{raw_name}'.")
    parts = [part for part in path.parts if part not in {"", "."}]
    if not parts or normalized.endswith("/"):
        return None
    if any(part == ".." for part in parts):
        raise PlanError(f"Unsafe ZIP path '{raw_name}'.")
    if parts[0] == "__MACOSX" or any(part in SKIPPED_ZIP_PARTS for part in parts):
        return None
    return parts


def extract_project_zip(zip_bytes: bytes, filename: str, workspace_dir: Path) -> int:
    if not filename.lower().endswith(".zip"):
        raise PlanError("Project source must be a .zip file.")

    workspace_root = workspace_dir.resolve()
    extracted = 0
    buffer = io.BytesIO(zip_bytes)
    try:
        with zipfile.ZipFile(buffer) as archive:
            bad_member = archive.testzip()
            if bad_member:
                raise PlanError(f"Project ZIP is corrupt near '{bad_member}'.")

            for info in archive.infolist():
                parts = _safe_zip_parts(info.filename)
                if parts is None:
                    continue

                target = workspace_dir.joinpath(*parts).resolve()
                try:
                    target.relative_to(workspace_root)
                except ValueError as exc:
                    raise PlanError(f"Unsafe ZIP path '{info.filename}'.") from exc

                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info) as source, target.open("wb") as destination:
                    shutil.copyfileobj(source, destination)
                extracted += 1
    except zipfile.BadZipFile as exc:
        raise PlanError("Project source must be a valid .zip file.") from exc
    finally:
        buffer.close()

    if extracted == 0:
        raise PlanError("Project ZIP did not contain extractable source files.")
    return extracted


async def stream_to_file(stream: asyncio.StreamReader | None, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if stream is None:
        path.touch()
        return

    with path.open("wb") as handle:
        while True:
            chunk = await stream.read(4096)
            if not chunk:
                break
            handle.write(chunk)
            handle.flush()


class JobManager:
    def __init__(self, data_dir: Path, codex_bin: str = "codex", batch_size: int = 10) -> None:
        self.data_dir = data_dir
        self.jobs_dir = data_dir / "jobs"
        self.codex_command = shlex.split(codex_bin)
        self.batch_size = batch_size
        self.current_job: Job | None = None
        self.jobs_dir.mkdir(parents=True, exist_ok=True)

    def create_job_from_zip(
        self,
        zip_bytes: bytes,
        filename: str,
        project_goal: str,
        constraints: Mapping[str, str],
    ) -> Job:
        if self.current_job and self.current_job.state in {"planning", "revising", "running", "stopping"}:
            raise PlanError("A job is already active.")

        cleaned_goal = project_goal.strip()
        if not cleaned_goal:
            raise PlanError("Project goal is required.")

        job_id = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]
        job_dir = self.jobs_dir / job_id
        workspace_dir = job_dir / "workspace"
        logs_dir = job_dir / "logs"
        constraints_dir = workspace_dir / "constraints"
        inputs_dir = job_dir / "inputs"
        input_constraints_dir = inputs_dir / "constraints"

        workspace_dir.mkdir(parents=True, exist_ok=False)
        logs_dir.mkdir(parents=True, exist_ok=True)
        constraints_dir.mkdir(parents=True, exist_ok=True)
        inputs_dir.mkdir(parents=True, exist_ok=True)
        input_constraints_dir.mkdir(parents=True, exist_ok=True)

        (inputs_dir / "project_goal.txt").write_text(cleaned_goal, encoding="utf-8")
        (inputs_dir / "source.zip").write_bytes(zip_bytes)
        extract_project_zip(zip_bytes, filename, workspace_dir)
        self.write_constraints(constraints_dir, input_constraints_dir, constraints)

        subprocess.run(["git", "init"], cwd=workspace_dir, text=True, capture_output=True, check=False)

        job = Job(
            id=job_id,
            job_dir=job_dir,
            workspace_dir=workspace_dir,
            logs_dir=logs_dir,
            project_goal=cleaned_goal,
        )
        self.current_job = job
        self.save_job_metadata(job)
        return job

    def create_job_from_content(self, plan_text: str, constraints: Mapping[str, str]) -> Job:
        tasks = require_valid_plan(plan_text)
        job_id = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]
        job_dir = self.jobs_dir / job_id
        workspace_dir = job_dir / "workspace"
        logs_dir = job_dir / "logs"
        constraints_dir = workspace_dir / "constraints"
        inputs_dir = job_dir / "inputs"
        input_constraints_dir = inputs_dir / "constraints"

        workspace_dir.mkdir(parents=True, exist_ok=False)
        logs_dir.mkdir(parents=True, exist_ok=True)
        constraints_dir.mkdir(parents=True, exist_ok=True)
        inputs_dir.mkdir(parents=True, exist_ok=True)
        input_constraints_dir.mkdir(parents=True, exist_ok=True)
        (workspace_dir / DOCUMENT_FILES["plan"]).write_text(plan_text, encoding="utf-8")
        (inputs_dir / DOCUMENT_FILES["plan"]).write_text(plan_text, encoding="utf-8")
        self.write_constraints(constraints_dir, input_constraints_dir, constraints)
        subprocess.run(["git", "init"], cwd=workspace_dir, text=True, capture_output=True, check=False)

        total, pending, completed = summarize_tasks(tasks)
        job = Job(
            id=job_id,
            job_dir=job_dir,
            workspace_dir=workspace_dir,
            logs_dir=logs_dir,
            project_goal="Legacy PLAN.md upload",
            state="awaiting_start",
            total_tasks=total,
            pending_tasks=pending,
            completed_tasks=completed,
        )
        self.current_job = job
        self.save_job_metadata(job)
        return job

    def write_constraints(
        self,
        constraints_dir: Path,
        input_constraints_dir: Path,
        constraints: Mapping[str, str],
    ) -> None:
        used_names: set[str] = set()
        for index, (filename, content) in enumerate(constraints.items(), start=1):
            safe_name = safe_constraint_name(filename, index)
            while safe_name.lower() in used_names:
                stem = Path(safe_name).stem
                suffix = Path(safe_name).suffix
                safe_name = f"{stem}-{index}{suffix}"
            used_names.add(safe_name.lower())
            (constraints_dir / safe_name).write_text(content, encoding="utf-8")
            (input_constraints_dir / safe_name).write_text(content, encoding="utf-8")

    def save_job_metadata(self, job: Job) -> None:
        job.metadata_path.write_text(json.dumps(job.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")

    def refresh_counts(self, job: Job) -> None:
        if not job.plan_path.exists():
            job.total_tasks = 0
            job.pending_tasks = 0
            job.completed_tasks = 0
            return
        tasks = require_valid_plan(job.plan_path.read_text(encoding="utf-8", errors="replace"))
        job.total_tasks, job.pending_tasks, job.completed_tasks = summarize_tasks(tasks)

    def start_planning_job(self) -> Job:
        job = self.require_current_job()
        if job.state in {"planning", "revising", "running", "stopping"}:
            raise PlanError("The current job is already active.")
        if job.state not in {"uploaded", "failed", "stopped"}:
            raise PlanError("Planning can only start after a project upload.")
        job.stop_requested = False
        job.background_task = asyncio.create_task(self.run_planning_job(job))
        self.save_job_metadata(job)
        return job

    def revise_current_plan(self, feedback: str) -> Job:
        job = self.require_current_job()
        if job.state in {"planning", "revising", "running", "stopping"}:
            raise PlanError("The current job is already active.")
        if job.state != "awaiting_start":
            raise PlanError("Generate PLAN.md before revising it.")
        if not feedback.strip():
            raise PlanError("Revision feedback is required.")
        job.stop_requested = False
        job.background_task = asyncio.create_task(self.run_revision_job(job, feedback.strip()))
        self.save_job_metadata(job)
        return job

    def start_current_job(self) -> Job:
        job = self.require_current_job()
        if job.state in {"planning", "revising", "running", "stopping"}:
            raise PlanError("The current job is already active.")
        if job.state not in {"awaiting_start", "stopped"}:
            raise PlanError("Generate and approve PLAN.md before starting execution.")
        self.refresh_counts(job)
        if job.pending_tasks == 0:
            job.state = "completed"
            job.finished_at = time.time()
            self.save_job_metadata(job)
            return job

        job.stop_requested = False
        job.background_task = asyncio.create_task(self.run_current_job(job))
        self.save_job_metadata(job)
        return job

    async def stop_current_job(self) -> None:
        job = self.current_job
        if not job or job.state not in {"planning", "revising", "running"}:
            return
        job.stop_requested = True
        job.state = "stopping"
        self.save_job_metadata(job)
        if job.active_process and job.active_process.returncode is None:
            job.active_process.terminate()
            try:
                await asyncio.wait_for(job.active_process.wait(), timeout=10)
            except asyncio.TimeoutError:
                job.active_process.kill()
                await job.active_process.wait()

    async def run_planning_job(self, job: Job) -> None:
        job.state = "planning"
        job.started_at = job.started_at or time.time()
        job.finished_at = None
        job.failure_reason = None
        self.save_job_metadata(job)

        try:
            await self.run_codex_process(job, self.build_planning_prompt(job), "planning")
            if job.stop_requested:
                self.mark_stopped(job)
                return
            self.refresh_counts(job)
            if job.total_tasks == 0:
                raise PlanError("Codex finished planning but PLAN.md has no checkbox tasks.")
            job.state = "awaiting_start"
            job.finished_at = time.time()
            self.save_job_metadata(job)
        except Exception as exc:
            self.mark_failed(job, exc)

    async def run_revision_job(self, job: Job, feedback: str) -> None:
        job.state = "revising"
        job.finished_at = None
        job.failure_reason = None
        self.save_job_metadata(job)

        try:
            await self.run_codex_process(job, self.build_revision_prompt(job, feedback), "revision")
            if job.stop_requested:
                self.mark_stopped(job)
                return
            self.refresh_counts(job)
            if job.total_tasks == 0:
                raise PlanError("Codex finished revising but PLAN.md has no checkbox tasks.")
            job.state = "awaiting_start"
            job.finished_at = time.time()
            self.save_job_metadata(job)
        except Exception as exc:
            self.mark_failed(job, exc)

    async def run_current_job(self, job: Job | None = None) -> None:
        job = job or self.current_job
        if not job:
            return
        job.state = "running"
        job.started_at = job.started_at or time.time()
        job.finished_at = None
        job.failure_reason = None
        self.save_job_metadata(job)

        try:
            while True:
                if job.stop_requested:
                    self.mark_stopped(job)
                    return

                if not job.plan_path.exists():
                    raise PlanError("PLAN.md disappeared.")

                plan_text = job.plan_path.read_text(encoding="utf-8", errors="replace")
                tasks = require_valid_plan(plan_text)
                total, pending_before, completed = summarize_tasks(tasks)
                job.total_tasks = total
                job.pending_tasks = pending_before
                job.completed_tasks = completed
                self.save_job_metadata(job)

                if pending_before == 0:
                    job.state = "completed"
                    job.finished_at = time.time()
                    self.save_job_metadata(job)
                    return

                batch = select_pending_batch(tasks, self.batch_size)
                if not batch:
                    raise PlanError("No pending checkbox tasks could be selected.")

                await self.run_codex_process(job, self.build_execution_prompt(job, plan_text, batch), "execution")

                if job.stop_requested:
                    self.mark_stopped(job)
                    return

                if not job.plan_path.exists():
                    raise PlanError("PLAN.md disappeared.")

                after_text = job.plan_path.read_text(encoding="utf-8", errors="replace")
                after_tasks = require_valid_plan(after_text)
                total_after, pending_after, completed_after = summarize_tasks(after_tasks)
                job.total_tasks = total_after
                job.pending_tasks = pending_after
                job.completed_tasks = completed_after
                self.save_job_metadata(job)

                if pending_after >= pending_before:
                    raise PlanError(
                        "Codex finished but the unchecked task count did not decrease. "
                        "Stopping to avoid an endless loop."
                    )
        except Exception as exc:
            self.mark_failed(job, exc)

    async def run_codex_process(self, job: Job, prompt: str, label: str) -> None:
        job.current_round += 1
        self.save_job_metadata(job)
        round_dir = job.logs_dir / f"round-{job.current_round:03d}-{label}"
        round_dir.mkdir(parents=True, exist_ok=True)

        prompt_path = round_dir / "prompt.md"
        stdout_path = round_dir / "stdout.jsonl"
        stderr_path = round_dir / "stderr.txt"
        final_path = round_dir / "final-message.md"
        prompt_path.write_text(prompt, encoding="utf-8")

        cmd = [
            *self.codex_command,
            "exec",
            "--dangerously-bypass-approvals-and-sandbox",
            "-C",
            str(job.workspace_dir),
            "--json",
            "-o",
            str(final_path),
            "-",
        ]
        env = os.environ.copy()
        process = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=job.workspace_dir,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        job.active_process = process

        stdout_task = asyncio.create_task(stream_to_file(process.stdout, stdout_path))
        stderr_task = asyncio.create_task(stream_to_file(process.stderr, stderr_path))
        if process.stdin is None:
            raise PlanError("Codex subprocess stdin is unavailable.")
        process.stdin.write(prompt.encode("utf-8"))
        await process.stdin.drain()
        process.stdin.close()
        await process.wait()
        await asyncio.gather(stdout_task, stderr_task)
        job.active_process = None

        final_message = tail_text(final_path)
        if final_message:
            job.last_message = final_message

        if process.returncode != 0 and not job.stop_requested:
            stderr_tail = tail_text(stderr_path, 4_000)
            raise PlanError(f"Codex exited with code {process.returncode}. {stderr_tail}".strip())

    def build_planning_prompt(self, job: Job) -> str:
        return render_template(
            "planning_prompt.md",
            {
                "PROJECT_GOAL": job.project_goal,
                "CONSTRAINTS": constraints_block(load_constraints(job.constraints_dir)),
                "WORKSPACE_FILES": self.workspace_snapshot(job.workspace_dir),
            },
        )

    def build_revision_prompt(self, job: Job, feedback: str) -> str:
        return render_template(
            "revision_prompt.md",
            {
                "PROJECT_GOAL": job.project_goal,
                "FEEDBACK": feedback,
                "PLAN_MD": read_document(job.plan_path),
                "HANDOFF_MD": read_document(job.handoff_path),
                "TEST_REPORT_MD": read_document(job.test_report_path),
                "CONSTRAINTS": constraints_block(load_constraints(job.constraints_dir)),
            },
        )

    def build_execution_prompt(self, job: Job, plan_text: str, batch: list[PlanTask]) -> str:
        return render_template(
            "continuation_prompt.md",
            {
                "PROJECT_GOAL": job.project_goal,
                "SELECTED_BATCH": selected_batch_block(batch),
                "PLAN_MD": plan_text,
                "HANDOFF_MD": read_document(job.handoff_path),
                "TEST_REPORT_MD": read_document(job.test_report_path),
                "CONSTRAINTS": constraints_block(load_constraints(job.constraints_dir)),
            },
        )

    def workspace_snapshot(self, workspace_dir: Path) -> str:
        files = self.file_tree_for_workspace(workspace_dir)
        if not files:
            return "Workspace is empty."
        return "\n".join(f"- {item['path']}" for item in files[:200])

    def require_current_job(self) -> Job:
        if not self.current_job:
            raise PlanError("No uploaded job is available.")
        return self.current_job

    def mark_stopped(self, job: Job) -> None:
        job.state = "stopped"
        job.finished_at = time.time()
        job.active_process = None
        self.save_job_metadata(job)

    def mark_failed(self, job: Job, exc: Exception) -> None:
        job.state = "failed"
        job.failure_reason = str(exc)
        job.finished_at = time.time()
        job.active_process = None
        self.save_job_metadata(job)

    def status(self) -> dict:
        if not self.current_job:
            return {"state": "idle", "job": None}
        try:
            if self.current_job.plan_path.exists():
                self.refresh_counts(self.current_job)
                self.save_job_metadata(self.current_job)
        except PlanError as exc:
            if self.current_job.state in {"planning", "revising", "running", "stopping"}:
                pass
            elif self.current_job.state not in {"failed", "completed", "stopped"}:
                self.mark_failed(self.current_job, exc)
        return {"state": self.current_job.state, "job": self.current_job.to_dict()}

    def document(self, document_name: str) -> dict:
        job = self.require_current_job()
        filename = validate_document_name(document_name)
        path = job.workspace_dir / filename
        return {
            "job_id": job.id,
            "document": document_name,
            "filename": filename,
            "available": path.exists(),
            "content": path.read_text(encoding="utf-8", errors="replace") if path.exists() else "",
        }

    def logs_for_dir(self, logs_dir: Path) -> list[dict]:
        if not logs_dir.exists():
            return []
        rounds = []
        for round_dir in sorted(logs_dir.glob("round-*")):
            if not round_dir.is_dir():
                continue
            rounds.append(
                {
                    "round": round_dir.name,
                    "prompt": tail_text(round_dir / "prompt.md", 8_000),
                    "stdout": tail_text(round_dir / "stdout.jsonl"),
                    "stderr": tail_text(round_dir / "stderr.txt"),
                    "final_message": tail_text(round_dir / "final-message.md"),
                }
            )
        return rounds

    def logs(self) -> list[dict]:
        job = self.current_job
        if not job:
            return []
        return self.logs_for_dir(job.logs_dir)

    def file_tree_for_workspace(self, workspace_dir: Path) -> list[dict]:
        if not workspace_dir.exists():
            return []
        items: list[dict] = []
        for path in sorted(workspace_dir.rglob("*")):
            relative_parts = path.relative_to(workspace_dir).parts
            if ".git" in relative_parts:
                continue
            relative = path.relative_to(workspace_dir).as_posix()
            items.append(
                {
                    "path": relative,
                    "type": "directory" if path.is_dir() else "file",
                    "size": path.stat().st_size if path.is_file() else None,
                }
            )
            if len(items) >= MAX_FILE_TREE_ITEMS:
                break
        return items

    def file_tree(self) -> list[dict]:
        job = self.current_job
        if not job:
            return []
        return self.file_tree_for_workspace(job.workspace_dir)

    def build_zip_for_workspace(self, job_id: str, workspace_dir: Path) -> Path:
        if not workspace_dir.exists():
            raise PlanError("No workspace is available.")
        downloads_dir = self.data_dir / "downloads"
        downloads_dir.mkdir(parents=True, exist_ok=True)
        zip_path = downloads_dir / f"{job_id}-workspace.zip"
        if zip_path.exists():
            zip_path.unlink()
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
            for path in workspace_dir.rglob("*"):
                relative_parts = path.relative_to(workspace_dir).parts
                if ".git" in relative_parts or path.is_dir():
                    continue
                archive.write(path, path.relative_to(workspace_dir).as_posix())
        return zip_path

    def build_workspace_zip(self) -> Path:
        job = self.current_job
        if not job or not job.workspace_dir.exists():
            raise PlanError("No workspace is available.")
        return self.build_zip_for_workspace(job.id, job.workspace_dir)

    def validate_history_job_id(self, job_id: str) -> Path:
        if not JOB_ID_RE.match(job_id):
            raise PlanError("Invalid history job id.")
        job_dir = self.jobs_dir / job_id
        try:
            job_dir.relative_to(self.jobs_dir)
        except ValueError as exc:
            raise PlanError("Invalid history job id.") from exc
        if not job_dir.is_dir():
            raise PlanError("History job not found.")
        return job_dir

    def history_record_for_dir(self, job_dir: Path) -> dict:
        job_id = job_dir.name
        workspace_dir = job_dir / "workspace"
        logs_dir = job_dir / "logs"
        inputs_dir = job_dir / "inputs"
        metadata = read_json_file(job_dir / "job.json")
        created_at = float(metadata.get("created_at") or parse_job_created_at(job_id, job_dir.stat().st_mtime))
        state = str(metadata.get("state") or "legacy")
        failure_reason = metadata.get("failure_reason")
        last_message = str(metadata.get("last_message") or "")
        project_goal = str(metadata.get("project_goal") or tail_text(inputs_dir / "project_goal.txt", 2_000))
        plan_path = workspace_dir / DOCUMENT_FILES["plan"]
        if not plan_path.exists():
            plan_path = workspace_dir / "plan.md"

        total = int(metadata.get("total_tasks") or 0)
        pending = int(metadata.get("pending_tasks") or 0)
        completed = int(metadata.get("completed_tasks") or 0)
        if plan_path.exists():
            try:
                total, pending, completed = summarize_tasks(
                    require_valid_plan(plan_path.read_text(encoding="utf-8", errors="replace"))
                )
            except PlanError:
                pass

        round_count = len([path for path in logs_dir.glob("round-*") if path.is_dir()]) if logs_dir.exists() else 0
        current_round = int(metadata.get("current_round") or round_count)

        if self.current_job and self.current_job.id == job_id:
            live = self.current_job.to_dict()
            state = live["state"]
            created_at = live["created_at"]
            failure_reason = live["failure_reason"]
            last_message = live["last_message"]
            current_round = live["current_round"]
            project_goal = live["project_goal"]

        documents = {
            key: (workspace_dir / filename).exists()
            for key, filename in DOCUMENT_FILES.items()
        }
        return {
            "id": job_id,
            "state": state,
            "project_goal": project_goal,
            "created_at": created_at,
            "started_at": metadata.get("started_at"),
            "finished_at": metadata.get("finished_at"),
            "current_round": current_round,
            "round_count": round_count,
            "total_tasks": total,
            "pending_tasks": pending,
            "completed_tasks": completed,
            "failure_reason": failure_reason,
            "last_message": last_message,
            "documents": documents,
            "uploaded_plan_available": False,
            "current_plan_available": documents["plan"] or (workspace_dir / "plan.md").exists(),
            "constraint_count": self.constraint_count_for_dir(inputs_dir, workspace_dir),
        }

    def constraint_count_for_dir(self, inputs_dir: Path, workspace_dir: Path) -> int:
        constraint_dir = inputs_dir / "constraints"
        if not constraint_dir.exists():
            constraint_dir = workspace_dir / "constraints"
        return len(
            [
                path
                for path in constraint_dir.iterdir()
                if path.is_file() and path.suffix.lower() in ALLOWED_CONSTRAINT_SUFFIXES
            ]
        ) if constraint_dir.exists() else 0

    def history(self) -> list[dict]:
        records = []
        for job_dir in self.jobs_dir.iterdir():
            if job_dir.is_dir() and JOB_ID_RE.match(job_dir.name):
                records.append(self.history_record_for_dir(job_dir))
        return sorted(records, key=lambda item: (item["created_at"], item["id"]), reverse=True)

    def history_detail(self, job_id: str) -> dict:
        return self.history_record_for_dir(self.validate_history_job_id(job_id))

    def history_document(self, job_id: str, document_name: str) -> dict:
        job_dir = self.validate_history_job_id(job_id)
        filename = validate_document_name(document_name)
        workspace_dir = job_dir / "workspace"
        path = workspace_dir / filename
        if document_name == "plan" and not path.exists():
            path = workspace_dir / "plan.md"
        return {
            "job_id": job_id,
            "document": document_name,
            "filename": path.name,
            "available": path.exists(),
            "content": path.read_text(encoding="utf-8", errors="replace") if path.exists() else "",
        }

    def history_plan(self, job_id: str, version: str) -> dict:
        document = self.history_document(job_id, "plan")
        return {
            "job_id": job_id,
            "requested_version": version,
            "version": "current",
            "fallback": version == "uploaded",
            "uploaded_plan_available": False,
            "content": document["content"],
        }

    def history_logs(self, job_id: str) -> list[dict]:
        job_dir = self.validate_history_job_id(job_id)
        return self.logs_for_dir(job_dir / "logs")

    def history_file_tree(self, job_id: str) -> list[dict]:
        job_dir = self.validate_history_job_id(job_id)
        return self.file_tree_for_workspace(job_dir / "workspace")

    def build_history_workspace_zip(self, job_id: str) -> Path:
        job_dir = self.validate_history_job_id(job_id)
        return self.build_zip_for_workspace(job_id, job_dir / "workspace")

    def clear_all_jobs(self) -> None:
        if self.current_job and self.current_job.state in {"planning", "revising", "running", "stopping"}:
            raise PlanError("Cannot clear jobs while a job is active.")
        if self.jobs_dir.exists():
            shutil.rmtree(self.jobs_dir)
        self.jobs_dir.mkdir(parents=True, exist_ok=True)
        self.current_job = None
