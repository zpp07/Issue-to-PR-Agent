from pathlib import Path
import subprocess
import sys

import pytest

from core.audit import TaskStore
from core.client import StdlibOpenAI, make_client
from core.policy import CommandLevel, CommandPolicy
from core.tools import run_command, write_file
from core.workflow import IssueToPRWorkflow


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True,
                          text=True, encoding="utf-8").stdout.strip()


def test_command_policy_and_shell_free_execution():
    policy = CommandPolicy()
    assert policy.evaluate(["python", "-m", "pytest", "-q"]).level == CommandLevel.ALLOW
    assert policy.evaluate(["git", "push"]).level == CommandLevel.APPROVAL
    assert policy.evaluate(["python", "-c", "print(1)"]).level == CommandLevel.DENY
    assert "argv" in run_command("python -c print(1)")
    assert run_command([sys.executable, "--version"]).startswith("Python")


def test_dependency_free_client_fallback_can_be_constructed():
    client = make_client(api_key="test-key", base_url="http://127.0.0.1:9")
    assert isinstance(client, StdlibOpenAI)
    assert client.chat.completions is not None


def test_write_file_enforces_byte_limit(tmpdir):
    tmp_path = Path(str(tmpdir))
    result = write_file(tmp_path / "oversized.txt", "x" * (201 * 1024))
    assert "内容过大" in result
    assert not (tmp_path / "oversized.txt").exists()


def test_approval_binds_exact_plan_and_diff(tmpdir):
    tmp_path = Path(str(tmpdir))
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init")
    (repo / "sample.py").write_text("value = 1\n", encoding="utf-8")
    git(repo, "add", "sample.py")
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=Test", "-c", "user.email=test@example.com",
                    "commit", "-m", "initial"], check=True, capture_output=True)

    store = TaskStore(tmp_path / "tasks.sqlite3")
    workflow = IssueToPRWorkflow(store, tmp_path / "workspaces")
    context = workflow.create_task(repo, "change sample value")
    plan = {
        "goal": "change value", "affected_files": ["sample.py"], "steps": ["edit", "test"],
        "risks": ["logic regression"], "allowed_commands": [["python", "-m", "pytest", "-q"]],
    }
    plan_hash = workflow.submit_plan(context.task_id, plan)
    workflow.approve_plan(context.task_id, plan_hash, "tester", "approved")
    assert store.task(context.task_id)["status"] == "ready_to_execute"

    (context.workspace / "sample.py").write_text("value = 2\n", encoding="utf-8")
    review = workflow.record_execution(context.task_id, "1 passed", {"passed": True, "issues": [], "tests_exit_code": 0})
    workflow.approve_diff(context.task_id, review["review_sha256"], "tester", "approved")
    assert store.task(context.task_id)["status"] == "approved_for_pr"
    assert len(store.events(context.task_id)) >= 8

    subprocess.run(["git", "-C", str(repo), "worktree", "remove", "--force", str(context.workspace)],
                   check=True, capture_output=True)


def test_failed_independent_validation_never_requests_diff_approval(tmpdir):
    tmp_path = Path(str(tmpdir))
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init")
    (repo / "sample.py").write_text("value = 1\n", encoding="utf-8")
    git(repo, "add", "sample.py")
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=Test", "-c", "user.email=test@example.com",
                    "commit", "-m", "initial"], check=True, capture_output=True)
    store = TaskStore(tmp_path / "tasks.sqlite3")
    workflow = IssueToPRWorkflow(store, tmp_path / "workspaces")
    context = workflow.create_task(repo, "change value")
    plan = {"goal": "x", "affected_files": ["sample.py"], "steps": ["x"], "risks": [],
            "allowed_commands": [["python", "-m", "pytest", "-q"]]}
    plan_hash = workflow.submit_plan(context.task_id, plan)
    workflow.approve_plan(context.task_id, plan_hash, "tester", "approved")
    outcome = workflow.record_execution(context.task_id, "no tests ran", {"passed": True, "tests_exit_code": 5})
    assert outcome["validation"] == "failed"
    assert store.task(context.task_id)["status"] == "validation_failed"
    subprocess.run(["git", "-C", str(repo), "worktree", "remove", "--force", str(context.workspace)],
                   check=True, capture_output=True)


def test_dirty_repository_is_rejected_before_worktree_creation(tmpdir):
    tmp_path = Path(str(tmpdir))
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init")
    (repo / "tracked.py").write_text("x = 1\n", encoding="utf-8")
    git(repo, "add", "tracked.py")
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=Test", "-c", "user.email=test@example.com",
                    "commit", "-m", "initial"], check=True, capture_output=True)
    (repo / "untracked_test.py").write_text("pass\n", encoding="utf-8")
    workflow = IssueToPRWorkflow(TaskStore(tmp_path / "tasks.sqlite3"), tmp_path / "workspaces")
    with pytest.raises(RuntimeError, match="未提交"):
        workflow.create_task(repo, "must be reproducible")
