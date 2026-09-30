"""Offline publication serialization checks; no GitHub or model calls."""
from concurrent.futures import ThreadPoolExecutor
import json
import threading

import pytest

from core.audit import TaskStore
from core.workflow import IssueToPRWorkflow, _digest


@pytest.fixture
def workflow(tmp_path, monkeypatch):
    store = TaskStore(tmp_path / "tasks.sqlite3")
    workflow = IssueToPRWorkflow(store, tmp_path / "workspaces")
    monkeypatch.setattr("core.workflow._git", lambda *args: "approved patch")
    return workflow


def ready_task(workflow):
    task_id = workflow.store.create_task("fix issue", "unused-repo", "unused-workspace", "base")
    request = {
        "draft": True, "repository": "example/repo",
        "diff_sha256": _digest("approved patch"),
    }
    request_hash = _digest(request)
    workflow.store.artifact(task_id, "draft_pr_request", json.dumps(request), request_hash)
    workflow.store.approve(task_id, "publish", request_hash, "approved", "tester")
    workflow.store.set_status(task_id, "ready_to_publish")
    return task_id, request_hash


def publication_result():
    return {"draft": True, "repository": "example/repo", "number": 1}


def test_concurrent_same_task_cannot_enter_publisher_twice(workflow):
    task_id, request_hash = ready_task(workflow)
    entered, release = threading.Event(), threading.Event()

    class BlockingPublisher:
        calls = 0

        def publish(self, **kwargs):
            self.calls += 1
            entered.set()
            assert release.wait(timeout=10)
            return publication_result()

    publisher = BlockingPublisher()
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(workflow.publish_draft_pr, task_id, request_hash, publisher)
        try:
            assert entered.wait(timeout=10)
            with pytest.raises(RuntimeError, match="任务正在发布"):
                workflow.publish_draft_pr(task_id, request_hash, publisher)
            assert publisher.calls == 1
            assert workflow.store.task(task_id)["status"] == "ready_to_publish"
        finally:
            release.set()
        assert pending.result(timeout=10) == publication_result()

    assert workflow.store.task(task_id)["status"] == "draft_pr_created"
    assert json.loads(workflow.store.latest_artifact(task_id, "draft_pr")["content"]) == publication_result()
    with pytest.raises(RuntimeError, match="任务尚未获得发布审批"):
        workflow.publish_draft_pr(task_id, request_hash, publisher)
    assert publisher.calls == 1


def test_publisher_exception_releases_guard_and_allows_explicit_retry(workflow):
    task_id, request_hash = ready_task(workflow)

    class RetryPublisher:
        calls = 0

        def publish(self, **kwargs):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("offline publisher failure")
            return publication_result()

    publisher = RetryPublisher()
    with pytest.raises(RuntimeError, match="offline publisher failure"):
        workflow.publish_draft_pr(task_id, request_hash, publisher)
    assert workflow.store.task(task_id)["status"] == "ready_to_publish"
    assert workflow.store.events(task_id)[-1]["event_type"] == "draft_pr_publish_failed"
    assert workflow.publish_draft_pr(task_id, request_hash, publisher) == publication_result()
    assert publisher.calls == 2
    assert workflow.store.task(task_id)["status"] == "draft_pr_created"


def test_different_tasks_can_publish_independently(workflow):
    tasks = [ready_task(workflow), ready_task(workflow)]
    rendezvous = threading.Barrier(2)

    class ConcurrentPublisher:
        def publish(self, **kwargs):
            rendezvous.wait(timeout=10)
            return publication_result()

    publisher = ConcurrentPublisher()
    with ThreadPoolExecutor(max_workers=2) as pool:
        pending = [pool.submit(workflow.publish_draft_pr, task_id, subject, publisher)
                   for task_id, subject in tasks]
        assert [item.result(timeout=10) for item in pending] == [publication_result()] * 2
    assert all(workflow.store.task(task_id)["status"] == "draft_pr_created" for task_id, _ in tasks)
