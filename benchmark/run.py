"""Run single, reviewer and planner baselines inside the Phase 3 sandbox."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
from pathlib import Path
import sys
import tempfile
import time

from core.agent import run_agent
from core.client import make_client
from core.policy import CommandPolicy
from core.sandbox import DockerSandbox
from core.tools import build_tool_registry, make_executor
from core.usage import Usage
from benchmark.catalog import CASES, CASE_BY_ID
from benchmark.dashboard import generate
from benchmark.dataset import materialize, sha256_text
from coder_reviewer import multi_agent_review

CODER_TOOLS = build_tool_registry("read_file", "write_file", "run_command", "finish")
PLANNER_TOOLS = build_tool_registry("read_file", "plan_finish")
CODER_PROMPT = """You are a Python bug-fixing agent. Read buggy.py and test_buggy.py,
edit only buggy.py, and call run_command with argv ["python", "-m", "pytest", "-q"]
to verify public tests. Finish with the finish tool. Never alter tests."""
PLANNER_PROMPT = """Inspect buggy.py and test_buggy.py and produce a structured repair plan.
Do not edit files. Finish with plan_finish. The only allowed command is
["python", "-m", "pytest", "-q"]."""


class EvaluationInfrastructureError(RuntimeError):
    """A transient provider/runtime failure that must not count as model failure."""


def executor_for(workspace, sandbox):
    return make_executor(
        workspace, policy=CommandPolicy(),
        command_runner=sandbox.command_runner(workspace),
        writable_files=["buggy.py"],
    )


def run_method(client, method, workspace, issue, sandbox, model):
    executor = executor_for(workspace, sandbox)
    usage, trace = Usage(), []
    rounds, agent_passed = 1, False
    if method == "single":
        result, usage = run_agent(
            client, CODER_PROMPT, CODER_TOOLS, issue, model=model,
            usage=usage, trace=trace, execute_tool=executor,
        )
        agent_passed = not (isinstance(result, dict) and result.get("retryable"))
    elif method == "reviewer":
        agent_passed, _, rounds, usage = multi_agent_review(
            client, str(Path(workspace) / "buggy.py"), task=issue,
            model=model, usage=usage, trace=trace, verbose=False, executor=executor,
        )
    elif method == "planner":
        plan, usage = run_agent(
            client, PLANNER_PROMPT, PLANNER_TOOLS, issue, model=model,
            usage=usage, trace=trace, execute_tool=executor, max_steps=8,
        )
        if not isinstance(plan, dict) or "steps" not in plan:
            return False, rounds, usage, trace
        planned_task = issue + "\nApproved plan:\n" + json.dumps(plan, ensure_ascii=False)
        agent_passed, _, rounds, usage = multi_agent_review(
            client, str(Path(workspace) / "buggy.py"), task=planned_task,
            model=model, usage=usage, trace=trace, verbose=False, executor=executor,
        )
    else:
        raise ValueError(f"unknown method: {method}")
    return agent_passed, rounds, usage, trace


def evaluate_one(client, case, method, sandbox, model):
    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix=f"agentbench-{case.id}-") as temp:
        paths = materialize(case, temp)
        workspace, hidden = paths["workspace"], paths["hidden_tests"]
        original_public = case.public_tests
        agent_passed, rounds, usage, trace = run_method(
            client, method, workspace, case.issue, sandbox, model,
        )
        if any(step.get("action") == "llm_error" and step.get("retryable") for step in trace):
            raise EvaluationInfrastructureError("retryable LLM failure during evaluation")
        public_path = workspace / "test_buggy.py"
        current_public = public_path.read_text(encoding="utf-8")
        test_tampered = sha256_text(current_public) != sha256_text(original_public)
        # Never trust an agent-modified public test during verification.
        public_path.write_text(original_public, encoding="utf-8")
        verification = sandbox.run_hidden_tests(
            workspace, hidden,
            ["python", "-m", "pytest", "-q", "/workspace/test_buggy.py", "/hidden", "-p", "no:cacheprovider"],
        )
        return {
            "case": case.id, "category": case.category, "difficulty": case.difficulty,
            "method": method, "agent_passed": bool(agent_passed),
            "tests_pass": verification.exit_code == 0, "test_tampered": test_tampered,
            "container_timeout": verification.timed_out, "review_rounds": rounds,
            "prompt_tokens": usage.prompt_tokens, "completion_tokens": usage.completion_tokens,
            "total_tokens": usage.total_tokens, "calls": usage.calls,
            "cost_rmb": round(usage.cost_rmb(), 6),
            "elapsed_seconds": round(time.perf_counter() - started, 3),
            "verification_output": verification.output[-500:],
            "trace_steps": len(trace),
        }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", default="all", help="all or comma-separated case ids")
    parser.add_argument("--methods", default="single,reviewer,planner")
    parser.add_argument("--model", default="deepseek-chat")
    parser.add_argument("--image", default="issue-to-pr-runner:phase3")
    parser.add_argument("--output", default=str(Path(__file__).parent / "results.jsonl"))
    parser.add_argument("--workers", type=int, default=1, help="parallel isolated evaluations")
    parser.add_argument("--resume", action="store_true", help="skip case/method rows already in output")
    args = parser.parse_args()
    if args.workers < 1:
        parser.error("--workers must be at least 1")
    sandbox = DockerSandbox(image=args.image)
    sandbox.require_available()  # never fall back to host execution
    selected = CASES if args.cases == "all" else [CASE_BY_ID[item.strip()] for item in args.cases.split(",")]
    methods = [item.strip() for item in args.methods.split(",")]
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    completed = set()
    if args.resume and output.exists():
        for line in output.read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                completed.add((row["case"], row["method"]))
    jobs = [
        (case, method) for case in selected for method in methods
        if (case.id, method) not in completed
    ]

    def execute(job):
        case, method = job
        # Do not share an SDK/HTTP client across worker threads. The benchmark
        # owns one whole-job retry because the stdlib client intentionally does
        # not add a second per-request retry layer.
        for attempt in range(2):
            try:
                return evaluate_one(make_client(), case, method, sandbox, args.model)
            except EvaluationInfrastructureError:
                if attempt == 1:
                    raise
                time.sleep(1)

    failures = []
    with output.open("a", encoding="utf-8") as stream:
        if args.workers == 1:
            def sequential_rows():
                for job in jobs:
                    try:
                        yield execute(job)
                    except Exception as exc:
                        failures.append((job[0].id, job[1], exc))
                        print(f"ERROR {job[0].id} {job[1]}: {exc}", file=sys.stderr, flush=True)
            rows = sequential_rows()
        else:
            pool = ThreadPoolExecutor(max_workers=args.workers)
            futures = {pool.submit(execute, job): job for job in jobs}
            def parallel_rows():
                for future in as_completed(futures):
                    job = futures[future]
                    try:
                        yield future.result()
                    except Exception as exc:
                        failures.append((job[0].id, job[1], exc))
                        print(f"ERROR {job[0].id} {job[1]}: {exc}", file=sys.stderr, flush=True)
            rows = parallel_rows()
        try:
            for row in rows:
                stream.write(json.dumps(row, ensure_ascii=False) + "\n")
                stream.flush()
                print(f"{row['case']:22s} {row['method']:8s} pass={row['tests_pass']} tokens={row['total_tokens']}")
        finally:
            if args.workers != 1:
                # Python 3.8 does not support the cancel_futures argument.
                pool.shutdown(wait=True)
    generate(output, output.with_suffix(".md"))
    if failures:
        failed_keys = ", ".join(f"{case}/{method}" for case, method, _ in failures)
        raise RuntimeError(f"{len(failures)} evaluation(s) failed without rows: {failed_keys}")


if __name__ == "__main__":
    main()
