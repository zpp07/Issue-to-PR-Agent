"""Offline regression tests for planning on repositories needing several reads."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from core.audit import TaskStore
from core.workflow import IssueToPRWorkflow
from issue_to_pr import IssueToPRService, PlanningUnavailable


PLAN = {
    "goal": "Clarify the missing review label",
    "affected_files": ["web/app.js"],
    "steps": ["Read the Diff tab", "Update the label", "Run public tests"],
    "risks": ["Keep the approval gate unchanged"],
    "allowed_commands": [["python", "-m", "pytest", "-q"]],
}


def _response(name: str, arguments: dict, index: int):
    call = SimpleNamespace(
        id=f"call-{index}",
        function=SimpleNamespace(name=name, arguments=json.dumps(arguments)),
    )
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=None, tool_calls=[call]))],
        usage=None,
        _request_id=f"request-{index}",
    )


def _workflow(tmp_path: Path) -> tuple[IssueToPRWorkflow, str]:
    repo = tmp_path / "repo"
    (repo / "web").mkdir(parents=True)
    (repo / "web" / "app.js").write_text("const label = 'expired';\n", encoding="utf-8")

    def git(*args: str) -> None:
        subprocess.run(
            ["git", "-C", str(repo), *args], check=True,
            capture_output=True, text=True, encoding="utf-8",
        )

    git("init", "-b", "master")
    git("add", "web/app.js")
    git("-c", "user.name=Test", "-c", "user.email=test@example.invalid",
        "commit", "-m", "baseline")
    workflow = IssueToPRWorkflow(TaskStore(tmp_path / "tasks.sqlite3"), tmp_path / "workspaces")
    context = workflow.create_task(repo, "Clarify a missing review")
    return workflow, context.task_id


def test_planner_reserves_terminal_turns_for_plan_finish(tmp_path):
    workflow, task_id = _workflow(tmp_path)
    calls: list[dict] = []

    def create(**kwargs):
        calls.append(kwargs)
        names = {item["function"]["name"] for item in kwargs["tools"]}
        if "read_file" in names:
            return _response("read_file", {"path": "web/app.js"}, len(calls))
        return _response("plan_finish", PLAN, len(calls))

    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    plan, plan_hash = IssueToPRService(workflow, client).plan_existing(task_id)

    assert plan == PLAN and len(plan_hash) == 64
    assert len(calls) == 11  # Ten investigation turns, then the reserved terminal turn.
    assert [item["function"]["name"] for item in calls[-1]["tools"]] == ["plan_finish"]
    assert any("调查时间已结束" in message.get("content", "")
               for message in calls[-1]["messages"] if isinstance(message, dict))
    assert workflow.store.task(task_id)["status"] == "awaiting_plan_approval"


def test_planner_max_steps_is_reported_and_same_task_can_retry(tmp_path):
    workflow, task_id = _workflow(tmp_path)
    original_workspace = workflow.store.task(task_id)["workspace_path"]
    calls = 0

    def never_finishes(**kwargs):
        nonlocal calls
        calls += 1
        return _response("read_file", {"path": "web/app.js"}, calls)

    client = SimpleNamespace(chat=SimpleNamespace(
        completions=SimpleNamespace(create=never_finishes),
    ))
    service = IssueToPRService(workflow, client)
    with pytest.raises(PlanningUnavailable, match="max_steps_no_patch"):
        service.plan_existing(task_id)

    assert calls == 12
    assert workflow.store.task(task_id)["status"] == "planning_failed"
    events = workflow.store.events(task_id)
    failures = [json.loads(event["payload"]) for event in events
                if event["event_type"] == "planning_failed"]
    assert failures == [{"reason": "agent_exit", "exit_reason": "max_steps_no_patch",
                         "steps_used": 12}]

    service.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(
        create=lambda **_: _response("plan_finish", PLAN, 1),
    )))
    plan, _ = service.plan_existing(task_id)
    assert plan == PLAN
    assert workflow.store.task(task_id)["status"] == "awaiting_plan_approval"
    assert workflow.store.task(task_id)["workspace_path"] == original_workspace


def test_planner_invalid_finish_is_distinct_from_step_exhaustion(tmp_path):
    workflow, task_id = _workflow(tmp_path)
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(
        create=lambda **_: _response("plan_finish", {"goal": "incomplete"}, 1),
    )))
    with pytest.raises(PlanningUnavailable, match="Planner 提交的计划无效"):
        IssueToPRService(workflow, client).plan_existing(task_id)

    assert workflow.store.task(task_id)["status"] == "planning_failed"
    failures = [json.loads(event["payload"]) for event in workflow.store.events(task_id)
                if event["event_type"] == "planning_failed"]
    assert failures == [{"reason": "invalid_plan", "exit_reason": "finish", "steps_used": 1}]
