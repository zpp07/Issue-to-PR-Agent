"""Offline W4 checks for same-origin UI assets and simulated publication."""

from io import BytesIO
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

import service_http
from core.audit import TaskStore
from core.fake_publisher import FakeDraftPRPublisher
from core.workflow import IssueToPRWorkflow


def test_static_ui_is_served_same_origin_without_external_dependencies():
    class StaticHandler:
        def __init__(self, path):
            self.path = path
            self.wfile = BytesIO()
            self.status = None
            self.headers = {}

        def _static(self, path):
            return service_http.Handler._static(self, path)

        def send_response(self, status):
            self.status = status

        def send_header(self, name, value):
            self.headers[name] = value

        def end_headers(self):
            pass

    for path, expected_type in (
        ("/", "text/html"), ("/app.css", "text/css"), ("/app.js", "text/javascript"),
    ):
        handler = StaticHandler(path)
        service_http.Handler.do_GET(handler)
        assert handler.status == 200
        assert handler.headers["Content-Type"].startswith(expected_type)
        assert int(handler.headers["Content-Length"]) == len(handler.wfile.getvalue())
        assert handler.headers["X-Content-Type-Options"] == "nosniff"
        assert "script-src 'self'" in handler.headers["Content-Security-Policy"]
        assert "connect-src 'self'" in handler.headers["Content-Security-Policy"]
        assert handler.wfile.getvalue()

    html = (Path(service_http.WEB_DIR) / "index.html").read_text(encoding="utf-8")
    assert '<script src="/app.js" defer></script>' in html
    assert '<link rel="stylesheet" href="/app.css">' in html
    assert 'src="http' not in html and 'href="http' not in html


def test_ui_config_and_fake_publish_need_explicit_mode(monkeypatch):
    class RecordingWorkflow:
        calls = 0

        def publish_draft_pr(self, task_id, subject_hash, publisher):
            self.calls += 1
            assert task_id == "task-1" and subject_hash == "approved-request-hash"
            assert isinstance(publisher, FakeDraftPRPublisher)
            return publisher.publish(
                workspace="nonexistent", base_commit="base", expected_diff_sha256="diff",
                request={"repository": "example/repo", "head_branch": "agent/fix", "base_branch": "main"},
            )

    workflow = RecordingWorkflow()
    monkeypatch.setattr(service_http, "workflow", workflow)
    replies = []
    post = SimpleNamespace(
        path="/tasks/task-1/draft-pr/simulate",
        _body=lambda: {"subject_hash": "approved-request-hash"},
        _reply=lambda status, body: replies.append((status, body)),
    )
    get = SimpleNamespace(path="/ui-config", _reply=lambda status, body: (status, body))

    monkeypatch.setattr(service_http, "FAKE_PUBLISHER_MODE", False)
    monkeypatch.setattr(service_http, "REAL_PUBLISHER_MODE", False)
    assert service_http.Handler.do_GET(get) == (200, {
        "fake_publisher": False, "real_publisher": False,
    })
    service_http.Handler.do_POST(post)
    assert replies[-1][0] == 404 and workflow.calls == 0

    monkeypatch.setattr(service_http, "FAKE_PUBLISHER_MODE", True)
    assert service_http.Handler.do_GET(get) == (200, {
        "fake_publisher": True, "real_publisher": False,
    })
    service_http.Handler.do_POST(post)
    assert replies[-1][0] == 200
    assert replies[-1][1]["simulated"] is True
    assert replies[-1][1]["draft"] is True
    assert replies[-1][1]["html_url"] is None
    assert workflow.calls == 1
    post.path = "/tasks/task-1/draft-pr/publish"
    service_http.Handler.do_POST(post)
    assert replies[-1][0] == 403 and workflow.calls == 1


def test_real_publish_route_needs_explicit_mode_and_confirmation(monkeypatch):
    calls = []

    class RecordingWorkflow:
        def publish_draft_pr(self, task_id, subject_hash, publisher):
            calls.append((task_id, subject_hash, publisher))
            return {
                "repository": "example/repo", "draft": True, "number": 12,
                "html_url": "https://github.com/example/repo/pull/12",
            }

    replies = []
    body = {"subject_hash": "approved-request-hash", "confirmation": "create-draft-pr"}
    post = SimpleNamespace(
        path="/tasks/task-1/draft-pr/publish", _body=lambda: body,
        headers={"Host": "127.0.0.1:8765", "Content-Type": "application/json"},
        _reply=lambda status, payload: replies.append((status, payload)),
    )
    monkeypatch.setattr(service_http, "workflow", RecordingWorkflow())
    monkeypatch.setattr(service_http, "FAKE_PUBLISHER_MODE", False)
    monkeypatch.setattr(service_http, "REAL_PUBLISHER_MODE", False)

    service_http.Handler.do_POST(post)
    assert replies[-1][0] == 403 and calls == []

    monkeypatch.setattr(service_http, "REAL_PUBLISHER_MODE", True)
    body.pop("confirmation")
    service_http.Handler.do_POST(post)
    assert replies[-1][0] == 409 and calls == []

    sentinel = object()
    body["confirmation"] = "create-draft-pr"
    monkeypatch.setattr(service_http.GitHubAPI, "from_env", lambda: "api")
    monkeypatch.setattr(service_http, "GitHubDraftPRPublisher", lambda api: sentinel)
    service_http.Handler.do_POST(post)
    assert replies[-1][0] == 200
    assert calls == [("task-1", "approved-request-hash", sentinel)]


def test_frontend_publish_modes_and_polling_contract():
    script = (Path(service_http.WEB_DIR) / "app.js").read_text(encoding="utf-8")
    assert "/draft-pr/publish" in script
    assert "/draft-pr/simulate" in script
    assert 'confirmation: "create-draft-pr"' in script
    assert "state.realPublisher" in script
    assert "real-publish-confirm" in script
    assert "response.status !== 202" in script
    assert script.count("startPolling(taskId)") == 2  # Definition and the execute-success call.
    assert "innerHTML" not in script  # Issue, trace and diff are rendered as text nodes.
    assert "approval_subject_sha256" in script
    assert "state.fullDiffShown" in script
    assert "affected_files" in script
    assert "npm" not in script and "https://" not in script


def test_simulated_publish_records_state_without_commit_push_or_github(tmpdir, monkeypatch):
    def git(repo, *args):
        return subprocess.run(
            ["git", "-C", str(repo), *args], check=True, capture_output=True,
            text=True, encoding="utf-8",
        ).stdout.strip()

    root = Path(str(tmpdir))
    repo = root / "repo"
    repo.mkdir()
    git(repo, "init")
    (repo / "sample.py").write_text("value = 1\n", encoding="utf-8")
    git(repo, "add", "sample.py")
    git(repo, "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
        "commit", "-m", "initial")
    workflow = IssueToPRWorkflow(TaskStore(root / "tasks.sqlite3"), root / "workspaces")
    context = workflow.create_task(repo, "change sample value")
    plan_hash = workflow.submit_plan(context.task_id, {
        "goal": "change value", "affected_files": ["sample.py"],
        "steps": ["edit", "test"], "risks": [],
        "allowed_commands": [["python", "-m", "pytest", "-q"]],
    })
    workflow.approve_plan(context.task_id, plan_hash, "tester", "approved")
    (context.workspace / "sample.py").write_text("value = 2\n", encoding="utf-8")
    review = workflow.record_execution(
        context.task_id, "1 passed", {"passed": True, "tests_exit_code": 0},
    )
    workflow.approve_diff(context.task_id, review["review_sha256"], "tester", "approved")
    frozen = workflow.prepare_draft_pr(
        context.task_id, repository="example/repo", base_branch="main",
        head_branch="agent/change-value", title="fix: change sample value",
    )
    workflow.approve_publish(context.task_id, frozen["request_sha256"], "tester", "approved")

    monkeypatch.setattr(service_http, "FAKE_PUBLISHER_MODE", True)
    monkeypatch.setattr(service_http, "workflow", workflow)
    handler = SimpleNamespace(
        path=f"/tasks/{context.task_id}/draft-pr/simulate",
        _body=lambda: {"subject_hash": frozen["request_sha256"]},
        _reply=lambda status, body: (status, body),
    )
    status, result = service_http.Handler.do_POST(handler)
    assert status == 200 and result["simulated"] is True
    assert result["html_url"] is None
    assert workflow.store.task(context.task_id)["status"] == "draft_pr_created"
    assert json.loads(workflow.store.latest_artifact(context.task_id, "draft_pr")["content"])["simulated"] is True
    assert git(context.workspace, "rev-parse", "HEAD") == context.base_commit
    assert git(context.workspace, "status", "--short") == "M sample.py"
    assert git(context.workspace, "remote") == ""
