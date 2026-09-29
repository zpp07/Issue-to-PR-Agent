"""Offline end-to-end HTTP workflow using a deterministic model and sandbox."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import time
from types import SimpleNamespace

import service_http
from core.audit import TaskStore
from core.execution import ExecutionManager
from core.workflow import IssueToPRWorkflow
from issue_to_pr import IssueToPRService


def _tool_response(name: str, arguments: dict, call_id: str):
    call = SimpleNamespace(
        id=call_id,
        function=SimpleNamespace(name=name, arguments=json.dumps(arguments)),
    )
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=None, tool_calls=[call]))],
        usage=None,
        _request_id=call_id,
    )


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True,
        text=True, encoding="utf-8",
    ).stdout.strip()


def _post(path: str, body: dict):
    handler = SimpleNamespace(path=path, _body=lambda: body,
                              _reply=lambda status, payload: (status, payload))
    return service_http.Handler.do_POST(handler)


def _get(path: str):
    handler = SimpleNamespace(path=path, _reply=lambda status, payload: (status, payload))
    return service_http.Handler.do_GET(handler)


def test_ui_http_flow_reaches_ready_to_publish_with_fake_client(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    (repo / "buggy.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
    (repo / "test_buggy.py").write_text(
        "from buggy import add\n\ndef test_add():\n    assert add(2, 3) == 5\n", encoding="utf-8",
    )
    _git(repo, "add", "buggy.py", "test_buggy.py")
    _git(repo, "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
         "commit", "-m", "baseline")

    store = TaskStore(tmp_path / "tasks.sqlite3")
    workflow = IssueToPRWorkflow(store, tmp_path / "workspaces")
    responses = [
        _tool_response("plan_finish", {
            "goal": "Make add return a+b", "affected_files": ["buggy.py"],
            "steps": ["edit buggy.py", "run public test"], "risks": [],
            "allowed_commands": [["python", "-m", "pytest", "-q"]],
        }, "plan"),
        _tool_response("write_file", {
            "path": "buggy.py", "content": "def add(a, b):\n    return a + b\n",
        }, "edit"),
        _tool_response("finish", {"result": "changed subtraction to addition"}, "finish"),
        _tool_response("review_finish", {"passed": True, "issues": []}, "review"),
    ]
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(
        create=lambda **kwargs: responses.pop(0),
    )))

    class FakeSandbox:
        def command_runner(self, workspace):
            def unexpected_command(*args, **kwargs):
                raise AssertionError("Coder did not request a shell command")

            return unexpected_command

        def run(self, workspace, argv, **kwargs):
            assert Path(workspace).is_dir()
            assert argv == ["python", "-m", "pytest", "test_buggy.py", "-q"]
            return SimpleNamespace(exit_code=0, output="1 passed (fake sandbox)")

    def service():
        return IssueToPRService(workflow, client, sandbox=FakeSandbox())

    monkeypatch.setattr(service_http, "store", store)
    monkeypatch.setattr(service_http, "workflow", workflow)
    monkeypatch.setattr(service_http, "service", service)
    monkeypatch.setattr(service_http, "execution_manager", ExecutionManager(store, service))
    monkeypatch.setattr(service_http, "FAKE_PUBLISHER_MODE", False)
    monkeypatch.setattr(service_http.GitHubAPI, "from_env", lambda: (_ for _ in ()).throw(
        AssertionError("No GitHub access allowed in the offline test")))

    status, created = _post("/tasks", {"repo_path": str(repo), "issue": "Fix add(a, b)"})
    assert status == 201
    task_id = created["task_id"]
    assert store.task(task_id)["status"] == "awaiting_plan_approval"
    assert len(responses) == 3
    status, plan = _get(f"/tasks/{task_id}/artifacts/plan")
    assert status == 200
    assert plan["approval_subject_sha256"] == created["plan_sha256"]
    assert plan["approval_subject_kind"] == "plan"

    status, approved = _post(f"/tasks/{task_id}/approvals/plan", {
        "subject_hash": plan["approval_subject_sha256"],
        "actor": "offline-test", "decision": "approved",
    })
    assert status == 200 and approved["status"] == "ready_to_execute"

    status, started = _post(f"/tasks/{task_id}/execute", {
        "target_file": "buggy.py", "max_rounds": 1,
    })
    assert status == 202 and started["status"] == "executing"
    assert started["events_url"] == f"/tasks/{task_id}/events?after=0"
    seen, cursor = [], 0
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        page_status, page = _get(f"/tasks/{task_id}/events?after={cursor}&limit=2")
        assert page_status == 200
        assert all(event["sequence"] > cursor for event in page["events"])
        seen.extend(page["events"])
        cursor = page["next_after"]
        if page["execution_done"]:
            break
        time.sleep(0.01)
    assert page["execution_done"] is True
    assert page["status"] == "awaiting_diff_approval"
    assert [event["sequence"] for event in seen] == sorted({event["sequence"] for event in seen})
    event_types = [event["event_type"] for event in seen]
    assert "execution_started" in event_types
    assert "execution_finished" in event_types
    steps = [json.loads(event["payload"]) for event in seen if event["event_type"] == "agent_step"]
    assert [(step["phase"], step["action"]) for step in steps
            if step["action"] != "agent_exit"] == [
        ("coder", "write_file"), ("coder", "finish"), ("reviewer", "review_finish"),
    ]
    assert responses == []

    status, diff = _get(f"/tasks/{task_id}/artifacts/diff")
    assert status == 200 and "+    return a + b" in diff["content"]
    review_hash = diff["approval_subject_sha256"]
    assert review_hash and review_hash != diff["artifact_sha256"]
    assert diff["approval_subject_kind"] == "diff_review"
    status, review = _get(f"/tasks/{task_id}/artifacts/diff_review")
    assert status == 200 and review["artifact_sha256"] == review_hash
    review_content = json.loads(review["content"])
    assert review_content["diff_sha256"] == diff["artifact_sha256"]

    status, approved = _post(f"/tasks/{task_id}/approvals/diff", {
        "subject_hash": review_hash, "actor": "offline-test", "decision": "approved",
    })
    assert status == 200 and approved["status"] == "approved_for_pr"

    status, frozen = _post(f"/tasks/{task_id}/draft-pr", {
        "repository": "example/repo", "base_branch": "main",
        "head_branch": "agent/fix-add", "title": "fix: correct add result",
    })
    assert status == 200 and frozen["request"]["draft"] is True
    assert frozen["request"]["diff_review_sha256"] == review_hash
    assert frozen["request"]["diff_sha256"] == diff["artifact_sha256"]
    assert store.task(task_id)["status"] == "awaiting_publish_approval"
    status, request = _get(f"/tasks/{task_id}/artifacts/draft_pr_request")
    assert status == 200
    assert request["approval_subject_sha256"] == frozen["request_sha256"]
    assert request["approval_subject_kind"] == "draft_pr_request"

    status, approved = _post(f"/tasks/{task_id}/approvals/publish", {
        "subject_hash": request["approval_subject_sha256"],
        "actor": "offline-test", "decision": "approved",
    })
    assert status == 200 and approved["status"] == "ready_to_publish"
    status, listing = _get("/tasks")
    assert status == 200
    assert any(row["id"] == task_id and row["status"] == "ready_to_publish"
               for row in listing["tasks"])
    status, artifacts = _get(f"/tasks/{task_id}/artifacts")
    assert status == 200
    assert "draft_pr" not in {item["kind"] for item in artifacts["artifacts"]}
    assert _git(Path(store.task(task_id)["workspace_path"]), "status", "--short") == "M buggy.py"
