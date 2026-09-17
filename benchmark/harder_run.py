"""Phase-4 browse-vs-retrieval ablation on cross-file tasks."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import tempfile
import time

from benchmark.dashboard import generate
from benchmark.harder_catalog import CASES, CASE_BY_ID
from benchmark.harder_dataset import materialize
from core.agent import run_agent
from core.client import make_client
from core.policy import CommandPolicy
from core.sandbox import DockerSandbox
from core.search import HybridCodeSearch, format_hits
from core.tools import build_tool_registry, make_executor
from core.usage import Usage


BROWSE_TOOLS = build_tool_registry("read_file", "write_file", "run_command", "finish")
PROMPT = """You are fixing a multi-file Python repository. The user supplies the repository tree.
Read the implementation, related configuration and documentation contract before editing. You may
edit source Python files but never tests or documentation. Verify with
[\"python\", \"-m\", \"pytest\", \"-q\", \"tests/test_public.py\"] and finish only after it passes.
When a retrieved-evidence section is supplied, treat its cited chunks as already-read evidence: do not
search again or re-read the same lines unless they are incomplete."""


def _tree(workspace: Path) -> str:
    return "\n".join(path.relative_to(workspace).as_posix() for path in sorted(workspace.rglob("*")) if path.is_file())


def _retrieved_evidence(search, query, file_limit=4):
    """Select one best chunk per file so retrieval cannot flood the prompt."""
    selected, paths = [], set()
    for hit in search.search(query, top_k=10, candidate_k=30):
        if hit.chunk.path in paths:
            continue
        selected.append(hit)
        paths.add(hit.chunk.path)
        if len(selected) >= file_limit:
            break
    return selected


def evaluate_one(case, method, sandbox, model):
    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix=f"hardbench-{case.id}-") as temp:
        paths = materialize(case, temp)
        workspace, hidden = paths["workspace"], paths["hidden_tests"]
        editable = [path for path in case.files if path.endswith(".py")]
        search = HybridCodeSearch(workspace) if method == "hybrid" else None
        executor = make_executor(
            workspace, policy=CommandPolicy(), command_runner=sandbox.command_runner(workspace),
            writable_files=editable,
        )
        trace, usage = [], Usage()
        task = f"Issue: {case.issue}\n\nRepository tree:\n{_tree(workspace)}"
        retrieved = _retrieved_evidence(search, case.issue) if search else []
        if retrieved:
            task += "\n\nRetrieved evidence (one controlled retrieval):\n" + format_hits(retrieved)
        result, usage = run_agent(
            make_client(), PROMPT, BROWSE_TOOLS, task,
            model=model, usage=usage, trace=trace, execute_tool=executor, max_steps=15,
        )
        public_path = workspace / "tests" / "test_public.py"
        test_tampered = public_path.read_text(encoding="utf-8") != case.public_tests
        public_path.write_text(case.public_tests, encoding="utf-8")
        verification = sandbox.run_hidden_tests(
            workspace, hidden,
            ["python", "-m", "pytest", "-q", "/workspace/tests/test_public.py", "/hidden", "-p", "no:cacheprovider"],
        )
        retryable = isinstance(result, dict) and result.get("retryable")
        if retryable:
            raise RuntimeError(result.get("result"))
        return {
            "case": case.id, "category": case.category, "difficulty": "hard", "method": method,
            "agent_passed": not retryable, "tests_pass": verification.exit_code == 0,
            "test_tampered": test_tampered, "container_timeout": verification.timed_out,
            "review_rounds": 1, "prompt_tokens": usage.prompt_tokens,
            "completion_tokens": usage.completion_tokens, "total_tokens": usage.total_tokens,
            "calls": usage.calls, "cost_rmb": round(usage.cost_rmb(), 6),
            "elapsed_seconds": round(time.perf_counter() - started, 3),
            "verification_output": verification.output[-500:], "trace_steps": len(trace),
            "search_calls": 1 if search else 0,
            "retrieved_files": [hit.chunk.path for hit in retrieved],
            "read_calls": sum(step.get("action") == "read_file" for step in trace),
        }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", default="all")
    parser.add_argument("--methods", default="browse,hybrid")
    parser.add_argument("--model", default="deepseek-chat")
    parser.add_argument("--image", default="issue-to-pr-runner:phase3")
    parser.add_argument("--output", default=str(Path(__file__).parent / "harder_results.jsonl"))
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    sandbox = DockerSandbox(image=args.image)
    sandbox.require_available()
    selected = CASES if args.cases == "all" else [CASE_BY_ID[item.strip()] for item in args.cases.split(",")]
    methods = [item.strip() for item in args.methods.split(",")]
    if any(method not in {"browse", "hybrid"} for method in methods):
        parser.error("methods must be browse and/or hybrid")
    output = Path(args.output)
    completed = set()
    if args.resume and output.exists():
        completed = {(row["case"], row["method"]) for row in
                     (json.loads(line) for line in output.read_text(encoding="utf-8").splitlines() if line)}
    with output.open("a", encoding="utf-8") as stream:
        for case in selected:
            for method in methods:
                if (case.id, method) in completed:
                    continue
                row = evaluate_one(case, method, sandbox, args.model)
                stream.write(json.dumps(row, ensure_ascii=False) + "\n")
                stream.flush()
                print(f"{case.id:28s} {method:7s} pass={row['tests_pass']} tokens={row['total_tokens']}")
    generate(output, output.with_suffix(".md"), title="Phase 4 harder-suite retrieval ablation")


if __name__ == "__main__":
    main()
