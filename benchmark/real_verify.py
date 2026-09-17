"""Verify real benchmark test transitions in a networkless Docker runtime."""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
from pathlib import Path
import shutil
import subprocess
import tempfile

from benchmark.real_cases import RealCase, read_jsonl
from benchmark.real_snapshot import snapshot_directory
from core.sandbox import DockerLimits, DockerSandbox, SandboxResult


@dataclass(frozen=True)
class TestAudit:
    instance_id: str
    image: str
    preparation_exit_code: int
    baseline_exit_code: int
    fixed_exit_code: int
    baseline_failed_as_expected: bool
    fixed_tests_passed: bool
    test_state: str
    fail_to_pass_count: int
    pass_to_pass_sample_count: int
    preparation_output: str
    baseline_output: str
    fixed_output: str

    def to_dict(self) -> dict:
        return asdict(self)


def expected_pytest_failure(result: SandboxResult) -> bool:
    output = result.output.lower()
    return (
        result.exit_code == 1
        and " failed" in output
        and "error collecting" not in output
        and " errors during collection" not in output
    )


def _apply(root: Path, patch: str) -> None:
    handle = tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", newline="\n", suffix=".patch",
        prefix="benchmark-", dir=str(root), delete=False,
    )
    try:
        with handle:
            handle.write(patch)
        result = subprocess.run(
            ["git", "apply", "--no-index", "--whitespace=nowarn",
             Path(handle.name).name],
            cwd=str(root), text=True, capture_output=True, check=False,
        )
        if result.returncode:
            raise RuntimeError((result.stderr or result.stdout).strip())
    finally:
        try:
            Path(handle.name).unlink()
        except FileNotFoundError:
            pass


def normalize_pytest_target(target: str) -> str:
    """Use function-level selection when an upstream parameter id is stale."""
    if "::" not in target:
        return target
    path, node = target.rsplit("::", 1)
    return f"{path}::{node.split('[', 1)[0]}"


def _pytest_command(case: RealCase, pass_sample: int) -> list[str]:
    raw_targets = list(case.fail_to_pass)
    raw_targets.extend(case.pass_to_pass[:pass_sample])
    targets = list(dict.fromkeys(normalize_pytest_target(target) for target in raw_targets))
    return ["python", "-m", "pytest", "-q", "-p", "no:cacheprovider", *targets]


def verify_case(case: RealCase, cache_root: str | Path, image: str,
                docker_binary: str, pass_sample: int = 5,
                keep_workspace: bool = False) -> TestAudit:
    source = snapshot_directory(cache_root, case)
    if not source.is_dir():
        raise FileNotFoundError(f"snapshot is not prepared: {source}")
    runs = Path(cache_root) / "runs"
    runs.mkdir(parents=True, exist_ok=True)
    run_root = Path(tempfile.mkdtemp(prefix=f"{case.instance_id}-", dir=str(runs)))
    workspace = run_root / "workspace"
    try:
        shutil.copytree(source, workspace)
        _apply(workspace, case.test_patch)
        sandbox = DockerSandbox(
            image=image, docker_binary=docker_binary,
            limits=DockerLimits(cpus=2.0, memory="2g", pids=256, timeout=300),
        )
        preparation = SandboxResult(0, "no preparation required")
        if case.repo == "facebookresearch/hydra":
            preparation = sandbox.run(
                workspace, ["python", "setup.py", "antlr"],
                read_only_workspace=False, timeout=120,
            )
            if preparation.exit_code != 0:
                return TestAudit(
                    instance_id=case.instance_id, image=image,
                    preparation_exit_code=preparation.exit_code,
                    baseline_exit_code=125, fixed_exit_code=125,
                    baseline_failed_as_expected=False, fixed_tests_passed=False,
                    test_state="environment_failed",
                    fail_to_pass_count=len(case.fail_to_pass),
                    pass_to_pass_sample_count=min(pass_sample, len(case.pass_to_pass)),
                    preparation_output=preparation.output,
                    baseline_output="not run", fixed_output="not run",
                )
        command = _pytest_command(case, pass_sample)
        baseline = sandbox.run(
            workspace, command, read_only_workspace=True, timeout=300,
        )
        _apply(workspace, case.gold_patch)
        fixed = sandbox.run(
            workspace, command, read_only_workspace=True, timeout=300,
        )
        baseline_expected = expected_pytest_failure(baseline)
        fixed_passed = fixed.exit_code == 0
        return TestAudit(
            instance_id=case.instance_id, image=image,
            preparation_exit_code=preparation.exit_code,
            baseline_exit_code=baseline.exit_code,
            fixed_exit_code=fixed.exit_code,
            baseline_failed_as_expected=baseline_expected,
            fixed_tests_passed=fixed_passed,
            test_state="tests_verified" if baseline_expected and fixed_passed else "test_failed",
            fail_to_pass_count=len(case.fail_to_pass),
            pass_to_pass_sample_count=min(pass_sample, len(case.pass_to_pass)),
            preparation_output=preparation.output,
            baseline_output=baseline.output,
            fixed_output=fixed.output,
        )
    finally:
        if not keep_workspace:
            shutil.rmtree(run_root, ignore_errors=True)


def write_or_replace(audit: TestAudit, path: str | Path) -> None:
    destination = Path(path)
    rows = []
    if destination.is_file():
        rows = [json.loads(line) for line in destination.read_text(encoding="utf-8").splitlines()
                if line.strip()]
    rows = [row for row in rows if row.get("instance_id") != audit.instance_id]
    rows.append(audit.to_dict())
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("instance_id")
    parser.add_argument("--cases", default="benchmark/real/selected.jsonl")
    parser.add_argument("--cache-root", default=".local/real_benchmark")
    parser.add_argument("--image", required=True)
    parser.add_argument("--docker-binary", default="docker")
    parser.add_argument("--pass-sample", type=int, default=5)
    parser.add_argument("--output", default="benchmark/real/test_audit.jsonl")
    parser.add_argument("--keep-workspace", action="store_true")
    args = parser.parse_args()
    if args.pass_sample < 0:
        parser.error("pass-sample must be non-negative")
    matches = [case for case in read_jsonl(args.cases)
               if case.instance_id == args.instance_id]
    if len(matches) != 1:
        parser.error(f"expected exactly one case named {args.instance_id!r}")
    audit = verify_case(
        matches[0], args.cache_root, args.image, args.docker_binary,
        args.pass_sample, args.keep_workspace,
    )
    write_or_replace(audit, args.output)
    print(json.dumps(audit.to_dict(), ensure_ascii=False, indent=2))
    if audit.test_state != "tests_verified":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
