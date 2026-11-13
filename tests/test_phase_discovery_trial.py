"""Exercise repository discovery and the isolated visitor workflow offline."""

import json
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

from core.discovery import recommend_issues, scan_pytest
from core.github import GitHubAPI
from core.trial import PRESET, create_preset
from core.trial_invites import issue_invite
from service_trial import create_trial_app


def test_github_recommendations_exclude_pull_requests_and_assigned_issues(monkeypatch):
    def issue(number, **updates):
        return {"number": number, "state": "open", "body": "Steps to reproduce in buggy.py",
                "labels": [], "title": f"Issue {number}", **updates}

    api = GitHubAPI()

    def open_issues(method, path):
        assert method == "GET" and path.startswith("/repos/example/project/issues?")
        return [issue(1), issue(2, labels=[{"name": "bug"}]), issue(3, pull_request={}),
                issue(4, assignees=[{}]), issue(5, state="closed"), issue(6, body="")]

    monkeypatch.setattr(api, "_request", open_issues)
    results = recommend_issues(api, "https://github.com/example/project")
    assert [row["number"] for row in results] == [2, 1]
    assert results[0]["reasons"][0] == "标记为缺陷或回归"


def test_scan_reports_only_failures_reproduced_on_the_same_tree(tmp_path, monkeypatch):
    repo = create_preset(tmp_path)
    runs = iter(["FAILED test_buggy.py::test_add\nFAILED test_buggy.py::test_other", "FAILED test_buggy.py::test_add"])
    calls = []

    def verify(workspace, paths, sandbox, **candidate):
        calls.append(candidate)
        return 1, next(runs), {"snapshot_unchanged": True}

    monkeypatch.setattr("core.discovery.verify_public", verify)
    result = scan_pytest(repo, object())
    assert result["failures"] == ["test_buggy.py::test_add"]
    assert calls[0] == calls[1] and calls[0]["base_commit"] == result["base_commit"]


def test_trial_requires_visitor_key_and_single_use_plan_approval(monkeypatch, tmp_path):
    origin = "https://trial.example.test"
    supplied = []
    responses = iter([
        ("plan_finish", {"goal": "修复加法", "affected_files": ["buggy.py"], "risks": [],
                         "steps": ["改为加法"], "allowed_commands": [["python", "-m", "pytest", "-q"]]}),
        ("replace_text", {"path": "buggy.py", "old": "return a - b", "new": "return a + b"}),
        ("finish", {"result": "fixed"}),
        ("review_finish", {"passed": True, "issues": []}),
    ])

    class OfflineModel:
        def __init__(self, key, remaining=16):
            supplied.append(key)
            self.remaining = remaining
            self.chat = SimpleNamespace(completions=self)

        def close(self):
            pass

        def create(self, **_):
            self.remaining -= 1
            name, args = next(responses)
            call = SimpleNamespace(id=name, function=SimpleNamespace(name=name, arguments=json.dumps(args)))
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=None, tool_calls=[call]))],
                                   usage=None, _request_id=name)

    class Sandbox:
        def require_available(self):
            pass

        def command_runner(self, workspace):
            return None

        def run(self, workspace, argv, **kwargs):
            assert kwargs["read_only_workspace"]
            assert "return a + b" in (Path(workspace) / "buggy.py").read_text()
            return SimpleNamespace(exit_code=0, output=argv[-2] + '{"cases":1,"collected":1,"passed":1,"failed":0}')

    monkeypatch.setenv("DEEPSEEK_API_KEY", "owner-key-unused")
    token, invite_id = issue_invite(tmp_path, 1)
    other_token, other_id = issue_invite(tmp_path, 1)
    expired_token, expired_id = issue_invite(tmp_path, 1)
    (tmp_path / expired_id).write_text("0", encoding="ascii")
    app = create_trial_app(origin, sandbox=Sandbox(), model_factory=OfflineModel, invite_dir=tmp_path)
    with TestClient(app, base_url=origin) as client:
        key = "visitor-test-key"
        headers = {"origin": origin, "X-Trial-Invite": token}
        assert client.get("/trial/").status_code == 200
        assert client.get("/tasks").status_code == 404
        assert client.get("/trial/access").status_code == 403
        assert client.get("/trial/access", headers={"X-Trial-Invite": expired_token}).status_code == 403
        assert client.get("/trial/access", headers=headers).status_code == 200
        assert client.post("/trial/plan", headers={"origin": origin},
                           json={"api_key": key, "preset": PRESET}).status_code == 403
        assert client.post("/trial/plan", json={"api_key": key, "preset": PRESET}).status_code == 403
        assert client.post("/trial/plan", headers=headers, json={"preset": PRESET}).status_code == 400
        assert client.post("/trial/plan", headers=headers, json={"api_key": key, "preset": "external"}).status_code == 400
        assert supplied == []
        planned = client.post("/trial/plan", headers=headers, json={"api_key": key, "preset": PRESET})
        assert planned.status_code == 200 and key not in planned.text
        payload = {"api_key": key, "ticket": planned.json()["ticket"], "plan_sha256": "0" * 64}
        assert client.post("/trial/execute", headers=headers, json=payload).status_code == 409
        payload["plan_sha256"] = planned.json()["plan_sha256"]
        other_headers = {"origin": origin, "X-Trial-Invite": other_token}
        assert client.post("/trial/execute", headers=other_headers, json=payload).status_code == 404
        (tmp_path / other_id).unlink()
        assert client.get("/trial/access", headers=other_headers).status_code == 403
        result = client.post("/trial/execute", headers=headers, json=payload)
        assert result.status_code == 200 and key not in result.text
        assert result.json()["status"] == "awaiting_diff_approval"
        assert result.json()["junit"]["passed"] == 1
        assert "+    return a + b" in result.json()["diff"]
        assert client.post("/trial/execute", headers=headers, json=payload).status_code == 404
        (tmp_path / invite_id).unlink()
        assert client.post("/trial/execute", headers=headers, json=payload).status_code == 403
    assert supplied == [key, key]
