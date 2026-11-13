from pathlib import Path
import json
import subprocess
from types import SimpleNamespace

import pytest

from core.policy import CommandPolicy
from core.audit import TaskStore
from core.candidate import candidate, git_run
from core.github import GitHubDraftPRPublisher, GitHubError
from core.sandbox import DockerSandbox, DockerUnavailable, SandboxResult
from core.search import build_code_search, chunk_repository
from core.tools import make_executor
from core.verification import verify_public
from issue_to_pr import IssueToPRService
from core.workflow import IssueToPRWorkflow


def test_product_requires_sandbox_before_accessing_task_or_client():
    with pytest.raises(DockerUnavailable, match="trusted_local"):
        IssueToPRService(None, None).execute_after_plan_approval("unused", "sample.py")


@pytest.mark.parametrize("argv", [
    ["python", "-m", "pytest", "-p", "evil"],
    ["pytest", "-o", "addopts=--collect-only"],
    ["ruff", "check", ".", "--fix"],
    ["git", "diff", "--output=sample.py"],
])
def test_command_policy_denies_unapproved_flags(argv):
    assert not CommandPolicy().evaluate(argv).allowed


def test_model_command_mount_is_read_only(monkeypatch, tmp_path):
    sandbox = DockerSandbox()
    calls = []
    monkeypatch.setattr(sandbox, "run", lambda *args, **kwargs:
                        calls.append(kwargs) or SandboxResult(0, "ok"))
    sandbox.command_runner(tmp_path)(["pytest", "-q"])
    assert calls[0]["read_only_workspace"] is True


@pytest.mark.parametrize("mode", ["pass", "empty", "mutate", "drift"])
def test_verify_public_snapshot_and_evidence(tmp_path, mode):
    workspace = tmp_path / "repo"
    workspace.mkdir()
    subprocess.run(["git", "init", "-q", str(workspace)], check=True)
    test_file = workspace / "test_sample.py"
    test_file.write_text("def test_sample(): pass\n", encoding="utf-8")

    class Sandbox:
        def run(self, path, argv, **kwargs):
            assert path != workspace
            assert kwargs["read_only_workspace"] is True
            assert (path / test_file.name).read_bytes() == test_file.read_bytes()
            if mode == "mutate":
                (path / test_file.name).write_text("modified", encoding="utf-8")
            if mode == "drift":
                test_file.write_text("modified", encoding="utf-8")
            output = "exit early" if mode == "empty" else argv[-2] + '{"cases":1,"collected":1,"passed":1,"failed":0}'
            return SandboxResult(0, output)

    code, _, evidence = verify_public(workspace, [test_file.name], Sandbox())
    assert (code == 0) is (mode == "pass")
    assert evidence["snapshot_unchanged"] is (mode in {"pass", "empty"})
    if mode != "drift":
        assert test_file.read_text(encoding="utf-8") == "def test_sample(): pass\n"


def test_timeout_removes_named_container(monkeypatch, tmp_path):
    sandbox = DockerSandbox()
    calls = []
    monkeypatch.setattr(sandbox, "require_available", lambda: None)

    def run(argv, **kwargs):
        calls.append(argv)
        if argv[1] == "run":
            raise subprocess.TimeoutExpired(argv, 1)
        return subprocess.CompletedProcess(argv, 0, stdout="sha256:local\n")

    monkeypatch.setattr(subprocess, "run", run)
    result = sandbox.run(tmp_path, ["pytest"], timeout=1)
    name = calls[1][calls[1].index("--name") + 1]
    assert calls[2][1:] == ["rm", "-f", name]
    assert result.timed_out and result.image_id == "sha256:local"


@pytest.mark.parametrize("mode", ["preserve", "retarget", "escape", "populated_submodule"])
def test_verify_public_directory_symlink(tmp_path, mode):
    workspace = tmp_path / "repo"
    workspace.mkdir()
    git_run(workspace, ["init", "-q"])
    git_run(workspace, ["config", "core.symlinks", "true"])
    (workspace / ".agents" / "skills").mkdir(parents=True)
    (workspace / ".agents" / "skills" / "example.txt").write_text("skill", encoding="utf-8")
    (workspace / ".claude").mkdir()
    link = workspace / ".claude" / "skills"
    target = "../../outside" if mode == "escape" else "../.agents/skills"
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError as exc:
        if getattr(exc, "winerror", None) != 1314:
            raise
        pytest.skip("Windows requires permission to create symbolic links")
    git_run(workspace, ["add", "."])
    git_run(workspace, ["-c", "user.name=Test", "-c", "user.email=test@example.invalid",
                        "commit", "-qm", "initial"])
    submodule_commit = git_run(workspace, ["rev-parse", "HEAD"]).stdout.strip()
    git_run(workspace, ["update-index", "--add", "--cacheinfo", f"160000,{submodule_commit},vendor"])
    (workspace / "vendor").mkdir()
    git_run(workspace, ["-c", "user.name=Test", "-c", "user.email=test@example.invalid",
                        "commit", "-qm", "base"])
    base = git_run(workspace, ["rev-parse", "HEAD"]).stdout.strip()
    tree = candidate(workspace, base).tree
    if mode == "populated_submodule":
        (workspace / "vendor" / ".git").write_text("gitdir: unavailable", encoding="utf-8")

    class Sandbox:
        def run(self, snapshot, argv, **kwargs):
            assert mode in {"preserve", "retarget"}
            assert (snapshot / "vendor").is_dir()
            assert not list((snapshot / "vendor").iterdir())
            copied = snapshot / ".claude" / "skills"
            assert copied.is_symlink()
            assert copied.resolve() == snapshot / ".agents" / "skills"
            assert (copied / "example.txt").read_text(encoding="utf-8") == "skill"
            if mode == "retarget":
                copied.unlink()
                copied.symlink_to("..", target_is_directory=True)
            return SandboxResult(0, argv[-1] + '{"cases":1,"collected":1,"passed":1,"failed":0}')

    if mode == "escape":
        with pytest.raises(ValueError, match="工作区外"):
            verify_public(workspace, [], Sandbox(), base_commit=base, expected_tree=tree)
    elif mode == "populated_submodule":
        with pytest.raises(ValueError, match="空子模块"):
            verify_public(workspace, [], Sandbox(), base_commit=base, expected_tree=tree)
    else:
        code, _, evidence = verify_public(workspace, [], Sandbox(), base_commit=base, expected_tree=tree)
        assert (code == 0) == evidence["snapshot_unchanged"] == (mode == "preserve")


def _approved_task(tmp_path, files):
    repo = tmp_path / "source"
    repo.mkdir()
    git_run(repo, ["init", "-q"])
    git_run(repo, ["config", "user.name", "Offline Test"])
    git_run(repo, ["config", "user.email", "test@example.invalid"])
    (repo / "a.py").write_text("a = 1\n", encoding="utf-8")
    (repo / "b.py").write_text("b = 1\n", encoding="utf-8")
    git_run(repo, ["add", "."])
    git_run(repo, ["commit", "-qm", "base"])
    git_run(repo, ["remote", "add", "origin", "https://github.com/test/fixture.git"])
    workflow = IssueToPRWorkflow(TaskStore(tmp_path / "audit.db"), tmp_path / "workspaces")
    ctx = workflow.create_task(repo, "Example change")
    plan = workflow.submit_plan(ctx.task_id, {
        "goal": "Fix", "affected_files": files, "steps": ["edit"], "risks": [],
        "allowed_commands": [["git", "status"]],
    })
    workflow.approve_plan(ctx.task_id, plan, "tester", "approved")
    return workflow, ctx


def test_plan_enforces_file_and_argv_and_final_paths(tmp_path):
    workflow, ctx = _approved_task(tmp_path, ["a.py"])
    execute = workflow.executor_for(ctx.task_id, enforce_plan=True)
    assert "allowlist" in execute("write_file", {"path": "b.py", "content": "b = 2"})
    assert "当前批准计划" in execute("run_command", {"argv": ["git", "diff"]})
    assert (ctx.workspace / "b.py").read_text() == "b = 1\n"
    (ctx.workspace / "b.py").write_text("b = 2\n", encoding="utf-8")
    result = workflow.record_execution(ctx.task_id, "1 passed", {"passed": True, "tests_exit_code": 0})
    assert result["validation"] == "failed"
    failure = next(event for event in workflow.store.events(ctx.task_id)
                   if event["event_type"] == "validation_failed")
    assert json.loads(failure["payload"])["outside_approved_plan"] == ["b.py"]


@pytest.mark.parametrize("mixed", [False, True])
def test_approved_new_file_reaches_same_committed_tree(tmp_path, monkeypatch, mixed):
    workflow, ctx = _approved_task(tmp_path, ["new.py", "a.py", "b.py"])
    execute = workflow.executor_for(ctx.task_id, enforce_plan=True)
    assert "已写入" in execute("write_file", {"path": "new.py", "content": "value = 42\n"})
    if mixed:
        (ctx.workspace / "a.py").write_text("a = 9\n", encoding="utf-8")
        (ctx.workspace / "b.py").unlink()
    patch = candidate(ctx.workspace, ctx.base_commit)
    assert "new file mode" in patch.diff
    assert git_run(ctx.workspace, ["diff", "--cached", "--name-only"]).stdout == ""
    review = workflow.record_execution(
        ctx.task_id, "1 passed", {"passed": True, "tests_exit_code": 0},
        tested_tree=patch.tree,
    )
    workflow.approve_diff(ctx.task_id, review["review_sha256"], "tester", "approved")
    prepared = workflow.prepare_draft_pr(ctx.task_id, repository="test/fixture", base_branch="main",
                                         head_branch="agent/task", title="Test change")
    request_hash = prepared["request_sha256"]
    workflow.approve_publish(ctx.task_id, request_hash, "tester", "approved")
    calls = []

    def fake_git(repo, args, **kwargs):
        if args[0] == "push":
            calls.append(args)
            return subprocess.CompletedProcess(args, 0, stdout="")
        return git_run(repo, args, **kwargs)

    monkeypatch.setattr("core.github._git", fake_git)
    remote_pr = {}

    def create_draft_pr(repository, **kwargs):
        sha = git_run(ctx.workspace, ["rev-parse", "HEAD"]).stdout.strip()
        remote_pr.update({
            "draft": True, "number": 1, "html_url": "https://example.invalid/1",
            "head": {"sha": sha}, "base": {"ref": kwargs["base"]},
            "title": kwargs["title"], "body": kwargs["body"],
        })
        if mixed:
            raise GitHubError("response lost after remote write")
        return {**remote_pr, "commit": sha}

    api = SimpleNamespace(require_write_auth=lambda: None,
                          find_open_pull=lambda *args: remote_pr or None,
                          create_draft_pr=create_draft_pr)
    publisher = GitHubDraftPRPublisher(api)
    git_run(ctx.workspace, ["remote", "set-url", "--push", "origin", "https://github.com/test/other.git"])
    with pytest.raises(GitHubError, match="remote"):
        workflow.publish_draft_pr(ctx.task_id, request_hash, publisher)
    assert not calls
    git_run(ctx.workspace, ["remote", "set-url", "--push", "origin", "https://github.com/test/fixture.git"])
    (ctx.workspace / "extra.py").write_text("drift\n", encoding="utf-8")
    with pytest.raises(ValueError, match="已变化"):
        workflow.publish_draft_pr(ctx.task_id, request_hash, publisher)
    assert not calls
    (ctx.workspace / "extra.py").unlink()
    # Repeat approval after the deliberately invalidated publication request.
    review = workflow.record_execution(ctx.task_id, "1 passed", {"passed": True, "tests_exit_code": 0})
    workflow.approve_diff(ctx.task_id, review["review_sha256"], "tester", "approved")
    prepared = workflow.prepare_draft_pr(ctx.task_id, repository="test/fixture", base_branch="main",
                                         head_branch="agent/task", title="Test change")
    workflow.approve_publish(ctx.task_id, prepared["request_sha256"], "tester", "approved")
    final_hash = prepared["request_sha256"]
    if mixed:
        with pytest.raises(GitHubError, match="response lost"):
            workflow.publish_draft_pr(ctx.task_id, final_hash, publisher)
    else:
        complete = workflow.store.complete_operation
        with monkeypatch.context() as patcher:
            def fail_save(*args, **kwargs):
                raise RuntimeError("database write interrupted")
            patcher.setattr(workflow.store, "complete_operation", fail_save)
            with pytest.raises(RuntimeError, match="database write interrupted"):
                workflow.publish_draft_pr(ctx.task_id, final_hash, publisher)
        assert workflow.store.complete_operation == complete
    assert workflow.store.task(ctx.task_id)["status"] == "ready_to_publish"
    remote_pr["base"]["ref"] = "another-base"
    with pytest.raises(GitHubError, match="不同 base"):
        workflow.publish_draft_pr(ctx.task_id, final_hash, publisher)
    remote_pr["base"]["ref"] = "main"
    result = workflow.publish_draft_pr(ctx.task_id, final_hash, publisher)
    assert result["reused"] is True
    assert git_run(ctx.workspace, ["rev-parse", "HEAD^{tree}"]).stdout.strip() == patch.tree
    assert calls == [["push", "https://github.com/test/fixture.git",
                      result["commit"] + ":refs/heads/agent/task"]]


def test_fastapi_boundary_does_not_enter_real_publisher(monkeypatch):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    import service_api

    def forbidden(*args, **kwargs):
        raise AssertionError("Unauthorized request reached GitHub")

    monkeypatch.setattr(service_api, "github", forbidden)
    client = TestClient(service_api.app, base_url="http://127.0.0.1:8000")
    route = "/tasks/t/draft-pr/publish"
    payload = {"subject_hash": "unused", "confirmation": "create-draft-pr"}
    monkeypatch.delenv("AGENT_REAL_PUBLISHER", raising=False)
    assert client.post(route, json=payload).status_code == 403
    monkeypatch.setenv("AGENT_REAL_PUBLISHER", "1")
    assert client.post(route, json=payload, headers={"Host": "evil.example:8000"}).status_code == 403
    assert client.post(route, json=payload, headers={"Origin": "http://evil.example"}).status_code == 403
    assert client.post(route, json={"subject_hash": "unused"}).status_code == 409
    assert client.post(route, json=payload, headers={"Content-Length": "1048577"}).status_code == 413


def test_search_reflects_writes_and_excludes_external_links(tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "sample.py").write_text("old_signal = 1\n", encoding="utf-8")
    secret = tmp_path / "outside.py"
    secret.write_text("external_secret = 1\n", encoding="utf-8")
    try:
        (root / "linked.py").symlink_to(secret)
    except OSError:
        pass  # Windows installations may disallow creating symlinks.
    assert all("external_secret" not in item.text for item in chunk_repository(root))
    search = build_code_search(root)
    executor = make_executor(root, code_search=search)
    assert "old_signal" in executor("search_code", {"query": "old_signal"})
    executor("replace_text", {"path": "sample.py", "old": "old_signal", "new": "new_signal"})
    result = executor("search_code", {"query": "new_signal"})
    assert "new_signal" in result and "old_signal" not in result


def test_memory_window_skips_stale_records(tmp_path):
    workflow, ctx = _approved_task(tmp_path, ["a.py"])
    memory = workflow.memory
    for number in range(4):
        fact = f"old_signal = {number}"
        (ctx.workspace / "a.py").write_text(fact + "\n", encoding="utf-8")
        memory.remember_sourced_fact(ctx.task_id, fact, "a.py", fact)
    (ctx.workspace / "a.py").write_text("a = 2\n", encoding="utf-8")
    rendered = memory.render_context(ctx.task_id, limit=1)
    assert "已批准计划" in rendered and "old_signal" not in rendered
