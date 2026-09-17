"""Materialize cross-file retrieval cases with hidden tests kept outside the workspace."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from benchmark.harder_catalog import CASE_BY_ID, HardCase


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def materialize(case: HardCase | str, root: str | Path) -> dict:
    if isinstance(case, str):
        case = CASE_BY_ID[case]
    root = Path(root)
    workspace, hidden = root / "workspace", root / "hidden_tests"
    workspace.mkdir(parents=True, exist_ok=False)
    hidden.mkdir(parents=True, exist_ok=False)
    for relative, content in case.files.items():
        destination = workspace / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(content, encoding="utf-8")
    public_path = workspace / "tests" / "test_public.py"
    public_path.parent.mkdir(parents=True, exist_ok=True)
    public_path.write_text(case.public_tests, encoding="utf-8")
    (hidden / "test_hidden.py").write_text(case.hidden_tests, encoding="utf-8")
    manifest = {
        "case": case.id,
        "files": {path: _sha256(content) for path, content in case.files.items()},
        "public_test_sha256": _sha256(case.public_tests),
        "hidden_test_sha256": _sha256(case.hidden_tests),
        "relevant_files": list(case.relevant_files),
    }
    (root / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return {"workspace": workspace, "hidden_tests": hidden, "manifest": manifest}
