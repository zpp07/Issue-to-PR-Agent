"""Materialize a case into agent-visible and verifier-only directories."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from benchmark.catalog import CASE_BY_ID, Case


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def materialize(case: Case | str, root: str | Path) -> dict:
    if isinstance(case, str):
        case = CASE_BY_ID[case]
    root = Path(root)
    workspace = root / "workspace"
    hidden = root / "hidden_tests"
    workspace.mkdir(parents=True, exist_ok=False)
    hidden.mkdir(parents=True, exist_ok=False)
    (workspace / "buggy.py").write_text(case.source, encoding="utf-8")
    (workspace / "test_buggy.py").write_text(case.public_tests, encoding="utf-8")
    (hidden / "test_hidden.py").write_text(case.hidden_tests, encoding="utf-8")
    manifest = {
        "case": case.id, "issue": case.issue, "category": case.category,
        "difficulty": case.difficulty,
        "source_sha256": sha256_text(case.source),
        "public_test_sha256": sha256_text(case.public_tests),
        "hidden_test_sha256": sha256_text(case.hidden_tests),
    }
    (root / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return {"workspace": workspace, "hidden_tests": hidden, "manifest": manifest}
