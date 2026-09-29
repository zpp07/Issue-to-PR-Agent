"""Offline W2 checks: durable cursor, one worker, failure and restart states."""

import json
from pathlib import Path
from threading import Barrier, Event, Thread
import time
from types import SimpleNamespace

import pytest

import issue_to_pr
from core.audit import TaskStore
from core.execution import ExecutionManager
from core.workflow import IssueToPRWorkflow
from issue_to_pr import IssueToPRService


def _ready_task(tmpdir):
    root = Path(str(tmpdir))
    store = TaskStore(root / "tasks.sqlite3")
    task_id = store.create_task("issue", "repo", "workspace", "base")
    store.set_status(task_id, "ready_to_execute")
    return store, task_id, root


def _collect_until_done(manager, task_id, limit=1):
    after, collected = 0, []
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        page = manager.event_page(task_id, after, limit)
        collected.extend(page["events"])
        assert page["next_after"] >= after
        after = page["next_after"]
        if page["execution_done"]:
            assert page["has_more"] is False
            return collected, page
        time.sleep(0.01)
    raise AssertionError("execution did not reach a durable terminal event")


def test_single_claim_prevents_duplicate_workers_and_progress_overflow(tmpdir, monkeypatch):
    monkeypatch.setattr(issue_to_pr, "MAX_PROGRESS_EVENTS_PER_TASK", 2)
    store, task_id, root = _ready_task(tmpdir)
    workflow = IssueToPRWorkflow(store, root / "workspaces")
    started, release = Event(), Event()
    calls = []

    class FakeService:
        def execute_after_plan_approval(self, received_id, target_file, max_rounds):
            calls.append((received_id, target_file, max_rounds))
            started.set()
            assert release.wait(5)
            callback = IssueToPRService(workflow, client=None)._execution_event_callback(task_id)
            for step in range(5):
                callback({"phase": "coder", "round": 1, "step": step,
                          "action": "read_file", "status": "ok",
                          "args": {"path": "buggy.py"}})
            assert store.transition_status(task_id, "executing", "awaiting_diff_approval")
            return {"review_sha256": "fake"}

    manager = ExecutionManager(store, FakeService)
    accepted = manager.submit(task_id, "buggy.py", 1)
    assert accepted["status"] == "executing"
    try:
        assert started.wait(5)
        assert manager.event_page(task_id)["execution_done"] is False
        with pytest.raises(RuntimeError, match="已有执行"):
            manager.submit(task_id, "buggy.py", 1)
    finally:
        release.set()
    events, last_page = _collect_until_done(manager, task_id)
    assert calls == [(task_id, "buggy.py", 1)]
    assert last_page["status"] == "awaiting_diff_approval"
    assert [event["sequence"] for event in events] == sorted(
        {event["sequence"] for event in events}
    )
    assert sum(event["event_type"] == "agent_step" for event in events) == 2
    assert sum(event["event_type"] == "agent_trace_truncated" for event in events) == 1
    assert events[-1]["event_type"] == "execution_finished"
    assert manager.event_page(task_id, last_page["next_after"])["execution_done"] is True


def test_execution_claim_is_atomic_across_store_connections(tmpdir):
    store, task_id, root = _ready_task(tmpdir)
    barrier, outcomes = Barrier(2), []

    def claim():
        independent_store = TaskStore(root / "tasks.sqlite3")
        barrier.wait()
        try:
            independent_store.claim_execution(task_id)
            outcomes.append("claimed")
        except RuntimeError:
            outcomes.append("rejected")

    workers = [Thread(target=claim), Thread(target=claim)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=5)
        assert not worker.is_alive()
    assert sorted(outcomes) == ["claimed", "rejected"]
    assert store.task(task_id)["status"] == "executing"
    assert sum(event["event_type"] == "execution_started"
               for event in store.events(task_id)) == 1


def test_worker_exception_lands_in_terminal_state_without_raw_error(tmpdir):
    store, task_id, _ = _ready_task(tmpdir)

    class FailingService:
        def execute_after_plan_approval(self, *_):
            raise RuntimeError("private failure details")

    manager = ExecutionManager(store, FailingService)
    manager.submit(task_id, "buggy.py")
    events, last_page = _collect_until_done(manager, task_id, limit=2)
    assert last_page["status"] == "execution_failed"
    assert events[-1]["event_type"] == "execution_failed"
    assert json.loads(events[-1]["payload"]) == {"error_type": "RuntimeError"}
    assert "private failure details" not in json.dumps(events)


def test_restart_marks_abandoned_work_interrupted_without_replay(tmpdir):
    store, task_id, _ = _ready_task(tmpdir)
    store.claim_execution(task_id)
    calls = []
    manager = ExecutionManager(store, lambda: calls.append("replayed"))
    assert manager.recover_interrupted() == [task_id]
    events, last_page = _collect_until_done(manager, task_id)
    assert last_page["status"] == "execution_interrupted"
    assert events[-1]["event_type"] == "execution_interrupted"
    assert json.loads(events[-1]["payload"])["replayed"] is False
    assert calls == []
    assert manager.recover_interrupted() == []
    assert sum(event["event_type"] == "execution_interrupted"
               for event in store.events(task_id)) == 1


def test_restart_reconciles_terminal_status_missing_its_final_event(tmpdir):
    store, task_id, _ = _ready_task(tmpdir)
    store.claim_execution(task_id)
    assert store.transition_status(task_id, "executing", "validation_failed")
    manager = ExecutionManager(store, lambda: None)
    assert manager.event_page(task_id)["execution_done"] is False
    assert manager.recover_interrupted() == []
    events, page = _collect_until_done(manager, task_id)
    assert page["status"] == "validation_failed"
    assert events[-1]["event_type"] == "execution_finished"
    assert json.loads(events[-1]["payload"])["recovered"] is True


def test_event_cursor_is_stable_bounded_and_http_parseable(tmpdir, monkeypatch):
    store, task_id, _ = _ready_task(tmpdir)
    other_id = store.create_task("other", "repo", "workspace2", "base")
    store.event(task_id, "a", {})
    store.event(other_id, "other_event", {})
    store.event(task_id, "b", {})
    manager = ExecutionManager(store, lambda: None)
    first = manager.event_page(task_id, after=0, limit=1)
    second = manager.event_page(task_id, after=first["next_after"], limit=1)
    assert first["events"][0]["sequence"] < second["events"][0]["sequence"]
    assert all(event["task_id"] == task_id for event in first["events"] + second["events"])
    with pytest.raises(ValueError, match="after"):
        manager.event_page(task_id, after=-1)
    with pytest.raises(ValueError, match="after"):
        manager.event_page(task_id, after=2**63)
    with pytest.raises(ValueError, match="limit"):
        manager.event_page(task_id, limit=201)

    import service_http
    monkeypatch.setattr(service_http, "execution_manager", manager)
    handler = SimpleNamespace(
        path=f"/tasks/{task_id}/events?after={first['next_after']}&limit=1",
        _reply=lambda status, payload: (status, payload),
    )
    status, page = service_http.Handler.do_GET(handler)
    assert status == 200
    assert page["events"][0]["sequence"] == second["events"][0]["sequence"]
    replies = []
    handler._reply = lambda status, payload: replies.append((status, payload))
    handler.path = f"/tasks/{task_id}/events?after=-1"
    service_http.Handler.do_GET(handler)
    assert replies[-1][0] == 400
