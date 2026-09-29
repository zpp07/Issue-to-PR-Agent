"""Offline checks for the W3 public read views and HTTP routes."""

import hashlib
import json
import time
from types import SimpleNamespace

import pytest

from core.audit import TaskStore
from core.execution import ExecutionManager
from core.read_views import artifact_content, artifact_list, task_page


def _sha(content):
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _structured(content):
    return _sha(json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def _store_with_artifacts(tmpdir):
    store = TaskStore(tmpdir.join("tasks.sqlite3"))
    task_id = store.create_task("\n  Fix Unicode diff  \nprivate second line", "secret-repo", "secret-workspace", "base")
    plan = {"goal": "fix", "steps": ["edit", "test"]}
    plan_content = json.dumps(plan, ensure_ascii=False, indent=2)
    store.artifact(task_id, "plan", plan_content, _structured(plan))
    diff = "diff --git a/a.py b/a.py\n+你好\n" + "x" * 25_000
    store.artifact(task_id, "diff", diff, _sha(diff))
    report = "1 passed\n"
    store.artifact(task_id, "test_report", report, _sha(report))
    review = {"diff_sha256": _sha(diff), "test_report_sha256": _sha(report), "passed": True}
    store.artifact(task_id, "diff_review", json.dumps(review, indent=2), _structured(review))
    request = {"title": "Fix", "draft": True}
    store.artifact(task_id, "draft_pr_request", json.dumps(request, indent=2), _structured(request))
    draft = {"url": "https://example.invalid/pr/1", "draft": True}
    store.artifact(task_id, "draft_pr", json.dumps(draft, indent=2), _structured(draft))
    store.artifact(task_id, "gold_patch", "HIDDEN GOLD", _sha("HIDDEN GOLD"))
    store.artifact(task_id, "hidden_tests", "HIDDEN TESTS", _sha("HIDDEN TESTS"))
    return store, task_id, diff, review


def test_task_page_is_bounded_redacted_and_deterministic(tmpdir):
    store, task_id, _, _ = _store_with_artifacts(tmpdir)
    page = task_page(store, limit=1, offset=0)
    assert page == {"tasks": [{
        "id": task_id, "status": "created", "issue_summary": "Fix Unicode diff",
        "created_at": store.task(task_id)["created_at"],
        "updated_at": store.task(task_id)["updated_at"],
    }], "limit": 1, "offset": 0}
    assert task_page(store, limit=1, offset=1)["tasks"] == []
    assert "secret-repo" not in json.dumps(page)
    assert "private second line" not in json.dumps(page)
    for limit, offset in ((True, 0), (0, 0), (101, 0), (1, True), (1, -1), (1, 2**63)):
        with pytest.raises(ValueError):
            task_page(store, limit, offset)


def test_artifacts_allowlist_full_content_and_distinct_approval_hashes(tmpdir):
    store, task_id, diff, review = _store_with_artifacts(tmpdir)
    listing = artifact_list(store, task_id)
    assert {item["kind"] for item in listing["artifacts"]} == {
        "plan", "diff", "test_report", "diff_review", "draft_pr_request", "draft_pr",
    }
    assert all("content" not in item for item in listing["artifacts"])
    assert "HIDDEN" not in json.dumps(listing)
    full_diff = artifact_content(store, task_id, "diff")
    assert full_diff["content"] == diff  # More than 20k characters, no truncation.
    assert full_diff["size_bytes"] == len(diff.encode("utf-8"))
    assert full_diff["content_sha256"] == _sha(diff)
    assert full_diff["artifact_sha256"] == _sha(diff)
    assert full_diff["approval_kind"] == "diff"
    assert full_diff["approval_subject_kind"] == "diff_review"
    assert full_diff["approval_subject_sha256"] == _structured(review)
    assert full_diff["approval_subject_sha256"] != full_diff["content_sha256"]
    assert artifact_content(store, task_id, "diff_review")["approval_subject_sha256"] == _structured(review)
    plan = artifact_content(store, task_id, "plan")
    assert plan["approval_kind"] == "plan"
    assert plan["approval_subject_sha256"] == plan["artifact_sha256"]
    assert plan["content_sha256"] != plan["approval_subject_sha256"]
    request = artifact_content(store, task_id, "draft_pr_request")
    assert request["approval_kind"] == "publish"
    assert request["approval_subject_kind"] == "draft_pr_request"
    assert request["content_sha256"] != request["approval_subject_sha256"]
    assert artifact_content(store, task_id, "draft_pr")["approval_subject_sha256"] is None
    for forbidden in ("gold_patch", "hidden_tests", "review_report", "missing"):
        with pytest.raises(KeyError):
            artifact_content(store, task_id, forbidden)
    with pytest.raises(KeyError):
        artifact_list(store, "missing-task")


def test_stale_review_is_not_issued_as_current_diff_approval_subject(tmpdir):
    store, task_id, _, _ = _store_with_artifacts(tmpdir)
    changed_diff = "diff --git a/b.py b/b.py\n+different"
    store.artifact(task_id, "diff", changed_diff, _sha(changed_diff))
    current = artifact_content(store, task_id, "diff")
    assert current["approval_subject_sha256"] is None
    assert current["approval_subject_kind"] is None


def test_dependency_free_http_routes_use_public_views(tmpdir, monkeypatch):
    import service_http

    store, task_id, diff, review = _store_with_artifacts(tmpdir)
    monkeypatch.setattr(service_http, "store", store)
    handler = SimpleNamespace(path="/tasks?limit=1&offset=0", _reply=lambda code, body: (code, body))
    code, page = service_http.Handler.do_GET(handler)
    assert code == 200 and page["tasks"][0]["id"] == task_id
    handler.path = f"/tasks/{task_id}/artifacts"
    code, listing = service_http.Handler.do_GET(handler)
    assert code == 200 and len(listing["artifacts"]) == 6
    handler.path = f"/tasks/{task_id}/artifacts/diff"
    code, full = service_http.Handler.do_GET(handler)
    assert code == 200 and full["content"] == diff
    assert full["approval_subject_sha256"] == _structured(review)
    replies = []
    handler._reply = lambda code, body: replies.append((code, body))
    handler.path = f"/tasks/{task_id}/artifacts/hidden_tests"
    service_http.Handler.do_GET(handler)
    assert replies[-1][0] == 404
    handler.path = "/tasks?limit=0"
    service_http.Handler.do_GET(handler)
    assert replies[-1][0] == 400
    handler.path = "/tasks?offset=-1"
    service_http.Handler.do_GET(handler)
    assert replies[-1][0] == 400


def test_optional_fastapi_routes_use_the_same_public_views(tmpdir, monkeypatch):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient
    import service_api

    store, task_id, diff, review = _store_with_artifacts(tmpdir)
    monkeypatch.setattr(service_api, "store", store)
    with TestClient(service_api.app) as client:
        tasks = client.get("/tasks?limit=1").json()
        assert tasks["tasks"][0]["id"] == task_id
        listed = client.get(f"/tasks/{task_id}/artifacts").json()
        assert len(listed["artifacts"]) == 6
        full = client.get(f"/tasks/{task_id}/artifacts/diff").json()
        assert full["content"] == diff
        assert full["approval_subject_sha256"] == _structured(review)
        assert client.get(f"/tasks/{task_id}/artifacts/gold_patch").status_code == 404
        assert client.get("/tasks?limit=0").status_code == 400


def test_execution_error_prints_to_stderr_but_not_to_events(tmpdir, capfd):
    store = TaskStore(tmpdir.join("failure.sqlite3"))
    task_id = store.create_task("issue", "repo", "workspace", "base")
    store.set_status(task_id, "ready_to_execute")

    class FailingService:
        def execute_after_plan_approval(self, *_):
            raise RuntimeError("private failure details")

    manager = ExecutionManager(store, FailingService)
    manager.submit(task_id, "buggy.py")
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if manager.event_page(task_id)["execution_done"]:
            break
        time.sleep(0.01)
    else:
        raise AssertionError("worker never finished")
    stderr = capfd.readouterr().err
    assert "Traceback" in stderr
    assert "private failure details" in stderr
    assert "private failure details" not in json.dumps(store.events(task_id))
    assert json.loads(store.events(task_id)[-1]["payload"]) == {"error_type": "RuntimeError"}
