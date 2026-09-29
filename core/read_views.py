"""Public, read-only views for the local task UI.

Only explicitly listed workflow artifacts can cross the HTTP boundary.
The stored artifact hash may cover canonical JSON rather than the bytes of
the pretty-printed content, so both hashes are named independently.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from core.audit import TaskStore


PUBLIC_ARTIFACT_KINDS = (
    "plan", "diff", "test_report", "diff_review", "draft_pr_request", "draft_pr",
)


def task_page(store: TaskStore, limit: int = 50, offset: int = 0) -> dict[str, Any]:
    rows = store.list_tasks(limit, offset)
    tasks = []
    for row in rows:
        first_line = next((line.strip() for line in row["issue"].splitlines() if line.strip()), "")
        summary = " ".join(first_line.split())
        if len(summary) > 160:
            summary = summary[:159] + "…"
        tasks.append({
            "id": row["id"], "status": row["status"], "issue_summary": summary,
            "created_at": row["created_at"], "updated_at": row["updated_at"],
        })
    return {"tasks": tasks, "limit": limit, "offset": offset}


def _latest_if_present(store: TaskStore, task_id: str, kind: str) -> dict[str, Any] | None:
    try:
        return store.latest_artifact(task_id, kind)
    except KeyError:
        return None


def _approval_reference(store: TaskStore, task_id: str, kind: str,
                        artifact: dict[str, Any]) -> tuple[str | None, str | None, str | None]:
    if kind in {"plan", "diff_review", "draft_pr_request"}:
        approval_kind = {"plan": "plan", "diff_review": "diff", "draft_pr_request": "publish"}[kind]
        return approval_kind, kind, artifact["sha256"]
    if kind not in {"diff", "test_report"}:
        return None, None, None
    review = _latest_if_present(store, task_id, "diff_review")
    diff = _latest_if_present(store, task_id, "diff")
    test_report = _latest_if_present(store, task_id, "test_report")
    if review is None or diff is None or test_report is None:
        return None, None, None
    try:
        report = json.loads(review["content"])
    except (json.JSONDecodeError, TypeError):
        return None, None, None
    if not isinstance(report, dict):
        return None, None, None
    if (report.get("diff_sha256") != diff["sha256"]
            or report.get("test_report_sha256") != test_report["sha256"]
            or artifact["sha256"] != (diff if kind == "diff" else test_report)["sha256"]):
        return None, None, None
    return "diff", "diff_review", review["sha256"]


def _public_artifact(store: TaskStore, task_id: str, kind: str,
                     artifact: dict[str, Any], *, include_content: bool) -> dict[str, Any]:
    content = artifact["content"]
    encoded = content.encode("utf-8")
    approval_kind, subject_kind, subject_sha = _approval_reference(store, task_id, kind, artifact)
    result = {
        "kind": kind,
        "artifact_sha256": artifact["sha256"],
        "content_sha256": hashlib.sha256(encoded).hexdigest(),
        "size_bytes": len(encoded),
        "approval_kind": approval_kind,
        "approval_subject_kind": subject_kind,
        "approval_subject_sha256": subject_sha,
        "created_at": artifact["created_at"],
    }
    if include_content:
        result["content"] = content
    return result


def artifact_list(store: TaskStore, task_id: str) -> dict[str, Any]:
    store.task(task_id)
    artifacts = []
    for kind in PUBLIC_ARTIFACT_KINDS:
        artifact = _latest_if_present(store, task_id, kind)
        if artifact is not None:
            artifacts.append(_public_artifact(store, task_id, kind, artifact, include_content=False))
    return {"task_id": task_id, "artifacts": artifacts}


def artifact_content(store: TaskStore, task_id: str, kind: str) -> dict[str, Any]:
    if kind not in PUBLIC_ARTIFACT_KINDS:
        raise KeyError("artifact 不存在")
    store.task(task_id)
    artifact = store.latest_artifact(task_id, kind)
    return _public_artifact(store, task_id, kind, artifact, include_content=True)
