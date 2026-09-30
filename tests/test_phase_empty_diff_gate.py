"""Offline regression coverage for a Reviewer passing an unchanged worktree."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from coder_reviewer import multi_agent_review
from core.audit import TaskStore
from core.read_views import artifact_content, artifact_list
from core.tools import build_tool_registry
from core.workflow import IssueToPRWorkflow, _digest
from issue_to_pr import IssueToPRService, SEARCH_CODER_TOOLS, _has_candidate_patch


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def _approved_task(tmp_path: Path) -> tuple[IssueToPRWorkflow, str, Path]:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    (repo / "sample.py").write_text("value = 1\n", encoding="utf-8")
    _git(repo, "add", "sample.py")
    _git(repo, "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
         "commit", "-m", "baseline")
    workflow = IssueToPRWorkflow(TaskStore(tmp_path / "tasks.sqlite3"), tmp_path / "workspaces")
    context = workflow.create_task(repo, "Change sample.py")
    plan_hash = workflow.submit_plan(context.task_id, {
        "goal": "Change sample.py", "affected_files": ["sample.py"],
        "steps": ["edit", "test"], "risks": [], "allowed_commands": [],
    })
    workflow.approve_plan(context.task_id, plan_hash, "tester", "approved")
    return workflow, context.task_id, context.workspace


def test_empty_patch_cannot_create_a_diff_review_even_if_reviewer_and_tests_pass(tmp_path):
    workflow, task_id, workspace = _approved_task(tmp_path)
    assert not _has_candidate_patch(workspace, workflow.store.task(task_id)["base_commit"])

    outcome = workflow.record_execution(task_id, "109 passed, 1 skipped", {
        "passed": True, "issues": [], "tests_exit_code": 0,
    })

    assert outcome["validation"] == "failed"
    assert workflow.store.task(task_id)["status"] == "validation_failed"
    assert workflow.store.latest_artifact(task_id, "diff")["content"] == ""
    assert artifact_content(workflow.store, task_id, "diff")["approval_subject_sha256"] is None
    assert "diff_review" not in {item["kind"] for item in artifact_list(workflow.store, task_id)["artifacts"]}
    with pytest.raises(KeyError):
        workflow.store.latest_artifact(task_id, "diff_review")
    failures = [json.loads(event["payload"]) for event in workflow.store.events(task_id)
                if event["event_type"] == "validation_failed"]
    assert failures[-1]["reason"] == "empty_patch"


def test_legacy_empty_patch_may_be_rejected_but_cannot_be_approved(tmp_path):
    workflow, task_id, workspace = _approved_task(tmp_path)
    store = workflow.store
    store.artifact(task_id, "diff", "", _digest(""))
    store.artifact(task_id, "test_report", "1 passed", _digest("1 passed"))
    review_hash = _digest({"legacy": "empty patch"})
    store.artifact(task_id, "diff_review", "{}", review_hash)
    store.set_status(task_id, "awaiting_diff_approval")

    with pytest.raises(ValueError, match="没有可审批的代码改动"):
        workflow.approve_diff(task_id, review_hash, "tester", "approved")
    assert store.task(task_id)["status"] == "awaiting_diff_approval"
    assert not store.is_approved(task_id, "diff", review_hash)
    assert not _has_candidate_patch(workspace, store.task(task_id)["base_commit"])

    workflow.approve_diff(task_id, review_hash, "tester", "rejected")
    assert store.task(task_id)["status"] == "diff_rejected"


def test_approval_requires_the_reviewed_nonempty_diff_to_still_match(tmp_path):
    workflow, task_id, workspace = _approved_task(tmp_path)
    target = workspace / "sample.py"
    target.write_text("value = 2\n", encoding="utf-8")
    review = workflow.record_execution(task_id, "1 passed", {
        "passed": True, "issues": [], "tests_exit_code": 0,
    })
    target.write_text("value = 3\n", encoding="utf-8")

    with pytest.raises(ValueError, match="不一致"):
        workflow.approve_diff(task_id, review["review_sha256"], "tester", "approved")
    assert workflow.store.task(task_id)["status"] == "awaiting_diff_approval"

    target.write_text("value = 2\n", encoding="utf-8")
    workflow.approve_diff(task_id, review["review_sha256"], "tester", "approved")
    assert workflow.store.task(task_id)["status"] == "approved_for_pr"


def test_coder_without_patch_skips_paid_reviewer(tmp_path):
    target = tmp_path / "sample.py"
    target.write_text("value = 1\n", encoding="utf-8")
    calls = []

    def create(**kwargs):
        calls.append(kwargs)
        call = SimpleNamespace(
            id="coder-finish",
            function=SimpleNamespace(name="finish", arguments=json.dumps({"result": "done"})),
        )
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=None, tool_calls=[call]))],
            usage=None,
        )

    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    events = []
    passed, issues, rounds, _ = multi_agent_review(
        client, str(target), task="Change value", max_rounds=3, workspace=tmp_path,
        coder_tools=build_tool_registry("finish"),
        reviewer_tools=build_tool_registry("review_finish"),
        executor=lambda *_: "ok", verbose=False,
        has_candidate_patch=lambda: False, on_event=events.append,
    )

    assert passed is False and rounds == 1 and len(calls) == 1
    assert "未生成可审查" in issues[0]
    assert events[-1]["action"] == "candidate_patch_missing"
    assert SEARCH_CODER_TOOLS.has("replace_text")


def test_workflow_coder_patch_check_tracks_real_git_diff(tmp_path):
    workflow, task_id, workspace = _approved_task(tmp_path)
    base_commit = workflow.store.task(task_id)["base_commit"]
    assert not _has_candidate_patch(workspace, base_commit)
    (workspace / "sample.py").write_text("value = 2\n", encoding="utf-8")
    assert _has_candidate_patch(workspace, base_commit)


def test_same_issue_can_start_again_from_same_clean_repository(tmp_path):
    workflow, task_id, workspace = _approved_task(tmp_path)
    first = workflow.store.task(task_id)
    next_attempt = workflow.create_task(first["repo_path"], first["issue"])

    assert next_attempt.task_id != task_id
    assert next_attempt.workspace != workspace
    assert next_attempt.base_commit == first["base_commit"]
    assert next_attempt.workspace.is_dir() and workspace.is_dir()


def test_worktree_creation_failure_exposes_git_stderr(tmp_path, monkeypatch):
    workflow, task_id, _ = _approved_task(tmp_path)
    repo = workflow.store.task(task_id)["repo_path"]
    original_run = subprocess.run

    def failing_worktree_add(argv, *args, **kwargs):
        if "worktree" in argv and "add" in argv:
            raise subprocess.CalledProcessError(
                128, argv, stderr="fatal: diagnostic worktree failure",
            )
        return original_run(argv, *args, **kwargs)

    monkeypatch.setattr("core.workflow.subprocess.run", failing_worktree_add)
    with pytest.raises(RuntimeError, match="创建 Git worktree 失败：fatal: diagnostic worktree failure"):
        workflow.create_task(repo, "retry")


def test_worktree_creation_enables_long_paths_for_checkout(tmp_path, monkeypatch):
    workflow, task_id, _ = _approved_task(tmp_path)
    repo = workflow.store.task(task_id)["repo_path"]
    original_run = subprocess.run
    worktree_argv = []

    def capture_worktree_add(argv, *args, **kwargs):
        if "worktree" in argv and "add" in argv:
            worktree_argv.append(argv)
        return original_run(argv, *args, **kwargs)

    monkeypatch.setattr("core.workflow.subprocess.run", capture_worktree_add)
    next_attempt = workflow.create_task(repo, "retry")

    assert next_attempt.workspace.is_dir()
    assert len(worktree_argv) == 1
    assert worktree_argv[0][1:3] == ["-c", "core.longpaths=true"]


def test_fake_client_execution_with_no_edit_never_requests_diff_approval(tmp_path):
    workflow, task_id, _ = _approved_task(tmp_path)
    calls = []

    def create(**kwargs):
        calls.append(kwargs)
        call = SimpleNamespace(
            id="coder-finish",
            function=SimpleNamespace(name="finish", arguments=json.dumps({"result": "done"})),
        )
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=None, tool_calls=[call]))],
            usage=None,
        )

    class FakeSandbox:
        def command_runner(self, workspace):
            def unexpected_command(*args, **kwargs):
                raise AssertionError("No command expected from the Coder")

            return unexpected_command

        def run(self, workspace, argv, **kwargs):
            return SimpleNamespace(exit_code=0, output="all pre-existing tests passed")

    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    service = IssueToPRService(workflow, client, sandbox=FakeSandbox())
    outcome = service.execute_after_plan_approval(task_id, "sample.py", max_rounds=3)

    assert outcome["validation"] == "failed"
    assert workflow.store.task(task_id)["status"] == "validation_failed"
    assert len(calls) == 1  # No paid Reviewer call after a no-op Coder.
    assert "diff_review" not in {item["kind"] for item in artifact_list(workflow.store, task_id)["artifacts"]}
    actions = [json.loads(event["payload"]).get("action") for event in workflow.store.events(task_id)
               if event["event_type"] == "agent_step"]
    assert "candidate_patch_missing" in actions


def test_ui_has_separate_empty_patch_approve_and_reject_guards():
    script = (Path(__file__).resolve().parents[1] / "web" / "app.js").read_text(encoding="utf-8")
    assert "function hasReviewableDiff(artifact)" in script
    assert "const canApprove = canDecide && hasPatch && state.fullDiffShown;" in script
    assert '$("reject-diff-button").disabled = !(canDecide && (!hasPatch || state.fullDiffShown));' in script
    assert '(decision === "approved" && !hasPatch)' in script
    assert "本次执行没有生成可审批的代码改动" in script
