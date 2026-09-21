from pathlib import Path
import json
import py_compile
from types import SimpleNamespace

import pytest

from benchmark.catalog import CASES
from benchmark.dashboard import render
from benchmark.dataset import materialize
from core.agent import run_agent
from core.sandbox import DockerLimits, DockerSandbox, DockerUnavailable, SandboxResult
from core.tools import ToolResult, build_tool_registry, make_executor


def test_catalog_has_20_plus_compilable_separated_cases(tmpdir):
    assert len(CASES) >= 20
    assert len({case.id for case in CASES}) == len(CASES)
    for case in CASES:
        root = Path(str(tmpdir)) / case.id
        paths = materialize(case, root)
        workspace, hidden = paths["workspace"], paths["hidden_tests"]
        assert not (workspace / "test_hidden.py").exists()
        assert not (hidden / "buggy.py").exists()
        py_compile.compile(str(workspace / "buggy.py"), doraise=True)
        py_compile.compile(str(workspace / "test_buggy.py"), doraise=True)
        py_compile.compile(str(hidden / "test_hidden.py"), doraise=True)


def test_agent_container_has_hardening_and_no_hidden_mount(tmpdir):
    workspace = Path(str(tmpdir)) / "workspace"
    workspace.mkdir()
    sandbox = DockerSandbox(limits=DockerLimits(cpus=0.5, memory="256m", pids=64))
    argv = sandbox.container_argv(workspace, ["python", "-m", "pytest", "-q"])
    joined = " ".join(argv)
    assert "--network none" in joined
    assert "--user 65532:65532" in joined
    assert "--cap-drop ALL" in joined
    assert "no-new-privileges" in joined
    assert "--memory 256m" in joined
    assert "--pids-limit 64" in joined
    assert "DEEPSEEK_API_KEY" not in joined
    assert "/hidden" not in joined


def test_hidden_verifier_mounts_both_inputs_read_only(tmpdir):
    root = Path(str(tmpdir))
    workspace, hidden = root / "workspace", root / "hidden"
    workspace.mkdir()
    hidden.mkdir()
    argv = DockerSandbox().container_argv(
        workspace, ["python", "-m", "pytest", "/hidden"],
        read_only_workspace=True, hidden_tests=hidden,
    )
    mounts = [argv[index + 1] for index, value in enumerate(argv[:-1]) if value == "--mount"]
    assert any("dst=/workspace,readonly" in mount for mount in mounts)
    assert any("dst=/hidden,readonly" in mount for mount in mounts)


def test_docker_command_runner_preserves_exit_code_metadata(tmpdir, monkeypatch):
    workspace = Path(str(tmpdir)) / "workspace"
    workspace.mkdir()
    sandbox = DockerSandbox()
    monkeypatch.setattr(sandbox, "run", lambda *args, **kwargs: SandboxResult(1, "1 failed"))
    result = sandbox.command_runner(workspace)(["python", "-m", "pytest", "-q"])
    assert isinstance(result, ToolResult)
    assert result.metadata["command_exit_code"] == 1
    assert result.metadata["command_succeeded"] is False


def test_missing_docker_never_falls_back(monkeypatch):
    monkeypatch.setattr("core.sandbox.shutil.which", lambda _: None)
    with pytest.raises(DockerUnavailable, match="禁止"):
        DockerSandbox().require_available()


def test_dashboard_aggregates_methods():
    base = {"test_tampered": False, "container_timeout": False, "elapsed_seconds": 1.0,
            "total_tokens": 100, "cost_rmb": 0.01, "agent_passed": True,
            "verification_output": ""}
    markdown = render([
        {**base, "case": "a", "method": "single", "tests_pass": True},
        {**base, "case": "b", "method": "single", "tests_pass": False},
        {**base, "case": "a", "method": "reviewer", "tests_pass": True},
    ])
    assert "single | 2 | 1 | 50.0%" in markdown
    assert "reviewer | 1 | 1 | 100.0%" in markdown


def test_agent_accepts_provider_message_without_tool_calls_attribute():
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="plain completion"))],
        usage=None,
        _request_id="req-test",
    )
    completions = SimpleNamespace(create=lambda **_: response)
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    result, _ = run_agent(
        client, "system", build_tool_registry("finish"), "task", max_steps=1,
    )
    assert result == "plain completion"


def test_agent_injects_final_step_terminal_reminder():
    calls = []

    def response(name, arguments, call_id):
        tool_call = SimpleNamespace(
            id=call_id,
            function=SimpleNamespace(name=name, arguments=json.dumps(arguments)),
        )
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(
                content=None, tool_calls=[tool_call]
            ))], usage=None, _request_id=call_id,
        )

    responses = [
        response("read_file", {"path": "sample.py"}, "read"),
        response("plan_finish", {
            "goal": "fix", "affected_files": ["sample.py"], "steps": ["edit"],
            "risks": [], "allowed_commands": [],
        }, "plan"),
    ]

    def create(**kwargs):
        calls.append(kwargs)
        return responses.pop(0)

    client = SimpleNamespace(chat=SimpleNamespace(
        completions=SimpleNamespace(create=create)
    ))
    result, _ = run_agent(
        client, "system", build_tool_registry("read_file", "plan_finish"),
        "task", max_steps=2, execute_tool=lambda name, args: "source",
        final_step_prompt="submit the plan now",
        final_step_tool_names={"plan_finish"},
    )
    assert result["goal"] == "fix"
    assert any(message.get("content") == "submit the plan now"
               for message in calls[1]["messages"] if isinstance(message, dict))
    assert [tool["function"]["name"] for tool in calls[1]["tools"]] == ["plan_finish"]


def test_benchmark_write_allowlist_protects_public_tests(tmpdir):
    root = Path(str(tmpdir))
    source = root / "buggy.py"
    public_test = root / "test_buggy.py"
    source.write_text("old source", encoding="utf-8")
    public_test.write_text("trusted test", encoding="utf-8")
    executor = make_executor(root, writable_files=["buggy.py"])

    denied = executor("write_file", {"path": "test_buggy.py", "content": "tampered"})
    allowed = executor("write_file", {"path": "buggy.py", "content": "fixed source"})

    assert "allowlist" in denied
    assert public_test.read_text(encoding="utf-8") == "trusted test"
    assert "已写入" in allowed
    assert source.read_text(encoding="utf-8") == "fixed source"
