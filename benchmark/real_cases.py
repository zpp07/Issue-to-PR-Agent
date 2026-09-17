"""Normalized schema and deterministic filters for real repository tasks."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import re
from pathlib import Path
from typing import Any, Iterable


DIFF_HEADER = re.compile(r"^diff --git a/(.+?) b/(.+?)$", re.MULTILINE)
TEST_PARTS = {"test", "tests", "testing"}


def patch_files(patch: str) -> list[str]:
    return list(dict.fromkeys(match.group(2) for match in DIFF_HEADER.finditer(patch)))


def is_test_path(path: str) -> bool:
    parts = {part.lower() for part in Path(path).parts}
    name = Path(path).name.lower()
    return bool(parts.intersection(TEST_PARTS)) or name.startswith("test_") or name.endswith("_test.py")


def changed_lines(patch: str) -> int:
    count = 0
    for line in patch.splitlines():
        if line.startswith(("+++", "---")):
            continue
        if line.startswith(("+", "-")):
            count += 1
    return count


@dataclass(frozen=True)
class RealCase:
    source: str
    instance_id: str
    repo: str
    base_commit: str
    issue: str
    gold_patch: str
    test_patch: str
    fail_to_pass: tuple[str, ...]
    pass_to_pass: tuple[str, ...]
    created_at: str
    version: str
    production_files: tuple[str, ...]
    test_files: tuple[str, ...]
    changed_lines: int
    source_url: str

    @classmethod
    def from_swegym(cls, row: dict[str, Any]) -> "RealCase":
        gold_patch = row.get("patch") or ""
        test_patch = row.get("test_patch") or ""
        production = tuple(
            path for path in patch_files(gold_patch)
            if path.endswith(".py") and not is_test_path(path)
        )
        tests = tuple(dict.fromkeys(
            path for path in patch_files(test_patch)
            if is_test_path(path)
        ))
        return cls(
            source="SWE-Gym/SWE-Gym-Lite",
            instance_id=str(row["instance_id"]),
            repo=str(row["repo"]),
            base_commit=str(row["base_commit"]),
            issue=str(row.get("problem_statement") or "").strip(),
            gold_patch=gold_patch,
            test_patch=test_patch,
            fail_to_pass=tuple(row.get("FAIL_TO_PASS") or ()),
            pass_to_pass=tuple(row.get("PASS_TO_PASS") or ()),
            created_at=str(row.get("created_at") or ""),
            version=str(row.get("version") or ""),
            production_files=production,
            test_files=tests,
            changed_lines=changed_lines(gold_patch),
            source_url=f"https://github.com/{row['repo']}/commit/{row['base_commit']}",
        )

    def to_dict(self, include_gold: bool = True) -> dict[str, Any]:
        output = asdict(self)
        if not include_gold:
            output.pop("gold_patch", None)
            output.pop("test_patch", None)
        return output


@dataclass(frozen=True)
class CandidatePolicy:
    min_issue_chars: int = 80
    max_issue_chars: int = 12_000
    min_changed_lines: int = 2
    max_changed_lines: int = 180
    min_production_files: int = 1
    max_production_files: int = 4
    require_test_patch: bool = True

    def reasons(self, case: RealCase) -> list[str]:
        reasons = []
        if not self.min_issue_chars <= len(case.issue) <= self.max_issue_chars:
            reasons.append("issue_length")
        if not self.min_changed_lines <= case.changed_lines <= self.max_changed_lines:
            reasons.append("patch_size")
        if not self.min_production_files <= len(case.production_files) <= self.max_production_files:
            reasons.append("production_file_count")
        if self.require_test_patch and (not case.test_patch or not case.test_files):
            reasons.append("missing_test_patch")
        if not case.fail_to_pass:
            reasons.append("missing_fail_to_pass")
        if any(not path.endswith(".py") for path in case.production_files):
            reasons.append("non_python_production_patch")
        return reasons

    def accepts(self, case: RealCase) -> bool:
        return not self.reasons(case)


def candidate_score(case: RealCase) -> tuple[int, int, int, str]:
    """Favor cross-file, focused patches with explicit regression tests."""
    cross_file = min(len(case.production_files), 3)
    focused = max(0, 180 - case.changed_lines)
    verification = min(len(case.fail_to_pass), 5) + min(len(case.test_files), 3)
    return (cross_file, verification, focused, case.instance_id)


def select_cases(cases: Iterable[RealCase], limit: int,
                 max_per_repo: int = 2,
                 policy: CandidatePolicy | None = None) -> list[RealCase]:
    policy = policy or CandidatePolicy()
    accepted = sorted(
        (case for case in cases if policy.accepts(case)),
        key=candidate_score,
        reverse=True,
    )
    selected = []
    per_repo: dict[str, int] = {}
    for case in accepted:
        if per_repo.get(case.repo, 0) >= max_per_repo:
            continue
        selected.append(case)
        per_repo[case.repo] = per_repo.get(case.repo, 0) + 1
        if len(selected) >= limit:
            break
    return selected


def write_jsonl(cases: Iterable[RealCase], path: str | Path,
                include_gold: bool = True) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    content = "".join(
        json.dumps(case.to_dict(include_gold=include_gold), ensure_ascii=False) + "\n"
        for case in cases
    )
    destination.write_text(content, encoding="utf-8")


def read_jsonl(path: str | Path) -> list[RealCase]:
    cases = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        row["fail_to_pass"] = tuple(row["fail_to_pass"])
        row["pass_to_pass"] = tuple(row["pass_to_pass"])
        row["production_files"] = tuple(row["production_files"])
        row["test_files"] = tuple(row["test_files"])
        cases.append(RealCase(**row))
    return cases
