import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from benchmark.analyze_real_results import analyze, classify_run, load_enriched_results
from benchmark.real_run import run_review_revise
from benchmark.protocol import build_agent_protocol, canonical_hash, git_state
from core.agent import run_agent
from core.symbols import read_symbol_source
from core.tools import ToolResult, build_tool_registry
from core.usage import Usage


def _tool_response(name, arguments, call_id):
    call = SimpleNamespace(
        id=call_id,
        function=SimpleNamespace(name=name, arguments=json.dumps(arguments)),
    )
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=None, tool_calls=[call]))],
        usage=None,
        _request_id=call_id,
    )


def _client(responses):
    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(
        create=lambda **_: responses.pop(0)
    )))


def _run_no_progress(choice, next_action=None):
    responses = [
        _tool_response("read_file", {"path": "pkg.py"}, "read-0"),
        _tool_response("progress_decision", {"choice": choice, "reason": "reason"}, "decision"),
    ]
    if next_action == "replace_text":
        responses.extend([
            _tool_response("replace_text", {"path": "pkg.py", "old": "bad", "new": "good"}, "edit"),
            _tool_response("finish", {"result": "done"}, "finish"),
        ])
    elif next_action == "read_symbol":
        responses.extend([
            _tool_response("read_symbol", {"path": "pkg.py", "symbol": "target"}, "targeted"),
            _tool_response("finish", {"result": "done"}, "finish"),
        ])
    trace, state = [], {}

    def executor(name, _args):
        if name == "replace_text":
            return "已更新 pkg.py（精确替换 1 处）"
        return "ok"

    result, _ = run_agent(
        _client(responses), "system",
        build_tool_registry(
            "read_file", "read_symbol", "replace_text", "progress_decision", "finish"
        ),
        "task", max_steps=5, execute_tool=executor, trace=trace,
        run_state=state, no_progress_after=1, no_progress_prompt="choose now",
    )
    return result, trace, state


@pytest.mark.parametrize("choice", ["abandon", "finish"])
def test_no_progress_terminal_choices_are_structured(choice):
    result, trace, state = _run_no_progress(choice)
    event = next(item for item in trace if item["action"] == "no_progress_intervention")
    assert result["no_progress_choice"] == choice
    assert event["choice"] == choice
    assert state["exit_reason"] == "no_progress"


@pytest.mark.parametrize(
    "choice,next_action", [("minimal_edit", "replace_text"), ("targeted_read", "read_symbol")]
)
def test_no_progress_continuing_choices_record_next_action(choice, next_action):
    result, trace, state = _run_no_progress(choice, next_action)
    event = next(item for item in trace if item["action"] == "no_progress_intervention")
    assert result == "done"
    assert event["choice"] == choice and event["next_action"] == next_action
    assert state["exit_reason"] == "finish"


def test_no_progress_does_not_trigger_before_threshold():
    trace, state = [], {}
    result, _ = run_agent(
        _client([_tool_response("finish", {"result": "done"}, "finish")]),
        "system", build_tool_registry("progress_decision", "finish"), "task",
        max_steps=2, execute_tool=lambda *_: "ok", trace=trace, run_state=state,
        no_progress_after=1,
    )
    assert result == "done"
    assert not any(item["action"] == "no_progress_intervention" for item in trace)
    assert state["exit_reason"] == "finish"


def test_controller_finishes_when_latest_patch_passes_pytest():
    responses = [
        _tool_response(
            "replace_text", {"path": "pkg.py", "old": "bad", "new": "good"}, "edit"
        ),
        _tool_response(
            "run_command", {"argv": ["python", "-m", "pytest", "-q"]}, "test"
        ),
    ]
    trace, state = [], {}

    def executor(name, _args):
        if name == "replace_text":
            return ToolResult("updated", "ok", {"mutation_applied": True})
        return ToolResult(
            "1 passed", "ok", {"command_exit_code": 0, "command_succeeded": True}
        )

    result, _ = run_agent(
        _client(responses), "system",
        build_tool_registry("replace_text", "run_command", "finish"),
        "task", max_steps=5, execute_tool=executor, trace=trace, run_state=state,
        auto_finish_after_verified_patch=True,
    )
    assert result["auto_finished"] is True
    assert state["exit_reason"] == "verified_patch"
    assert state["patch_state"]["latest_patch_verified"] is True
    assert state["budget_state"]["remaining_steps"] == 3
    assert trace[-1]["action"] == "controller_finish"


def test_controller_does_not_finish_on_test_before_or_failure_after_edit():
    responses = [
        _tool_response(
            "run_command", {"argv": ["python", "-m", "pytest", "-q"]}, "pre-test"
        ),
        _tool_response(
            "replace_text", {"path": "pkg.py", "old": "bad", "new": "good"}, "edit"
        ),
        _tool_response(
            "run_command", {"argv": ["python", "-m", "pytest", "-q"]}, "failed-test"
        ),
        _tool_response("finish", {"result": "manual"}, "finish"),
    ]

    def executor(name, _args):
        if name == "replace_text":
            return ToolResult("updated", "ok", {"mutation_applied": True})
        exit_code = 0 if _args.get("argv") and not hasattr(executor, "tested") else 1
        executor.tested = True
        return ToolResult("tests", "ok", {"command_exit_code": exit_code})

    state = {}
    result, _ = run_agent(
        _client(responses), "system",
        build_tool_registry("replace_text", "run_command", "finish"),
        "task", max_steps=5, execute_tool=executor, run_state=state,
        auto_finish_after_verified_patch=True,
    )
    assert result == "manual"
    assert state["exit_reason"] == "finish"
    assert state["patch_state"]["latest_patch_verified"] is False


def test_controller_rejects_collect_only_as_completion_evidence():
    responses = [
        _tool_response(
            "replace_text", {"path": "pkg.py", "old": "bad", "new": "good"}, "edit"
        ),
        _tool_response(
            "run_command",
            {"argv": ["python", "-m", "pytest", "--collect-only", "-q"]},
            "collect",
        ),
        _tool_response("finish", {"result": "manual"}, "finish"),
    ]

    def executor(name, _args):
        if name == "replace_text":
            return ToolResult("updated", "ok", {"mutation_applied": True})
        return ToolResult("collected", "ok", {"command_exit_code": 0})

    state = {}
    result, _ = run_agent(
        _client(responses), "system",
        build_tool_registry("replace_text", "run_command", "finish"),
        "task", max_steps=4, execute_tool=executor, run_state=state,
        auto_finish_after_verified_patch=True,
    )
    assert result == "manual"
    assert "completion_evidence" not in state


def test_patch_reviewer_can_trigger_one_bounded_revision():
    responses = [
        _tool_response(
            "review_finish", {"passed": False, "issues": ["preserve zero values"]}, "review"
        ),
        _tool_response(
            "replace_text", {"path": "pkg.py", "old": "if value:", "new": "if value is not None:"},
            "revise",
        ),
        _tool_response(
            "run_command", {"argv": ["python", "-m", "pytest", "-q"]}, "test"
        ),
    ]

    def executor(name, _args):
        if name == "replace_text":
            return ToolResult("updated", "ok", {"mutation_applied": True})
        if name == "run_command":
            return ToolResult("1 passed", "ok", {"command_exit_code": 0})
        return "ok"

    state = {"phases": {"code": {"exit_reason": "finish"}}}
    result, _, trace, review, revised = run_review_revise(
        _client(responses), "Repository: owner/repo\nIssue: keep zero",
        "--- a/pkg.py\n+++ b/pkg.py\n-if value is None:\n+if value:\n",
        "1 passed", executor, "test-model", Usage(), None, None,
        reviewer_steps=2, reviser_steps=3, run_state=state,
    )
    assert revised is True and review["passed"] is False
    assert result["auto_finished"] is True
    assert [item["phase"] for item in trace] == ["review", "revise", "revise", "revise"]
    assert state["exit_reason"] == "verified_patch"


def test_patch_reviewer_pass_skips_reviser():
    responses = [
        _tool_response("review_finish", {"passed": True, "issues": []}, "review")
    ]
    state = {"phases": {}}
    _, _, trace, review, revised = run_review_revise(
        _client(responses), "Repository: owner/repo\nIssue: fix",
        "--- a/pkg.py\n+++ b/pkg.py\n-old\n+new\n", "1 passed",
        lambda *_: "ok", "test-model", Usage(), None, None, run_state=state,
    )
    assert review == {"passed": True, "issues": []}
    assert revised is False and state["exit_reason"] == "review_passed"
    assert all(item["phase"] == "review" for item in trace)


def test_positive_review_normalizes_provider_omitted_empty_issues():
    responses = [_tool_response("review_finish", {"passed": True}, "review")]
    state = {"phases": {}}
    _, _, _, review, revised = run_review_revise(
        _client(responses), "Repository: owner/repo\nIssue: fix",
        "--- a/pkg.py\n+++ b/pkg.py\n-old\n+new\n", "",
        lambda *_: "ok", "test-model", Usage(), None, None, run_state=state,
    )
    assert review == {"passed": True, "issues": []}
    assert revised is False and state["exit_reason"] == "review_passed"


def test_incomplete_patch_review_never_triggers_reviser():
    responses = [
        _tool_response("read_file", {"path": "pkg.py"}, "read"),
        _tool_response("finish", {"result": "wrong terminal tool"}, "wrong"),
    ]
    calls = []

    def create(**kwargs):
        calls.append(kwargs)
        return responses.pop(0)

    client = SimpleNamespace(chat=SimpleNamespace(
        completions=SimpleNamespace(create=create)
    ))
    state = {"phases": {}}
    _, _, _, review, revised = run_review_revise(
        client, "Repository: owner/repo\nIssue: fix",
        "--- a/pkg.py\n+++ b/pkg.py\n-old\n+new\n", "",
        lambda *_: "ok", "test-model", Usage(), None, None,
        reviewer_steps=2, run_state=state,
    )
    assert revised is False
    assert review["incomplete"] is True
    assert state["exit_reason"] == "review_incomplete"
    assert [tool["function"]["name"] for tool in calls[-1]["tools"]] == ["review_finish"]


def test_progress_decision_tool_is_hidden_until_intervention():
    calls = []
    responses = [
        _tool_response("read_file", {"path": "pkg.py"}, "read"),
        _tool_response("progress_decision", {
            "choice": "finish", "reason": "enough investigation",
        }, "decision"),
    ]

    def create(**kwargs):
        calls.append(kwargs)
        return responses.pop(0)

    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    run_agent(
        client, "system", build_tool_registry("read_file", "progress_decision", "finish"),
        "task", max_steps=3, execute_tool=lambda *_: "ok", no_progress_after=1,
    )
    first = {item["function"]["name"] for item in calls[0]["tools"]}
    second = {item["function"]["name"] for item in calls[1]["tools"]}
    assert "progress_decision" not in first
    assert "progress_decision" in second


def test_targeted_read_choice_rejects_search_before_one_bounded_read():
    responses = [
        _tool_response("read_file", {"path": "pkg.py"}, "read-0"),
        _tool_response("progress_decision", {
            "choice": "targeted_read", "reason": "need the exact symbol",
        }, "decision"),
        _tool_response("search_code", {"query": "broad retry"}, "rejected-search"),
        _tool_response("read_symbol", {"path": "pkg.py", "symbol": "target"}, "bounded-read"),
        _tool_response("finish", {"result": "done"}, "finish"),
    ]
    executed = []

    def executor(name, _args):
        executed.append(name)
        return "ok"

    trace, state = [], {}
    result, _ = run_agent(
        _client(responses), "system",
        build_tool_registry(
            "search_code", "read_file", "read_symbol", "progress_decision", "finish"
        ),
        "task", max_steps=6, execute_tool=executor, trace=trace,
        run_state=state, no_progress_after=1,
    )
    event = next(item for item in trace if item["action"] == "no_progress_intervention")
    rejected = next(item for item in trace if item["action"] == "search_code")
    assert result == "done"
    assert "search_code" not in executed
    assert rejected["status"] == "rejected"
    assert event["rejected_next_actions"] == ["search_code"]
    assert event["next_action"] == "read_symbol"


def test_read_symbol_handles_qualified_nested_decorated_and_ambiguous(tmpdir):
    path = Path(str(tmpdir)) / "sample.py"
    path.write_text(
        "def deco(fn):\n    return fn\n\n"
        "class Service:\n"
        "    @deco\n    def run(self,\n            value):\n        return value\n\n"
        "def outer():\n    def run():\n        return 1\n    return run()\n\n"
        "def duplicate():\n    return 1\n\n"
        "def duplicate():\n    return 2\n",
        encoding="utf-8",
    )
    method = read_symbol_source(path, "Service.run", context_lines=0)
    assert method["status"] == "ok" and method["kind"] == "method"
    assert method["source"].startswith("5:     @deco")
    nested = read_symbol_source(path, "outer.run", context_lines=0)
    assert nested["status"] == "ok" and nested["kind"] == "nested_function"
    ambiguous = read_symbol_source(path, "duplicate")
    assert ambiguous["status"] == "ambiguous" and len(ambiguous["candidates"]) == 2
    selected = read_symbol_source(path, "duplicate", occurrence=1, context_lines=0)
    assert "return 2" in selected["source"]


def test_read_symbol_accepts_newer_positional_only_syntax_on_old_runtime(tmpdir):
    path = Path(str(tmpdir)) / "modern.py"
    path.write_text(
        "def outer(value, /):\n"
        "    def inner(item, /, *, flag=True):\n"
        "        return item\n"
        "    return inner(value)\n",
        encoding="utf-8",
    )
    result = read_symbol_source(path, "outer.inner", context_lines=0)
    assert result["status"] == "ok"
    assert "def inner(item, /, *, flag=True):" in result["source"]


def test_protocol_hash_changes_with_behavior_not_external_revision():
    kwargs = dict(
        strategy="single", model="model", prompts={"single": "prompt"},
        tool_schemas={"single": []}, max_steps=25, planner_steps=10,
        max_tokens=None, max_cost_rmb=None, search_budget=8,
        no_progress={"enabled": False, "after_llm_turns": None},
    )
    first = build_agent_protocol(**kwargs)
    assert canonical_hash(first) == canonical_hash(build_agent_protocol(**kwargs))
    changed = build_agent_protocol(**{**kwargs, "max_steps": 26})
    assert canonical_hash(first) != canonical_hash(changed)
    completion_changed = build_agent_protocol(**{
        **kwargs,
        "completion_policy": {"auto_finish_after_verified_patch": True},
    })
    assert canonical_hash(first) != canonical_hash(completion_changed)
    assert "code_revision" not in first


def test_git_state_distinguishes_generated_untracked_files_from_code_changes(tmpdir):
    root = Path(str(tmpdir)) / "repo"
    root.mkdir()
    subprocess.run(["git", "init", str(root)], check=True, capture_output=True)
    tracked = root / "tracked.txt"
    tracked.write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", "tracked.txt"], check=True)
    subprocess.run([
        "git", "-C", str(root), "-c", "user.name=Test", "-c",
        "user.email=test@example.invalid", "commit", "-m", "base",
    ], check=True, capture_output=True)
    (root / "result.jsonl").write_text("{}\n", encoding="utf-8")
    generated = git_state(root)
    assert generated["worktree_dirty"]
    assert not generated["tracked_worktree_dirty"]
    assert generated["untracked_files_present"]
    tracked.write_text("changed\n", encoding="utf-8")
    changed = git_state(root)
    assert changed["tracked_worktree_dirty"] and "tracked_diff_hash" in changed


def test_legacy_provenance_and_failure_summary_match_audit():
    rows = load_enriched_results()
    assert len(rows) == 11
    assert rows[0]["code_revision"] == "bc54a5d"
    assert rows[-1]["recorded_in_revision"] == "ef90451"
    assert all(row["protocol_inferred"] for row in rows)
    summary = {item["class"]: item for item in analyze(rows)}
    assert {label: summary[label]["runs"] for label in summary} == {
        "A": 3, "B": 4, "C": 2, "D": 1, "E": 1,
    }


def test_classifier_uses_successful_mutation_not_attempted_edit():
    base = {"tests_pass": False, "strategy": "single", "modified_files": []}
    assert classify_run({**base, "edit_attempts": 1, "successful_mutations": 0}) == "B"
    assert classify_run({**base, "modified_files": ["pkg.py"], "successful_mutations": 1}) == "C"
    assert classify_run({
        **base, "modified_files": ["pkg.py"], "successful_mutations": 1,
        "pass_to_pass_pass": False,
    }) == "E"
