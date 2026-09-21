import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from benchmark.analyze_real_results import analyze, classify_run, load_enriched_results
from benchmark.protocol import build_agent_protocol, canonical_hash
from core.agent import run_agent
from core.symbols import read_symbol_source
from core.tools import build_tool_registry


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
    assert "code_revision" not in first


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
