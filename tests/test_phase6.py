import json
from pathlib import Path
import subprocess

import pytest

from benchmark.analyze_e2 import load_rows, summarize_arm, validate_rows, wilson_interval
from core.audit import TaskStore
from core.github import GitHubAPI, GitHubError, repository_from_remote
from core.workflow import IssueToPRWorkflow


def _row(protocol, arm_case="case", trial=1, passed=True):
    return {
        "protocol_version": protocol,
        "protocol_hash": f"hash-{protocol}",
        "evaluation_protocol_hash": "eval-hash",
        "evaluation_protocol": {"hidden_tests_visible_to_agent": False},
        "code_revision": "abc123",
        "tracked_worktree_dirty": False,
        "case": arm_case,
        "trial": trial,
        "tests_pass": passed,
        "total_tokens": 100,
        "cost_rmb": 0.1,
        "elapsed_seconds": 2,
        "exit_reason": "verified_patch",
        "agent_completed": True,
        "regression_detected": False,
        "revision_attempted": False,
    }


def test_wilson_interval_contains_observed_rate():
    low, high = wilson_interval(7, 12)
    assert low == pytest.approx(0.319, abs=0.001)
    assert high == pytest.approx(0.807, abs=0.001)
    assert low < 7 / 12 < high


def test_load_validate_and_summarize(tmpdir):
    protocol = "single-v6-verified-auto-np-off"
    path = tmpdir.join("results.jsonl")
    rows = [_row(protocol, trial=1, passed=True), _row(protocol, trial=2, passed=False)]
    path.write("\n".join(json.dumps(row) for row in rows) + "\n", ensure=True)

    loaded = load_rows(path)
    metadata = validate_rows(loaded)
    summary = summarize_arm(loaded)

    assert metadata["runs"] == 2
    assert all(row["arm"] == "B" for row in loaded)
    assert summary["passes"] == 1
    assert summary["total_tokens"] == 200
    assert summary["controller_triggers"] == 2
    assert summary["controller_passes"] == 1


def test_validate_rejects_duplicate_run_key(tmpdir):
    protocol = "single-v6-model-finish-np-off"
    path = tmpdir.join("duplicates.jsonl")
    row = _row(protocol)
    path.write(json.dumps(row) + "\n" + json.dumps(row) + "\n", ensure=True)

    with pytest.raises(ValueError, match="duplicate run key"):
        validate_rows(load_rows(path))


def _git(repo, *args):
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True,
        text=True, encoding="utf-8",
    ).stdout.strip()


def _approved_task(tmpdir):
    root = Path(str(tmpdir))
    repo = root / "repo"
    repo.mkdir()
    _git(repo, "init")
    (repo / "sample.py").write_text("value = 1\n", encoding="utf-8")
    _git(repo, "add", "sample.py")
    _git(repo, "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
         "commit", "-m", "initial")
    workflow = IssueToPRWorkflow(TaskStore(root / "tasks.sqlite3"), root / "workspaces")
    context = workflow.create_task(repo, "change sample value")
    plan = {
        "goal": "change value", "affected_files": ["sample.py"],
        "steps": ["edit", "test"], "risks": [],
        "allowed_commands": [["python", "-m", "pytest", "-q"]],
    }
    plan_hash = workflow.submit_plan(context.task_id, plan)
    workflow.approve_plan(context.task_id, plan_hash, "tester", "approved")
    (context.workspace / "sample.py").write_text("value = 2\n", encoding="utf-8")
    review = workflow.record_execution(
        context.task_id, "1 passed", {"passed": True, "tests_exit_code": 0},
    )
    workflow.approve_diff(context.task_id, review["review_sha256"], "tester", "approved")
    return workflow, context


class _FakePublisher:
    def __init__(self):
        self.calls = []

    def publish(self, **kwargs):
        self.calls.append(kwargs)
        return {
            "number": 7, "html_url": "https://github.com/owner/repo/pull/7",
            "draft": True, "repository": "owner/repo", "commit": "abc123",
        }


def test_draft_pr_requires_separate_exact_publish_approval(tmpdir):
    workflow, context = _approved_task(tmpdir)
    prepared = workflow.prepare_draft_pr(
        context.task_id, repository="owner/repo", base_branch="main",
        head_branch="agent/change-value", title="fix: change sample value",
    )
    request_hash = prepared["request_sha256"]
    assert prepared["request"]["draft"] is True
    assert workflow.store.task(context.task_id)["status"] == "awaiting_publish_approval"
    with pytest.raises(RuntimeError, match="发布审批"):
        workflow.publish_draft_pr(context.task_id, request_hash, _FakePublisher())

    workflow.approve_publish(context.task_id, request_hash, "tester", "approved")
    publisher = _FakePublisher()
    result = workflow.publish_draft_pr(context.task_id, request_hash, publisher)
    assert result["draft"] is True
    assert len(publisher.calls) == 1
    assert publisher.calls[0]["expected_diff_sha256"] == prepared["request"]["diff_sha256"]
    assert workflow.store.task(context.task_id)["status"] == "draft_pr_created"


def test_draft_pr_publication_rejects_diff_drift(tmpdir):
    workflow, context = _approved_task(tmpdir)
    prepared = workflow.prepare_draft_pr(
        context.task_id, repository="owner/repo", base_branch="main",
        head_branch="agent/change-value", title="fix: change sample value",
    )
    workflow.approve_publish(context.task_id, prepared["request_sha256"], "tester", "approved")
    (context.workspace / "sample.py").write_text("value = 3\n", encoding="utf-8")
    with pytest.raises(ValueError, match="diff 已变化"):
        workflow.publish_draft_pr(
            context.task_id, prepared["request_sha256"], _FakePublisher(),
        )
    assert workflow.store.task(context.task_id)["status"] == "ready_to_execute"


def test_github_api_always_requests_a_draft_pr():
    captured = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def read(self):
            return json.dumps({
                "number": 9, "html_url": "https://github.com/owner/repo/pull/9",
                "draft": True,
            }).encode()

    class Opener:
        def open(self, request, timeout):
            captured.update(json.loads(request.data.decode()))
            return Response()

    result = GitHubAPI("test-token", opener=Opener()).create_draft_pr(
        "owner/repo", title="title", body="body", head="agent/fix", base="main",
    )
    assert captured["draft"] is True
    assert result["draft"] is True
    assert repository_from_remote("git@github.com:owner/repo.git") == "owner/repo"
    with pytest.raises(GitHubError, match="GITHUB_TOKEN"):
        GitHubAPI().require_write_auth()


def test_workflow_rejects_non_draft_publisher_result(tmpdir):
    workflow, context = _approved_task(tmpdir)
    prepared = workflow.prepare_draft_pr(
        context.task_id, repository="owner/repo", base_branch="main",
        head_branch="agent/change-value", title="fix: change sample value",
    )
    workflow.approve_publish(context.task_id, prepared["request_sha256"], "tester", "approved")

    class UnsafePublisher:
        def publish(self, **_):
            return {"draft": False, "repository": "owner/repo", "number": 1}

    with pytest.raises(RuntimeError, match="Draft PR"):
        workflow.publish_draft_pr(
            context.task_id, prepared["request_sha256"], UnsafePublisher(),
        )
    assert workflow.store.task(context.task_id)["status"] == "ready_to_publish"
