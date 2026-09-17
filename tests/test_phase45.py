from benchmark.import_swegym import normalize, summary
import json
from pathlib import Path
import subprocess
import zipfile

import pytest

from benchmark.real_cases import (
    CandidatePolicy,
    RealCase,
    patch_files,
    select_cases,
    write_jsonl,
)
from benchmark.real_run import editable_python_files, load_manifest
from benchmark.real_snapshot import (
    SnapshotAudit,
    audit_snapshot,
    extract_archive,
    validate_identity,
    write_audits,
)
from benchmark.real_verify import _apply, expected_pytest_failure, normalize_pytest_target
from core.sandbox import SandboxResult


GOLD = """diff --git a/pkg/service.py b/pkg/service.py
--- a/pkg/service.py
+++ b/pkg/service.py
@@ -1 +1 @@
-return old
+return new
diff --git a/pkg/config.py b/pkg/config.py
--- a/pkg/config.py
+++ b/pkg/config.py
@@ -1 +1 @@
-TIMEOUT = 1
+TIMEOUT = 2
"""

TEST_PATCH = """diff --git a/tests/test_service.py b/tests/test_service.py
--- a/tests/test_service.py
+++ b/tests/test_service.py
@@ -1 +1,2 @@
 def test_old(): pass
+def test_regression(): assert True
"""


def row(instance="owner__repo-1", repo="owner/repo"):
    return {
        "instance_id": instance, "repo": repo, "base_commit": "abc123",
        "problem_statement": "The service uses the wrong timeout across modules and violates the documented behavior.",
        "patch": GOLD, "test_patch": TEST_PATCH, "FAIL_TO_PASS": ["tests/test_service.py::test_regression"],
        "PASS_TO_PASS": ["tests/test_service.py::test_old"], "created_at": "2025-01-01", "version": "1",
        "hints_text": "EXACT SECRET SOLUTION THAT MUST NOT BE PERSISTED",
    }


def test_real_case_parses_patch_and_discards_solution_hints():
    case = normalize([row()])[0]
    assert case.production_files == ("pkg/service.py", "pkg/config.py")
    assert case.test_files == ("tests/test_service.py",)
    assert "SECRET SOLUTION" not in str(case.to_dict())
    assert patch_files(GOLD) == ["pkg/service.py", "pkg/config.py"]
    assert CandidatePolicy().accepts(case)


def test_policy_rejects_unverifiable_or_oversized_tasks():
    base = row()
    base["test_patch"] = ""
    base["FAIL_TO_PASS"] = []
    case = RealCase.from_swegym(base)
    reasons = CandidatePolicy().reasons(case)
    assert "missing_test_patch" in reasons
    assert "missing_fail_to_pass" in reasons


def test_selection_is_deterministic_and_repo_balanced():
    rows = [row(f"a__repo-{index}", "a/repo") for index in range(4)]
    rows += [row(f"b__repo-{index}", "b/repo") for index in range(3)]
    cases = normalize(rows)
    selected = select_cases(cases, limit=4, max_per_repo=2)
    assert len(selected) == 4
    assert sum(case.repo == "a/repo" for case in selected) == 2
    assert sum(case.repo == "b/repo" for case in selected) == 2
    assert [case.instance_id for case in selected] == [case.instance_id for case in select_cases(cases, 4, 2)]


def test_summary_reports_filter_reasons_without_hints():
    rows = [row(), row("bad__repo-1", "bad/repo")]
    rows[1]["problem_statement"] = "short"
    cases = normalize(rows)
    result = summary(cases, CandidatePolicy())
    assert result["source_rows"] == 2
    assert result["accepted_rows"] == 1
    assert result["rejections"]["issue_length"] == 1


def test_snapshot_archive_is_safely_stripped_and_identity_validated(tmpdir):
    tmp_path = Path(str(tmpdir))
    archive = tmp_path / "repo.zip"
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr("repo-abc/pkg/service.py", "return 'old'\n")
        output.writestr("repo-abc/tests/test_service.py", "def test_old(): pass\n")
    destination = extract_archive(archive, tmp_path / "snapshot")
    assert (destination / "pkg/service.py").read_text() == "return 'old'\n"
    validate_identity("owner/repo", "abc1234")
    with pytest.raises(ValueError):
        validate_identity("owner/../repo", "abc1234")


def test_snapshot_archive_rejects_zip_slip(tmpdir):
    tmp_path = Path(str(tmpdir))
    archive = tmp_path / "bad.zip"
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr("repo-abc/../escaped.py", "bad")
    with pytest.raises(ValueError):
        extract_archive(archive, tmp_path / "snapshot")


def test_snapshot_archive_skips_symlinks_without_creating_them(tmpdir):
    tmp_path = Path(str(tmpdir))
    archive = tmp_path / "repo.zip"
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr("repo-abc/pkg/service.py", "return 'old'\n")
        link = zipfile.ZipInfo("repo-abc/pkg/service-link.py")
        link.create_system = 3
        link.external_attr = 0o120777 << 16
        output.writestr(link, "service.py")
    destination = extract_archive(archive, tmp_path / "snapshot")
    assert (destination / "pkg/service.py").is_file()
    assert not (destination / "pkg/service-link.py").exists()


def test_snapshot_audit_merge_replaces_and_appends_by_instance(tmpdir):
    def audit(instance_id, file_count):
        return SnapshotAudit(
            instance_id=instance_id, repo="owner/repo", base_commit="abc1234",
            snapshot_state="patch_verified", searchable_file_count=file_count,
            python_file_count=file_count, production_files_exist=True,
            gold_patch_applies=True, test_patch_applies=True, test_state="not_run",
            retrieval_files=(), context_files=(), gold_file_recall=0.0, reasons=(),
        )

    output = Path(str(tmpdir)) / "audit.jsonl"
    write_audits([audit("case-a", 1), audit("case-b", 2)], output)
    write_audits([audit("case-b", 20), audit("case-c", 3)], output, merge=True)
    rows = [json.loads(line) for line in output.read_text().splitlines()]
    assert [row["instance_id"] for row in rows] == ["case-a", "case-b", "case-c"]
    assert [row["searchable_file_count"] for row in rows] == [1, 20, 3]


def test_snapshot_audit_marks_patch_verified_but_tests_not_run(tmpdir):
    tmp_path = Path(str(tmpdir))
    (tmp_path / "pkg").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "pkg/service.py").write_text("return old\n")
    (tmp_path / "pkg/config.py").write_text("TIMEOUT = 1\n")
    (tmp_path / "tests/test_service.py").write_text("def test_old(): pass\n")
    for index in range(20):
        (tmp_path / f"notes_{index}.md").write_text(f"service timeout note {index}\n")
    case = RealCase.from_swegym(row())
    audit = audit_snapshot(case, tmp_path, context_files=15)
    assert audit.snapshot_state == "patch_verified"
    assert audit.eligible_for_execution
    assert audit.test_state == "not_run"
    assert audit.gold_patch_applies and audit.test_patch_applies
    assert len(audit.context_files) == 15
    _apply(tmp_path, case.gold_patch)
    assert (tmp_path / "pkg/service.py").read_text() == "return new\n"
    assert (tmp_path / "pkg/config.py").read_text() == "TIMEOUT = 2\n"


def test_real_test_transition_requires_an_actual_pytest_failure():
    assert expected_pytest_failure(SandboxResult(1, "1 failed, 4 passed"))
    assert expected_pytest_failure(SandboxResult(1, "=== FAILURES ===\ntest_regression"))
    assert not expected_pytest_failure(SandboxResult(2, "ERROR collecting tests"))
    assert not expected_pytest_failure(SandboxResult(1, "errors during collection"))
    assert not expected_pytest_failure(SandboxResult(1, "INTERNALERROR failures"))
    assert normalize_pytest_target("tests/test_x.py::test_x[value]") == "tests/test_x.py::test_x"


def test_apply_is_relative_to_snapshot_nested_inside_another_git_repo(tmpdir):
    parent = Path(str(tmpdir))
    subprocess.run(["git", "init", "-q", str(parent)], check=True)
    workspace = parent / "cache" / "snapshot"
    workspace.mkdir(parents=True)
    (workspace / "pkg").mkdir()
    (workspace / "pkg/service.py").write_text("return old\n")
    nested_patch = """diff --git a/pkg/service.py b/pkg/service.py
--- a/pkg/service.py
+++ b/pkg/service.py
@@ -1 +1 @@
-return old
+return new
"""
    _apply(workspace, nested_patch)
    assert (workspace / "pkg/service.py").read_text() == "return new\n"


def test_verified_manifest_loads_only_known_unique_cases(tmpdir):
    root = Path(str(tmpdir))
    cases_path = root / "cases.jsonl"
    manifest_path = root / "manifest.json"
    write_jsonl(normalize([row()]), cases_path)
    manifest_path.write_text(json.dumps({
        "status": "tests_verified",
        "cases": [{"instance_id": "owner__repo-1", "image": "runner:test"}],
    }))
    selected = load_manifest(manifest_path, cases_path)
    assert [(case.instance_id, image) for case, image in selected] == [
        ("owner__repo-1", "runner:test")
    ]

    manifest_path.write_text(json.dumps({
        "status": "tests_verified",
        "cases": [
            {"instance_id": "owner__repo-1", "image": "runner:test"},
            {"instance_id": "owner__repo-1", "image": "runner:test"},
        ],
    }))
    with pytest.raises(ValueError, match="unique"):
        load_manifest(manifest_path, cases_path)


def test_real_runner_write_allowlist_excludes_tests_and_non_python(tmpdir):
    root = Path(str(tmpdir))
    (root / "pkg").mkdir()
    (root / "tests").mkdir()
    (root / "pkg/service.py").write_text("VALUE = 1\n")
    (root / "pkg/config.yaml").write_text("value: 1\n")
    (root / "tests/test_service.py").write_text("def test_value(): pass\n")
    assert editable_python_files(root) == ["pkg/service.py"]
