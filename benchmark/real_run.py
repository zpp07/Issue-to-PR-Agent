"""Run the current repair agent against the frozen real-repository set.

Reference fixes and regression-test patches stay evaluator-only. The agent sees
the issue and the unmodified base snapshot; hidden tests are injected only
after the agent finishes.
"""
from __future__ import annotations

import argparse
import difflib
import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile
import time

from benchmark.real_cases import RealCase, is_test_path, read_jsonl
from benchmark.real_snapshot import searchable_files, snapshot_directory
from benchmark.real_verify import (
    _apply, _pass_to_pass_command, _prepare_fixture_bridge, _pytest_command,
)
from benchmark.protocol import (
    TRACE_SCHEMA_VERSION,
    build_agent_protocol,
    build_evaluation_protocol,
    git_state,
    protocol_record,
)
from core.agent import run_agent
from core.client import make_client
from core.policy import CommandPolicy
from core.sandbox import DockerLimits, DockerSandbox
from core.search import HybridCodeSearch, format_hits
from core.tools import build_tool_registry, make_executor
from core.usage import Usage


TOOLS = build_tool_registry(
    "search_code", "read_file", "read_symbol", "replace_text", "write_file",
    "run_command", "finish"
)
NO_PROGRESS_TOOLS = build_tool_registry(
    "search_code", "read_file", "read_symbol", "replace_text", "write_file",
    "run_command", "progress_decision", "finish"
)
PLANNER_TOOLS = build_tool_registry("search_code", "read_file", "read_symbol", "plan_finish")
EXECUTOR_TOOLS = build_tool_registry(
    "read_file", "read_symbol", "replace_text", "write_file", "run_command", "finish"
)
PATCH_REVIEWER_TOOLS = build_tool_registry("read_file", "read_symbol", "review_finish")
REVISER_TOOLS = build_tool_registry(
    "read_file", "read_symbol", "replace_text", "write_file", "run_command", "finish"
)
SEARCH_BUDGET = 8
NO_PROGRESS_PROMPT_TEMPLATE = """Investigation has completed {turns} decision turns without a successful
production-code edit. Call progress_decision now and choose exactly one option:
minimal_edit, targeted_read, abandon, or finish. Explain the missing evidence or
reason. If you choose minimal_edit or targeted_read, perform only that focused
next action on the following turn; do not resume broad searching.
"""


def no_progress_prompt(turns: int) -> str:
    return NO_PROGRESS_PROMPT_TEMPLATE.format(turns=turns)
PROMPT = """You are repairing a real Python repository from a GitHub issue.
Use search_code once or twice to locate relevant implementation and contract
evidence. Its citations contain line numbers: inspect those regions with
read_symbol or bounded read_file calls rather than repeatedly searching or reading a
large file from its beginning. Search has a hard budget of eight calls; after
you identify a plausible implementation, move to a minimal edit instead of
trying many query variants. Prefer replace_text for small, exact edits. You
may edit existing production Python files only. Never edit tests, documentation,
generated benchmark data, dependency files, or scratch files; attempts will be
rejected. run_command accepts pytest and ruff only, not python -c or shell
commands. Repository commands run offline in an isolated container. Run focused
tests when practical. Once the focused tests pass, stop investigating and call
finish with a concise summary of the fix.
"""
PLANNER_PROMPT = """You are the read-only investigation phase of a Python repair agent.
Use repository evidence to identify the most likely root cause and the smallest
credible fix. You may search and read, but cannot edit or run commands. Do not
keep searching for certainty: inspect the relevant implementation, then call
plan_finish with concrete affected files, symbols/regions, edit steps, risks,
and focused pytest commands. Never mention or assume hidden evaluator tests.
"""
EXECUTOR_PROMPT = """You are the execution phase of a Python repair agent.
Implement the approved evidence-based plan. Search is intentionally unavailable:
use bounded read_file calls to confirm exact text, make minimal changes with
replace_text, and run focused pytest commands. You may edit existing production
Python files only. Never edit tests, documentation, dependencies, or scratch
files. Once focused tests pass, call finish instead of continuing investigation.
"""
PATCH_REVIEWER_PROMPT = """You are a read-only patch reviewer for a Python repair agent.
Review the candidate patch against the issue, existing implementation, and visible
test evidence. Look for incorrect assumptions, incomplete call-site changes, edge
cases, and regressions. Never assume or request hidden tests. Use read_file or
read_symbol only when the supplied patch lacks necessary context. Finish with
review_finish. Set passed=true only when there is no concrete issue; otherwise
return a short list of actionable issues grounded in visible evidence.
"""
REVISER_PROMPT = """You are the single bounded revision pass of a Python repair agent.
Address only the reviewer's concrete issues while preserving correct parts of the
candidate patch. Read exact symbols as needed, make the smallest credible edit,
and run a focused visible pytest command. Never edit tests or infer hidden tests.
Once the latest edit passes pytest, the controller will stop automatically.
"""


class EvaluationInfrastructureError(RuntimeError):
    def __init__(self, message: str, retryable: bool):
        super().__init__(message)
        self.retryable = retryable


def load_manifest(manifest_path: str | Path,
                  cases_path: str | Path) -> list[tuple[RealCase, str]]:
    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    if manifest.get("status") != "tests_verified":
        raise ValueError("real benchmark manifest is not tests_verified")
    cases = {case.instance_id: case for case in read_jsonl(cases_path)}
    selected = []
    seen = set()
    for row in manifest.get("cases", []):
        instance_id, image = row.get("instance_id"), row.get("image")
        if not instance_id or not image or instance_id in seen:
            raise ValueError("manifest cases require unique instance_id and image")
        if instance_id not in cases:
            raise ValueError(f"manifest case is absent from selected cases: {instance_id}")
        seen.add(instance_id)
        selected.append((cases[instance_id], image))
    if not selected:
        raise ValueError("real benchmark manifest has no cases")
    return selected


def editable_python_files(workspace: str | Path) -> list[str]:
    return [path for path in searchable_files(workspace)
            if path.endswith(".py") and not is_test_path(path)]


def initial_evidence(search: HybridCodeSearch, issue: str, file_limit: int = 3):
    """Keep the initial prompt focused: one best chunk from each selected file."""
    selected, paths = [], set()
    for hit in search.search(issue, top_k=10, candidate_k=50):
        if hit.chunk.path in paths:
            continue
        selected.append(hit)
        paths.add(hit.chunk.path)
        if len(selected) >= file_limit:
            break
    return selected


def compact_trace(trace: list[dict]) -> list[dict]:
    """Persist navigation decisions without copying source/edit contents."""
    compact = []
    for step in trace:
        args = step.get("args") or {}
        row = {"phase": step.get("phase", "single"),
               "step": step.get("step"), "action": step.get("action")}
        for key in ("path", "start_line", "end_line", "argv"):
            if key in args:
                row[key] = args[key]
        if "query" in args:
            row["query"] = str(args["query"])[:200]
        if "result_chars" in step:
            row["result_chars"] = step["result_chars"]
        if "result_preview" in step:
            row["result_preview"] = step["result_preview"]
        for key in (
            "status", "mutation_applied", "threshold", "choice", "reason", "next_action",
            "patch_revision", "remaining_steps",
        ):
            if key in step:
                row[key] = step[key]
        compact.append(row)
    return compact


def with_search_budget(executor, max_calls: int = 8):
    """Stop unproductive search loops while leaving reads and edits available."""
    calls = 0

    def budgeted(name, args):
        nonlocal calls
        if name == "search_code":
            calls += 1
            if calls > max_calls:
                return ("代码检索预算已耗尽。请使用已有引用按行读取相关文件，"
                        "提出最小修改并运行聚焦测试。")
        return executor(name, args)

    return budgeted


def generate_report(results_path: str | Path, report_path: str | Path) -> None:
    source = Path(results_path)
    rows = ([json.loads(line) for line in source.read_text(encoding="utf-8").splitlines()
             if line.strip()] if source.is_file() else [])
    passed = sum(bool(row["tests_pass"]) for row in rows)
    tokens = sum(row["total_tokens"] for row in rows)
    cost = sum(row["cost_rmb"] for row in rows)
    elapsed = sum(row["elapsed_seconds"] for row in rows)
    lines = [
        "# Real-repository benchmark results", "",
        f"- Runs: {len(rows)}", f"- Passed: {passed}/{len(rows)}" if rows else "- Passed: 0/0",
        f"- Tokens: {tokens:,}", f"- Estimated cost: RMB {cost:.4f}",
        f"- Cumulative runtime: {elapsed / 60:.1f} minutes", "",
        "| Case | Protocol | Trial | Passed | Completed | Modified files | Gold recall | Tokens | Cost (RMB) |",
        "|---|---|---:|---:|---:|---|---:|---:|---:|",
    ]
    for row in rows:
        modified = ", ".join(f"`{path}`" for path in row["modified_files"]) or "-"
        lines.append(
            f"| `{row['case']}` | `{row.get('protocol_version', row.get('strategy', 'single'))}` | {row['trial']} | "
            f"{str(row['tests_pass']).lower()} | "
            f"{str(row['agent_completed']).lower()} | {modified} | "
            f"{row['gold_file_recall']:.0%} | {row['total_tokens']:,} | {row['cost_rmb']:.4f} |"
        )
    destination = Path(report_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _fingerprints(workspace: Path, paths: list[str]) -> dict[str, str]:
    return {path: hashlib.sha256((workspace / path).read_bytes()).hexdigest()
            for path in paths}


def _agent_patch(source: Path, workspace: Path, modified: list[str],
                 limit: int = 20_000) -> str:
    chunks = []
    for relative in modified:
        before = (source / relative).read_text(encoding="utf-8").splitlines(keepends=True)
        after = (workspace / relative).read_text(encoding="utf-8").splitlines(keepends=True)
        chunks.extend(difflib.unified_diff(
            before, after, fromfile=f"a/{relative}", tofile=f"b/{relative}",
        ))
        if sum(len(chunk) for chunk in chunks) >= limit:
            break
    patch = "".join(chunks)
    if len(patch) > limit:
        patch = patch[:limit] + f"\n...[agent patch truncated at {limit} characters]"
    return patch


def _prepare(case: RealCase, workspace: Path, sandbox: DockerSandbox) -> None:
    if case.repo != "facebookresearch/hydra":
        return
    result = sandbox.run(workspace, ["python", "setup.py", "antlr"], timeout=120)
    if result.exit_code != 0:
        raise RuntimeError(f"Hydra parser generation failed: {result.output}")


def _phase(trace: list[dict], name: str) -> list[dict]:
    for step in trace:
        step["phase"] = name
    return trace


def run_repair(client, strategy: str, task: str, executor, model: str,
               max_steps: int, planner_steps: int,
               max_tokens: int | None, max_cost_rmb: float | None = None,
               no_progress_after: int | None = None, run_state: dict | None = None,
               auto_finish_after_verified_patch: bool = True,
               ) -> tuple[object, Usage, list[dict], bool | None, dict | None]:
    usage = Usage()
    if run_state is None:
        run_state = {}
    if strategy in {"single", "review_revise"}:
        trace: list[dict] = []
        phase_state: dict = {}
        single_tools = NO_PROGRESS_TOOLS if no_progress_after is not None else TOOLS
        result, usage = run_agent(
            client, PROMPT, single_tools, task, model=model, max_steps=max_steps,
            execute_tool=with_search_budget(executor, SEARCH_BUDGET), usage=usage,
            trace=trace, max_tokens=max_tokens, max_cost_rmb=max_cost_rmb,
            run_state=phase_state, no_progress_after=no_progress_after,
            no_progress_prompt=(
                no_progress_prompt(no_progress_after) if no_progress_after is not None else None
            ),
            auto_finish_after_verified_patch=auto_finish_after_verified_patch,
        )
        run_state.update(phase_state)
        phase_name = "code" if strategy == "review_revise" else "single"
        run_state["phases"] = {phase_name: dict(phase_state)}
        return result, usage, _phase(trace, phase_name), None, None

    planner_trace: list[dict] = []
    planner_state: dict = {}
    plan, usage = run_agent(
        client, PLANNER_PROMPT, PLANNER_TOOLS, task, model=model,
        max_steps=planner_steps, execute_tool=with_search_budget(executor, SEARCH_BUDGET),
        usage=usage, trace=planner_trace, max_tokens=max_tokens,
        max_cost_rmb=max_cost_rmb, run_state=planner_state,
        final_step_prompt=(
            "This is the final planning step. Do not search or read again. "
            "Call plan_finish now using the strongest evidence already collected."
        ),
        final_step_tool_names={"plan_finish"},
    )
    planner_trace = _phase(planner_trace, "plan")
    planner_completed = isinstance(plan, dict) and isinstance(plan.get("steps"), list)
    if not planner_completed:
        run_state["exit_reason"] = "planner_incomplete"
        run_state["phases"] = {"plan": planner_state}
        return plan, usage, planner_trace, False, None

    execution_trace: list[dict] = []
    execution_state: dict = {}
    execution_task = (
        task.split("\n\nInitial retrieved evidence:", 1)[0]
        + "\n\nApproved repair plan:\n"
        + json.dumps(plan, ensure_ascii=False, indent=2)
    )
    result, usage = run_agent(
        client, EXECUTOR_PROMPT, EXECUTOR_TOOLS, execution_task,
        model=model, max_steps=max_steps, execute_tool=executor,
        usage=usage, trace=execution_trace, max_tokens=max_tokens,
        max_cost_rmb=max_cost_rmb, run_state=execution_state,
        auto_finish_after_verified_patch=auto_finish_after_verified_patch,
    )
    run_state["exit_reason"] = execution_state.get("exit_reason", "unknown")
    run_state["phases"] = {"plan": planner_state, "execute": execution_state}
    return result, usage, planner_trace + _phase(execution_trace, "execute"), True, plan


def run_review_revise(client, task: str, candidate_patch: str, visible_test_evidence: str,
                      executor, model: str, usage: Usage, max_tokens: int | None,
                      max_cost_rmb: float | None, reviewer_steps: int = 6,
                      reviser_steps: int = 10,
                      auto_finish_after_verified_patch: bool = True,
                      run_state: dict | None = None,
                      ) -> tuple[object, Usage, list[dict], dict, bool]:
    """Review one candidate patch and allow at most one bounded revision.

    Hidden tests and gold patches are deliberately absent.  This keeps the
    independent evaluator valid while still testing whether a separate reviewer
    catches semantic or regression risks from the issue, diff, and visible tests.
    """
    if run_state is None:
        run_state = {}
    review_trace: list[dict] = []
    review_state: dict = {}
    review_task = (
        task.split("\n\nInitial retrieved evidence:", 1)[0]
        + "\n\nCandidate patch produced by the coder:\n"
        + candidate_patch
        + "\n\nVisible test evidence (may be empty):\n"
        + (visible_test_evidence or "No visible pytest result was recorded.")
    )
    review, usage = run_agent(
        client, PATCH_REVIEWER_PROMPT, PATCH_REVIEWER_TOOLS, review_task,
        model=model, max_steps=reviewer_steps, execute_tool=executor,
        usage=usage, trace=review_trace, max_tokens=max_tokens,
        max_cost_rmb=max_cost_rmb, run_state=review_state,
        final_step_prompt=(
            "This is the final review step. Call review_finish now using only visible evidence."
        ),
        final_step_tool_names={"review_finish"},
    )
    if review_state.get("exit_reason") == "llm_error":
        latest = next(
            (step for step in reversed(review_trace) if step.get("action") == "llm_error"), {}
        )
        raise EvaluationInfrastructureError(
            latest.get("error", "Reviewer LLM failure"), bool(latest.get("retryable"))
        )
    normalized_review = dict(review) if isinstance(review, dict) else {}
    if normalized_review.get("passed") is True and "issues" not in normalized_review:
        # Some OpenAI-compatible providers omit a required empty array.  This
        # normalization is unambiguous only for a positive review; a negative
        # review without actionable issues remains incomplete.
        normalized_review["issues"] = []
    review_valid = bool(
        isinstance(normalized_review.get("passed"), bool)
        and isinstance(normalized_review.get("issues"), list)
    )
    structured_review = normalized_review if review_valid else {
        "passed": False,
        "issues": [],
        "incomplete": True,
        "reason": "Reviewer did not return a structured review_finish conclusion.",
    }
    issues = [str(item) for item in structured_review.get("issues") or []]
    passed = bool(structured_review.get("passed")) and not issues
    phases = run_state.setdefault("phases", {})
    phases["review"] = dict(review_state)
    if not review_valid or (not passed and not issues):
        run_state["exit_reason"] = "review_incomplete"
        return review, usage, _phase(review_trace, "review"), structured_review, False
    if passed:
        run_state["exit_reason"] = "review_passed"
        return review, usage, _phase(review_trace, "review"), structured_review, False

    revision_trace: list[dict] = []
    revision_state: dict = {}
    revision_task = (
        task.split("\n\nInitial retrieved evidence:", 1)[0]
        + "\n\nCandidate patch:\n"
        + candidate_patch
        + "\n\nReviewer issues:\n"
        + "\n".join(f"- {issue}" for issue in issues)
    )
    revised, usage = run_agent(
        client, REVISER_PROMPT, REVISER_TOOLS, revision_task,
        model=model, max_steps=reviser_steps, execute_tool=executor,
        usage=usage, trace=revision_trace, max_tokens=max_tokens,
        max_cost_rmb=max_cost_rmb, run_state=revision_state,
        auto_finish_after_verified_patch=auto_finish_after_verified_patch,
        final_step_prompt=(
            "This is the final revision step. Run the focused pytest command if needed, "
            "then call finish; do not start another investigation."
        ),
        final_step_tool_names={"finish"},
    )
    if revision_state.get("exit_reason") == "llm_error":
        latest = next(
            (step for step in reversed(revision_trace) if step.get("action") == "llm_error"), {}
        )
        raise EvaluationInfrastructureError(
            latest.get("error", "Reviser LLM failure"), bool(latest.get("retryable"))
        )
    phases["revise"] = dict(revision_state)
    run_state["exit_reason"] = revision_state.get("exit_reason", "revision_incomplete")
    return (
        revised, usage,
        _phase(review_trace, "review") + _phase(revision_trace, "revise"),
        structured_review, True,
    )


def evaluate_one(case: RealCase, image: str, cache_root: str | Path,
                 docker_binary: str, model: str, trial: int,
                 max_steps: int, max_tokens: int | None,
                 strategy: str = "single", planner_steps: int = 10,
                  max_cost_rmb: float | None = None,
                  no_progress_after: int | None = None,
                  provenance: dict | None = None,
                  auto_finish_after_verified_patch: bool = True,
                  reviewer_steps: int = 6, reviser_steps: int = 10) -> dict:
    started = time.perf_counter()
    source = snapshot_directory(cache_root, case)
    if not source.is_dir():
        raise FileNotFoundError(f"snapshot is not prepared: {source}")
    runs = Path(cache_root) / "agent_runs"
    runs.mkdir(parents=True, exist_ok=True)
    run_root = Path(tempfile.mkdtemp(
        prefix=f"{case.instance_id}-{strategy}-t{trial}-", dir=str(runs)
    ))
    workspace = run_root / "workspace"
    try:
        shutil.copytree(source, workspace)
        sandbox = DockerSandbox(
            image=image, docker_binary=docker_binary,
            limits=DockerLimits(cpus=2.0, memory="2g", pids=256, timeout=300),
        )
        _prepare(case, workspace, sandbox)
        editable = editable_python_files(workspace)
        test_paths = [path for path in searchable_files(workspace) if is_test_path(path)]
        before_tests = _fingerprints(workspace, test_paths)
        before_editable = _fingerprints(workspace, editable)
        search = HybridCodeSearch(workspace)
        initial_hits = initial_evidence(search, case.issue)
        task = (
            f"Repository: {case.repo}\nIssue:\n{case.issue}\n\n"
            "Initial retrieved evidence:\n" + format_hits(initial_hits)
        )
        executor = make_executor(
            workspace, policy=CommandPolicy(),
            command_runner=sandbox.command_runner(workspace),
            writable_files=editable, code_search=search,
        )
        run_state: dict = {}
        client = make_client()
        result, usage, trace, planner_completed, repair_plan = run_repair(
            client, strategy, task, executor, model, max_steps,
            planner_steps, max_tokens, max_cost_rmb, no_progress_after, run_state,
            auto_finish_after_verified_patch,
        )
        llm_errors = [step for step in trace if step.get("action") == "llm_error"]
        if llm_errors:
            # Provider/runtime failures are missing observations, not model
            # failures, and must never become scored rows.
            latest = llm_errors[-1]
            raise EvaluationInfrastructureError(
                latest.get("error", "LLM failure"), bool(latest.get("retryable"))
            )
        review = None
        revision_attempted = False
        if strategy == "review_revise":
            candidate_modified = sorted(
                path for path in editable
                if before_editable[path] != _fingerprints(workspace, [path])[path]
            )
            candidate_patch = _agent_patch(source, workspace, candidate_modified)
            visible_tests = "\n".join(
                str(step.get("result_preview", ""))
                for step in trace if step.get("action") == "run_command"
            )[-2000:]
            if candidate_patch:
                result, usage, extra_trace, review, revision_attempted = run_review_revise(
                    client, task, candidate_patch, visible_tests, executor, model, usage,
                    max_tokens, max_cost_rmb, reviewer_steps, reviser_steps,
                    auto_finish_after_verified_patch, run_state,
                )
                trace.extend(extra_trace)
            else:
                review = {"passed": False, "issues": ["Coder produced no candidate patch."],
                          "skipped": True}
                run_state["exit_reason"] = "no_candidate_patch"
        agent_completed = run_state.get("exit_reason") in {
            "finish", "verified_patch", "review_passed",
        }

        after_tests = _fingerprints(workspace, test_paths)
        after_editable = _fingerprints(workspace, editable)
        modified = sorted(path for path in editable
                          if before_editable[path] != after_editable[path])
        agent_patch = _agent_patch(source, workspace, modified)
        test_tampered = before_tests != after_tests

        # Evaluator-only material enters the workspace only after the agent is done.
        _apply(workspace, case.test_patch)
        _prepare_fixture_bridge(case, workspace)
        command, selected_pass_count = _pytest_command(case, workspace, pass_sample=5)
        verification = sandbox.run(
            workspace, command, read_only_workspace=True, timeout=300,
        )
        pass_to_pass_pass = verification.exit_code == 0
        pass_to_pass_output = "covered by combined passing verification"
        if verification.exit_code != 0 and selected_pass_count:
            pass_command, _ = _pass_to_pass_command(case, workspace, pass_sample=5)
            pass_verification = sandbox.run(
                workspace, pass_command, read_only_workspace=True, timeout=300,
            )
            pass_to_pass_pass = pass_verification.exit_code == 0
            pass_to_pass_output = pass_verification.output[-1000:]
        row = {
            "case": case.instance_id, "repo": case.repo, "strategy": strategy,
            "trial": trial,
            "model": model, "image": image, "agent_completed": agent_completed,
            "planner_completed": planner_completed,
            "repair_plan": repair_plan,
            "review": review,
            "revision_attempted": revision_attempted,
            "tests_pass": verification.exit_code == 0,
            "test_tampered": test_tampered, "container_timeout": verification.timed_out,
            "modified_files": modified, "gold_files": list(case.production_files),
            "agent_patch": agent_patch,
            "gold_file_recall": (len(set(modified).intersection(case.production_files)) /
                                 len(case.production_files)),
            "fail_to_pass_count": len(case.fail_to_pass),
            "pass_to_pass_sample_count": selected_pass_count,
            "pass_to_pass_pass": pass_to_pass_pass,
            "regression_detected": not pass_to_pass_pass,
            "pass_to_pass_output": pass_to_pass_output,
            "prompt_tokens": usage.prompt_tokens,
            "completion_tokens": usage.completion_tokens,
            "total_tokens": usage.total_tokens, "calls": usage.calls,
            "cost_rmb": round(usage.cost_rmb(), 6),
            "elapsed_seconds": round(time.perf_counter() - started, 3),
            "trace_steps": len(trace),
            "trace_schema_version": TRACE_SCHEMA_VERSION,
            "exit_reason": run_state.get("exit_reason", "unknown"),
            "run_state": run_state,
            "completion_evidence": run_state.get("completion_evidence"),
            "trace": compact_trace(trace),
            "search_calls": 1 + sum(step.get("action") == "search_code" for step in trace),
            "initial_search_calls": 1,
            "search_tool_calls": sum(
                step.get("action") == "search_code" for step in trace
            ),
            "search_executed": sum(
                step.get("action") == "search_code" and step.get("status") == "ok"
                for step in trace
            ),
            "search_blocked": sum(
                step.get("action") == "search_code" and step.get("status") == "blocked"
                for step in trace
            ),
            "read_calls": sum(step.get("action") == "read_file" for step in trace),
            "edit_attempts": sum(
                step.get("action") in {"replace_text", "write_file"} for step in trace
            ),
            "successful_mutations": sum(
                bool(step.get("mutation_applied")) for step in trace
            ),
            "rejected_mutations": sum(
                step.get("action") in {"replace_text", "write_file"}
                and step.get("status") in {"rejected", "error"}
                for step in trace
            ),
            "verification_output": verification.output[-1000:],
        }
        if provenance:
            row.update(provenance)
        return row
    finally:
        shutil.rmtree(run_root, ignore_errors=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", default="benchmark/real/verified_manifest.json")
    parser.add_argument("--cases-file", default="benchmark/real/selected.jsonl")
    parser.add_argument("--cache-root", default=".local/real_benchmark")
    parser.add_argument("--cases", default="all", help="all or comma-separated instance ids")
    parser.add_argument("--trials", type=int, default=3)
    parser.add_argument("--model", default="deepseek-chat")
    parser.add_argument("--docker-binary", default="docker")
    parser.add_argument("--max-steps", type=int, default=25)
    parser.add_argument("--planner-steps", type=int, default=10)
    parser.add_argument(
        "--strategy", choices=("single", "plan_execute", "review_revise"), default="single"
    )
    parser.add_argument("--reviewer-steps", type=int, default=6)
    parser.add_argument("--reviser-steps", type=int, default=10)
    parser.add_argument("--max-tokens", type=int)
    parser.add_argument("--max-cost-rmb", type=float)
    parser.add_argument(
        "--no-progress-after", type=int, default=0,
        help="inject the structured no-progress decision after N edit-free LLM turns; 0 disables",
    )
    finish_group = parser.add_mutually_exclusive_group()
    finish_group.add_argument(
        "--auto-finish-verified-patch", dest="auto_finish_verified_patch",
        action="store_true", default=True,
        help="let the controller stop after the latest mutation passes a pytest command",
    )
    finish_group.add_argument(
        "--no-auto-finish-verified-patch", dest="auto_finish_verified_patch",
        action="store_false",
        help="require the model to call finish even after a verified mutation",
    )
    parser.add_argument("--protocol-dir", default="benchmark/real/protocols")
    parser.add_argument(
        "--freeze-protocol-only", action="store_true",
        help="write the immutable protocol manifest and exit without creating a client or running cases",
    )
    parser.add_argument("--output", default="benchmark/real/results.jsonl")
    parser.add_argument("--report", help="default: output path with .md suffix")
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    if (args.trials < 1 or args.max_steps < 1 or args.planner_steps < 1
            or args.reviewer_steps < 1 or args.reviser_steps < 1
            or args.no_progress_after < 0):
        parser.error("trials and all stage step budgets must be positive")

    selected = load_manifest(args.manifest, args.cases_file)

    no_progress_after = args.no_progress_after or None
    active_prompts = {"single": PROMPT}
    active_tools = {"single": (
        NO_PROGRESS_TOOLS.schemas() if no_progress_after is not None else TOOLS.schemas()
    )}
    if args.strategy == "plan_execute":
        active_prompts = {
            "planner": PLANNER_PROMPT,
            "planner_final_step": (
                "This is the final planning step. Do not search or read again. "
                "Call plan_finish now using the strongest evidence already collected."
            ),
            "executor": EXECUTOR_PROMPT,
        }
        active_tools = {"planner": PLANNER_TOOLS.schemas(), "executor": EXECUTOR_TOOLS.schemas()}
    elif args.strategy == "review_revise":
        active_prompts = {
            "coder": PROMPT,
            "reviewer": PATCH_REVIEWER_PROMPT,
            "reviewer_final_step": (
                "This is the final review step. Call review_finish now using only visible evidence."
            ),
            "reviser": REVISER_PROMPT,
            "reviser_final_step": (
                "This is the final revision step. Run the focused pytest command if needed, "
                "then call finish; do not start another investigation."
            ),
        }
        active_tools = {
            "coder": NO_PROGRESS_TOOLS.schemas() if no_progress_after else TOOLS.schemas(),
            "reviewer": PATCH_REVIEWER_TOOLS.schemas(),
            "reviser": REVISER_TOOLS.schemas(),
        }
    elif no_progress_after is not None:
        active_prompts["no_progress"] = no_progress_prompt(no_progress_after)
    definition = build_agent_protocol(
        strategy=args.strategy, model=args.model, prompts=active_prompts,
        tool_schemas=active_tools, max_steps=args.max_steps,
        planner_steps=args.planner_steps, max_tokens=args.max_tokens,
        max_cost_rmb=args.max_cost_rmb, search_budget=SEARCH_BUDGET,
        no_progress={
            "enabled": no_progress_after is not None,
            "after_llm_turns": no_progress_after,
            "max_triggers": 1,
            "applies_to": "single-before-first-successful-mutation",
            "decision_tool_visibility": "only-after-trigger",
            "next_action_enforcement": {
                "minimal_edit": ["replace_text", "write_file"],
                "targeted_read": ["read_file", "read_symbol"],
            },
        },
        completion_policy={
            "auto_finish_after_verified_patch": args.auto_finish_verified_patch,
            "requires_successful_mutation": True,
            "accepted_verification": "pytest-exit-zero-after-latest-mutation",
            "hidden_tests_used_for_control": False,
            "terminal_tool_enforcement": {
                "planner": ["plan_finish"],
                "reviewer": ["review_finish"],
                "reviser": ["finish"],
            },
            "review_output_normalization": (
                "missing-issues-is-empty-only-when-passed-true"
                if args.strategy == "review_revise" else "not-applicable"
            ),
        },
        stage_budgets={
            "coder_or_executor_steps": args.max_steps,
            "planner_steps": args.planner_steps if args.strategy == "plan_execute" else 0,
            "reviewer_steps": args.reviewer_steps if args.strategy == "review_revise" else 0,
            "reviser_steps": args.reviser_steps if args.strategy == "review_revise" else 0,
            "max_revision_passes": 1 if args.strategy == "review_revise" else 0,
        },
    )
    finish_mode = "verified-auto" if args.auto_finish_verified_patch else "model-finish"
    version = f"{args.strategy}-v5-{finish_mode}-{'np9' if no_progress_after == 9 else 'np-custom' if no_progress_after else 'np-off'}"
    agent_protocol = protocol_record(definition, version)
    evaluation_protocol = build_evaluation_protocol(
        manifest_path=args.manifest, cases_path=args.cases_file,
        docker_images=[image for _, image in selected],
    )
    repo_root = Path(__file__).resolve().parents[1]
    provenance = {
        "protocol_version": agent_protocol["protocol_version"],
        "protocol_hash": agent_protocol["protocol_hash"],
        **evaluation_protocol,
        **git_state(repo_root),
    }
    protocol_dir = Path(args.protocol_dir)
    protocol_dir.mkdir(parents=True, exist_ok=True)
    protocol_path = protocol_dir / (
        f"{agent_protocol['protocol_hash']}-{evaluation_protocol['evaluation_protocol_hash']}.json"
    )
    if not protocol_path.exists():
        protocol_path.write_text(
            json.dumps({**agent_protocol, **evaluation_protocol}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    if args.cases != "all":
        requested = {item.strip() for item in args.cases.split(",") if item.strip()}
        available = {case.instance_id for case, _ in selected}
        unknown = requested - available
        if unknown:
            parser.error(f"cases are not in verified manifest: {', '.join(sorted(unknown))}")
        selected = [(case, image) for case, image in selected if case.instance_id in requested]

    if args.freeze_protocol_only:
        print(f"protocol_version={agent_protocol['protocol_version']}")
        print(f"protocol_hash={agent_protocol['protocol_hash']}")
        print(f"protocol_path={protocol_path}")
        return

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    completed = set()
    if args.resume and output.is_file():
        completed = {(row["case"], row.get("strategy", "single"),
                      row["trial"], row["model"], row.get("protocol_hash"))
                     for row in (json.loads(line) for line in
                                 output.read_text(encoding="utf-8").splitlines()) if row}
    failures = []
    with output.open("a", encoding="utf-8") as stream:
        for case, image in selected:
            for trial in range(1, args.trials + 1):
                if (case.instance_id, args.strategy, trial, args.model,
                        agent_protocol["protocol_hash"]) in completed:
                    continue
                try:
                    for attempt in range(2):
                        try:
                            row = evaluate_one(
                                case, image, args.cache_root, args.docker_binary,
                                args.model, trial, args.max_steps, args.max_tokens,
                                args.strategy, args.planner_steps, args.max_cost_rmb,
                                no_progress_after, provenance,
                                args.auto_finish_verified_patch,
                                args.reviewer_steps, args.reviser_steps,
                            )
                            break
                        except EvaluationInfrastructureError as exc:
                            if attempt == 1 or not exc.retryable:
                                raise
                            print(f"RETRY {case.instance_id} trial={trial}: {exc}",
                                  file=sys.stderr, flush=True)
                            time.sleep(1)
                except Exception as exc:
                    failures.append((case.instance_id, trial, exc))
                    print(f"ERROR {case.instance_id} trial={trial}: {exc}",
                          file=sys.stderr, flush=True)
                    continue
                stream.write(json.dumps(row, ensure_ascii=False) + "\n")
                stream.flush()
                print(f"{case.instance_id:36s} {args.strategy:12s} trial={trial} "
                      f"pass={row['tests_pass']} tokens={row['total_tokens']}")
    generate_report(output, args.report or output.with_suffix(".md"))
    if failures:
        keys = ", ".join(f"{case}/trial-{trial}" for case, trial, _ in failures)
        raise RuntimeError(f"{len(failures)} real evaluation(s) failed without rows: {keys}")


if __name__ == "__main__":
    main()
