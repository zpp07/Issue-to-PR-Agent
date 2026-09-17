from benchmark.import_swegym import normalize, summary
from pathlib import Path
import subprocess
import zipfile

import pytest

from benchmark.real_cases import CandidatePolicy, RealCase, patch_files, select_cases
from benchmark.real_snapshot import audit_snapshot, extract_archive, validate_identity
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
    assert not expected_pytest_failure(SandboxResult(2, "ERROR collecting tests"))
    assert not expected_pytest_failure(SandboxResult(1, "errors during collection"))
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
