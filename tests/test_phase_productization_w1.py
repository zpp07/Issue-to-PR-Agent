"""Offline checks for W1 progress callbacks and their HTTP-safe projection."""

import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

import issue_to_pr
from coder_reviewer import multi_agent_review
from core.agent import run_agent
from core.audit import TaskStore
from core.client import LLMCallError
from core.tools import ToolResult, build_tool_registry
from core.workflow import IssueToPRWorkflow
from issue_to_pr import IssueToPRService


def _tool_response(name, arguments, call_id):
    call = SimpleNamespace(
        id=call_id,
        function=SimpleNamespace(name=name, arguments=json.dumps(arguments)),
    )
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=None, tool_calls=[call]))],
        usage=None,
        _request_id=call_id,
    )


def _client(responses):
    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(
        create=lambda **_: responses.pop(0)
    )))


def _git(repo, *args):
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True,
        text=True, encoding="utf-8",
    ).stdout.strip()


def test_run_agent_callback_sees_completed_records_without_trace():
    responses = [
        _tool_response("write_file", {"path": "buggy.py", "content": "private source"}, "edit"),
        _tool_response("finish", {"result": "done"}, "finish"),
    ]
    events = []
    result, _ = run_agent(
        _client(responses), "system", build_tool_registry("write_file", "finish"),
        "task", max_steps=2,
        execute_tool=lambda *_: ToolResult("updated", "ok", {"mutation_applied": True}),
        on_event=events.append,
    )
    assert result == "done"
    assert [event["action"] for event in events] == ["write_file", "finish", "agent_exit"]
    assert events[0]["status"] == "ok"
    assert events[0]["result_chars"] == len("updated")
    assert events[0]["mutation_applied"] is True

    # Omitting the new optional callback preserves the old trace/result path.
    trace = []
    result_without_callback, _ = run_agent(
        _client([
            _tool_response("write_file", {"path": "buggy.py", "content": "private source"}, "edit"),
            _tool_response("finish", {"result": "done"}, "finish"),
        ]),
        "system", build_tool_registry("write_file", "finish"), "task",
        max_steps=2, execute_tool=lambda *_: ToolResult(
            "updated", "ok", {"mutation_applied": True}
        ), trace=trace,
    )
    assert result_without_callback == result
    assert [(item["action"], item["status"]) for item in trace] == [
        (item["action"], item["status"]) for item in events if item["action"] != "agent_exit"
    ]


def test_run_agent_callback_includes_controller_terminal_event():
    events = []
    result, _ = run_agent(
        _client([
            _tool_response("replace_text", {"path": "buggy.py", "old": "-", "new": "+"}, "edit"),
            _tool_response("run_command", {"argv": ["python", "-m", "pytest", "-q"]}, "test"),
        ]),
        "system", build_tool_registry("replace_text", "run_command", "finish"),
        "task", max_steps=3,
        execute_tool=lambda name, _: (
            ToolResult("updated", "ok", {"mutation_applied": True})
            if name == "replace_text" else
            ToolResult("1 passed", "ok", {"command_exit_code": 0})
        ),
        auto_finish_after_verified_patch=True, on_event=events.append,
    )
    assert result["auto_finished"] is True
    assert [event["action"] for event in events] == [
        "replace_text", "run_command", "controller_finish", "agent_exit"
    ]
    assert events[1]["command_exit_code"] == 0


@pytest.mark.parametrize("exit_reason", [
    "llm_error", "max_tokens", "max_cost", "free_text", "no_progress",
    "verified_patch", "finish", "max_steps_unverified_patch", "max_steps_no_patch",
])
def test_every_agent_exit_reason_emits_one_terminal_event(exit_reason):
    def edit_or_test(name, _args):
        if name == "replace_text":
            return ToolResult("updated", "ok", {"mutation_applied": True})
        if name == "run_command":
            return ToolResult("1 passed", "ok", {"command_exit_code": 0})
        return ToolResult("source", "ok")

    kwargs = {"max_steps": 2, "execute_tool": edit_or_test}
    names = ("read_file", "replace_text", "run_command", "progress_decision", "finish")
    if exit_reason == "llm_error":
        def fail(**_):
            raise LLMCallError("offline failure", retryable=True)
        client = SimpleNamespace(chat=SimpleNamespace(
            completions=SimpleNamespace(create=fail)
        ))
    else:
        if exit_reason in {"max_tokens", "max_cost"}:
            response = _tool_response("finish", {"result": "unused"}, "budget")
            response.usage = SimpleNamespace(prompt_tokens=10, completion_tokens=1)
            kwargs["max_tokens" if exit_reason == "max_tokens" else "max_cost_rmb"] = (
                1 if exit_reason == "max_tokens" else 0.0
            )
            responses = [response]
        elif exit_reason == "free_text":
            responses = [SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(
                    content="done", tool_calls=None
                ))], usage=None, _request_id="text",
            )]
        elif exit_reason == "no_progress":
            kwargs.update(max_steps=3, no_progress_after=1)
            responses = [
                _tool_response("read_file", {"path": "buggy.py"}, "read"),
                _tool_response("progress_decision", {
                    "choice": "abandon", "reason": "cannot proceed"
                }, "decision"),
            ]
        elif exit_reason == "verified_patch":
            kwargs.update(max_steps=3, auto_finish_after_verified_patch=True)
            responses = [
                _tool_response("replace_text", {
                    "path": "buggy.py", "old": "-", "new": "+"
                }, "edit"),
                _tool_response("run_command", {
                    "argv": ["python", "-m", "pytest", "-q"]
                }, "test"),
            ]
        elif exit_reason == "finish":
            responses = [_tool_response("finish", {"result": "done"}, "finish")]
        elif exit_reason == "max_steps_unverified_patch":
            kwargs["max_steps"] = 1
            responses = [_tool_response("replace_text", {
                "path": "buggy.py", "old": "-", "new": "+"
            }, "edit")]
        else:
            kwargs["max_steps"] = 1
            responses = [_tool_response("read_file", {"path": "buggy.py"}, "read")]
        client = _client(responses)

    events, state = [], {}
    run_agent(
        client, "system", build_tool_registry(*names), "task",
        on_event=events.append, run_state=state, **kwargs,
    )
    assert state["exit_reason"] == exit_reason
    terminal = [event for event in events if event["action"] == "agent_exit"]
    assert terminal == [{"action": "agent_exit", "exit_reason": exit_reason}]
    assert events[-1] == terminal[0]


def test_multi_agent_callback_adds_phase_and_round(tmpdir):
    tmp_path = Path(str(tmpdir))
    target = tmp_path / "buggy.py"
    target.write_text("value = 1\n", encoding="utf-8")
    events = []
    passed, _, rounds, _ = multi_agent_review(
        _client([
            _tool_response("finish", {"result": "first"}, "coder-1"),
            _tool_response("review_finish", {"passed": False, "issues": ["retry"]}, "review-1"),
            _tool_response("finish", {"result": "second"}, "coder-2"),
            _tool_response("review_finish", {"passed": True, "issues": []}, "review-2"),
        ]),
        str(target), max_rounds=2, workspace=tmp_path, verbose=False,
        executor=lambda *_: "ok", on_event=events.append,
    )
    assert passed is True and rounds == 2
    assert [(event["phase"], event["round"], event["action"]) for event in events] == [
        ("coder", 1, "finish"), ("coder", 1, "agent_exit"),
        ("reviewer", 1, "review_finish"), ("reviewer", 1, "agent_exit"),
        ("coder", 2, "finish"), ("coder", 2, "agent_exit"),
        ("reviewer", 2, "review_finish"), ("reviewer", 2, "agent_exit"),
    ]


def test_service_persists_only_public_fields_during_fake_execution(tmpdir, monkeypatch):
    tmp_path = Path(str(tmpdir))
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    (repo / "buggy.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
    (repo / "test_buggy.py").write_text(
        "from buggy import add\n\ndef test_add():\n    assert add(2, 3) == 5\n",
        encoding="utf-8",
    )
    _git(repo, "add", "buggy.py", "test_buggy.py")
    _git(repo, "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
         "commit", "-m", "baseline")
    store = TaskStore(tmp_path / "tasks.sqlite3")
    workflow = IssueToPRWorkflow(store, tmp_path / "workspaces")
    context = workflow.create_task(repo, "fix add")
    plan_hash = workflow.submit_plan(context.task_id, {
        "goal": "fix add", "affected_files": ["buggy.py"], "steps": ["edit", "test"],
        "risks": [], "allowed_commands": [["python", "-m", "pytest", "-q"]],
    })
    workflow.approve_plan(context.task_id, plan_hash, "tester", "approved")

    secret = "TOP-SECRET-SENTINEL"
    service = IssueToPRService(workflow, _client([
        _tool_response("write_file", {
            "path": "buggy.py", "content": f"def add(a, b):\n    return a + b\n# {secret}\n",
        }, "edit"),
        _tool_response("finish", {"result": "fixed"}, "finish"),
        _tool_response("review_finish", {"passed": True, "issues": []}, "review"),
    ]))
    # Exercise the existing HTTP route logic with a fake client and isolated DB.
    monkeypatch.setenv("AGENT_DATA_DIR", str(tmp_path / "http-data"))
    import service_http
    monkeypatch.setattr(service_http, "store", store)
    monkeypatch.setattr(service_http, "workflow", workflow)
    monkeypatch.setattr(service_http, "service", lambda: service)
    post = SimpleNamespace(
        path=f"/tasks/{context.task_id}/execute",
        _body=lambda: {"target_file": "buggy.py", "max_rounds": 1},
        _reply=lambda status, payload: (status, payload),
    )
    status, result = service_http.Handler.do_POST(post)
    assert status == 200
    assert "review_sha256" in result
    assert store.task(context.task_id)["status"] == "awaiting_diff_approval"
    get = SimpleNamespace(
        path=f"/tasks/{context.task_id}",
        _reply=lambda status, payload: (status, payload),
    )
    get_status, task_view = service_http.Handler.do_GET(get)
    assert get_status == 200
    progress = [event for event in task_view["events"]
                if event["event_type"] == "agent_step"]
    payloads = [json.loads(event["payload"]) for event in progress]
    assert [(event["phase"], event["action"]) for event in payloads
            if event["action"] != "agent_exit"] == [
        ("coder", "write_file"), ("coder", "finish"), ("reviewer", "review_finish")
    ]
    assert [event["exit_reason"] for event in payloads
            if event["action"] == "agent_exit"] == ["finish", "finish"]
    assert payloads[0]["path"] == "buggy.py"
    assert payloads[0]["status"] == "ok"
    assert secret not in json.dumps(payloads)
    assert not ({"args", "content", "result_preview", "error", "request_id"}
                & set().union(*(set(payload) for payload in payloads)))


def test_progress_limit_marks_truncation_without_losing_status_events(tmpdir, monkeypatch):
    tmp_path = Path(str(tmpdir))
    monkeypatch.setattr(issue_to_pr, "MAX_PROGRESS_EVENTS_PER_TASK", 2)
    store = TaskStore(tmp_path / "tasks.sqlite3")
    task_id = store.create_task("issue", "repo", "workspace", "base")
    workflow = IssueToPRWorkflow(store, tmp_path / "workspaces")
    callback = IssueToPRService(workflow, client=None)._execution_event_callback(task_id)
    for step in range(4):
        callback({"phase": "coder", "round": 1, "step": step,
                  "action": "read_file", "status": "ok", "args": {"path": "buggy.py"}})
    store.set_status(task_id, "validation_failed")
    IssueToPRService(workflow, client=None)._execution_event_callback(task_id)(
        {"phase": "coder", "round": 2, "step": 0, "action": "read_file", "status": "ok"}
    )
    events = store.events(task_id)
    assert sum(event["event_type"] == "agent_step" for event in events) == 2
    markers = [event for event in events if event["event_type"] == "agent_trace_truncated"]
    assert len(markers) == 1
    assert json.loads(markers[0]["payload"])["further_progress_events_omitted"] is True
    assert any(event["event_type"] == "status_changed" for event in events)
