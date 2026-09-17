"""Optional FastAPI façade for the local single-user Issue-to-PR workflow.

Run after installing project dependencies:
    uvicorn service_api:app --reload
"""
from __future__ import annotations

import os
from pathlib import Path

from core.audit import TaskStore
from core.client import make_client
from core.sandbox import DockerSandbox
from core.workflow import IssueToPRWorkflow

from issue_to_pr import IssueToPRService, PlanningUnavailable

try:
    from fastapi import FastAPI, HTTPException
    from pydantic import BaseModel
except ImportError as exc:  # makes core/test use possible without web extras
    raise RuntimeError("请先安装 web 依赖：pip install -e '.[web]' ") from exc

ROOT = Path(__file__).parent
configured_data_dir = os.environ.get("AGENT_DATA_DIR")
DATA_DIR = Path(configured_data_dir).resolve() if configured_data_dir else (ROOT / ".local").resolve()
DATA_DIR.mkdir(parents=True, exist_ok=True)
store = TaskStore(DATA_DIR / "agent_tasks.sqlite3")
workflow = IssueToPRWorkflow(store, DATA_DIR / "workspaces")


class CreateTask(BaseModel):
    repo_path: str
    issue: str


class ApprovalRequest(BaseModel):
    subject_hash: str
    decision: str
    actor: str = "local-user"
    reason: str | None = None


class ExecuteRequest(BaseModel):
    target_file: str
    max_rounds: int = 3


class SourcedFactRequest(BaseModel):
    statement: str
    source_path: str
    evidence: str


app = FastAPI(title="Local Issue-to-PR Agent", version="0.5.0")


def service() -> IssueToPRService:
    sandbox = DockerSandbox(image=os.environ.get("AGENT_SANDBOX_IMAGE", "issue-to-pr-runner:phase3"))
    return IssueToPRService(
        workflow, make_client(), os.environ.get("AGENT_MODEL", "deepseek-chat"), sandbox=sandbox,
    )


@app.post("/tasks")
def create_task(request: CreateTask):
    try:
        context, plan, plan_hash = service().create_and_plan(request.repo_path, request.issue)
        return {"task_id": context.task_id, "workspace": str(context.workspace), "plan": plan, "plan_sha256": plan_hash}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/tasks/{task_id}/plan")
def retry_plan(task_id: str):
    try:
        plan, plan_hash = service().plan_existing(task_id)
        return {"task_id": task_id, "plan": plan, "plan_sha256": plan_hash}
    except PlanningUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except (KeyError, ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/tasks/{task_id}/approvals/plan")
def approve_plan(task_id: str, request: ApprovalRequest):
    try:
        workflow.approve_plan(task_id, request.subject_hash, request.actor, request.decision, request.reason)
        return workflow.store.task(task_id)
    except (KeyError, ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/tasks/{task_id}/execute")
def execute(task_id: str, request: ExecuteRequest):
    try:
        return service().execute_after_plan_approval(task_id, request.target_file, request.max_rounds)
    except (KeyError, ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/tasks/{task_id}/approvals/diff")
def approve_diff(task_id: str, request: ApprovalRequest):
    try:
        workflow.approve_diff(task_id, request.subject_hash, request.actor, request.decision, request.reason)
        return workflow.store.task(task_id)
    except (KeyError, ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.get("/tasks/{task_id}")
def get_task(task_id: str):
    try:
        return {"task": workflow.store.task(task_id), "events": workflow.store.events(task_id)}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/tasks/{task_id}/memories")
def get_memories(task_id: str, kind: str | None = None, limit: int = 100):
    try:
        kinds = [kind] if kind else None
        return {"memories": workflow.memory.list(task_id, kinds=kinds, limit=limit)}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/tasks/{task_id}/memories/facts")
def remember_fact(task_id: str, request: SourcedFactRequest):
    try:
        return workflow.memory.remember_sourced_fact(
            task_id, request.statement, request.source_path, request.evidence,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
