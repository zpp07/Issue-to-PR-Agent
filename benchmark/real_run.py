"""Run the current repair agent against the frozen real-repository set.

Reference fixes and regression-test patches stay evaluator-only. The agent sees
the issue and the unmodified base snapshot; hidden tests are injected only
after the agent finishes.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile
import time

from benchmark.real_cases import RealCase, is_test_path, read_jsonl
from benchmark.real_snapshot import searchable_files, snapshot_directory
from benchmark.real_verify import _apply, _prepare_fixture_bridge, _pytest_command
from core.agent import run_agent
from core.client import make_client
from core.policy import CommandPolicy
from core.sandbox import DockerLimits, DockerSandbox
from core.search import HybridCodeSearch, format_hits
from core.tools import build_tool_registry, make_executor
from core.usage import Usage


TOOLS = build_tool_registry(
    "search_code", "read_file", "replace_text", "write_file", "run_command", "finish"
)
PROMPT = """You are repairing a real Python repository from a GitHub issue.
Use search_code once or twice to locate relevant implementation and contract
evidence. Its citations contain line numbers: inspect those regions with
read_file start_line/end_line rather than repeatedly searching or reading a
large file from its beginning. Prefer replace_text for small, exact edits. You
may edit existing production Python files only. Never edit tests, documentation,
generated benchmark data, or dependency files. Repository commands run offline
in an isolated container. Run focused tests when practical and finish with a
concise summary of the fix.
"""


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
        row = {"step": step.get("step"), "action": step.get("action")}
        for key in ("path", "start_line", "end_line", "argv"):
            if key in args:
                row[key] = args[key]
        if "query" in args:
            row["query"] = str(args["query"])[:200]
        compact.append(row)
    return compact


def _fingerprints(workspace: Path, paths: list[str]) -> dict[str, str]:
    return {path: hashlib.sha256((workspace / path).read_bytes()).hexdigest()
            for path in paths}


def _prepare(case: RealCase, workspace: Path, sandbox: DockerSandbox) -> None:
    if case.repo != "facebookresearch/hydra":
        return
    result = sandbox.run(workspace, ["python", "setup.py", "antlr"], timeout=120)
    if result.exit_code != 0:
        raise RuntimeError(f"Hydra parser generation failed: {result.output}")


def evaluate_one(case: RealCase, image: str, cache_root: str | Path,
                 docker_binary: str, model: str, trial: int,
                 max_steps: int, max_tokens: int | None) -> dict:
    started = time.perf_counter()
    source = snapshot_directory(cache_root, case)
    if not source.is_dir():
        raise FileNotFoundError(f"snapshot is not prepared: {source}")
    runs = Path(cache_root) / "agent_runs"
    runs.mkdir(parents=True, exist_ok=True)
    run_root = Path(tempfile.mkdtemp(prefix=f"{case.instance_id}-t{trial}-", dir=str(runs)))
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
        usage, trace = Usage(), []
        result, usage = run_agent(
            make_client(), PROMPT, TOOLS, task, model=model, max_steps=max_steps,
            execute_tool=make_executor(
                workspace, policy=CommandPolicy(),
                command_runner=sandbox.command_runner(workspace),
                writable_files=editable, code_search=search,
            ),
            usage=usage, trace=trace, max_tokens=max_tokens,
        )
        llm_errors = [step for step in trace if step.get("action") == "llm_error"]
        if llm_errors:
            # Provider/runtime failures are missing observations, not model
            # failures, and must never become scored rows.
            raise RuntimeError(llm_errors[-1].get("error", "LLM failure"))
        agent_completed = not isinstance(result, dict)

        after_tests = _fingerprints(workspace, test_paths)
        after_editable = _fingerprints(workspace, editable)
        modified = sorted(path for path in editable
                          if before_editable[path] != after_editable[path])
        test_tampered = before_tests != after_tests

        # Evaluator-only material enters the workspace only after the agent is done.
        _apply(workspace, case.test_patch)
        _prepare_fixture_bridge(case, workspace)
        command, selected_pass_count = _pytest_command(case, workspace, pass_sample=5)
        verification = sandbox.run(
            workspace, command, read_only_workspace=True, timeout=300,
        )
        return {
            "case": case.instance_id, "repo": case.repo, "trial": trial,
            "model": model, "image": image, "agent_completed": agent_completed,
            "tests_pass": verification.exit_code == 0,
            "test_tampered": test_tampered, "container_timeout": verification.timed_out,
            "modified_files": modified, "gold_files": list(case.production_files),
            "gold_file_recall": (len(set(modified).intersection(case.production_files)) /
                                 len(case.production_files)),
            "fail_to_pass_count": len(case.fail_to_pass),
            "pass_to_pass_sample_count": selected_pass_count,
            "prompt_tokens": usage.prompt_tokens,
            "completion_tokens": usage.completion_tokens,
            "total_tokens": usage.total_tokens, "calls": usage.calls,
            "cost_rmb": round(usage.cost_rmb(), 6),
            "elapsed_seconds": round(time.perf_counter() - started, 3),
            "trace_steps": len(trace),
            "trace": compact_trace(trace),
            "search_calls": 1 + sum(step.get("action") == "search_code" for step in trace),
            "read_calls": sum(step.get("action") == "read_file" for step in trace),
            "verification_output": verification.output[-1000:],
        }
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
    parser.add_argument("--max-tokens", type=int)
    parser.add_argument("--output", default="benchmark/real/results.jsonl")
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    if args.trials < 1 or args.max_steps < 1:
        parser.error("trials and max-steps must be positive")

    selected = load_manifest(args.manifest, args.cases_file)
    if args.cases != "all":
        requested = {item.strip() for item in args.cases.split(",") if item.strip()}
        available = {case.instance_id for case, _ in selected}
        unknown = requested - available
        if unknown:
            parser.error(f"cases are not in verified manifest: {', '.join(sorted(unknown))}")
        selected = [(case, image) for case, image in selected if case.instance_id in requested]

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    completed = set()
    if args.resume and output.is_file():
        completed = {(row["case"], row["trial"], row["model"])
                     for row in (json.loads(line) for line in
                                 output.read_text(encoding="utf-8").splitlines()) if row}
    failures = []
    with output.open("a", encoding="utf-8") as stream:
        for case, image in selected:
            for trial in range(1, args.trials + 1):
                if (case.instance_id, trial, args.model) in completed:
                    continue
                try:
                    row = evaluate_one(
                        case, image, args.cache_root, args.docker_binary,
                        args.model, trial, args.max_steps, args.max_tokens,
                    )
                except Exception as exc:
                    failures.append((case.instance_id, trial, exc))
                    print(f"ERROR {case.instance_id} trial={trial}: {exc}",
                          file=sys.stderr, flush=True)
                    continue
                stream.write(json.dumps(row, ensure_ascii=False) + "\n")
                stream.flush()
                print(f"{case.instance_id:36s} trial={trial} "
                      f"pass={row['tests_pass']} tokens={row['total_tokens']}")
    if failures:
        keys = ", ".join(f"{case}/trial-{trial}" for case, trial, _ in failures)
        raise RuntimeError(f"{len(failures)} real evaluation(s) failed without rows: {keys}")


if __name__ == "__main__":
    main()
