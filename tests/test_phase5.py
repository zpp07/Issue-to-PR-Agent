import hashlib
import json
from pathlib import Path

import pytest

from core.audit import TaskStore
from core.memory import MEMORY_KINDS, TaskMemory
from core.workflow import IssueToPRWorkflow


def _task(tmpdir):
    root = Path(str(tmpdir))
    workspace = root / "workspace"
    workspace.mkdir()
    store = TaskStore(root / "tasks.sqlite3")
    task_id = store.create_task("issue", str(root), str(workspace), "base-commit")
    return root, workspace, store, task_id


def test_sourced_fact_requires_verbatim_workspace_evidence_and_expires(tmpdir):
    _, workspace, store, task_id = _task(tmpdir)
    source = workspace / "contract.md"
    evidence = "Timeout values are expressed in milliseconds."
    source.write_text(f"# Contract\n{evidence}\n", encoding="utf-8")
    memory = TaskMemory(store)

    record = memory.remember_sourced_fact(task_id, evidence, "contract.md", evidence)
    assert record["kind"] == "sourced_fact"
    assert record["source_type"] == "workspace_file"
    assert evidence in memory.render_context(task_id)

    with pytest.raises(ValueError, match="推断"):
        memory.remember_sourced_fact(task_id, "Timeouts should be short.", "contract.md", evidence)
    with pytest.raises(ValueError, match="工作区"):
        memory.remember_sourced_fact(task_id, evidence, str(workspace.parent / "outside.md"), evidence)

    source.write_text("# Changed contract\n", encoding="utf-8")
    listed = memory.list(task_id)
    assert listed[0]["stale"] is True
    assert evidence not in memory.render_context(task_id)


def test_approved_plan_automatically_becomes_provenance_memory(tmpdir):
    root, _, store, task_id = _task(tmpdir)
    workflow = IssueToPRWorkflow(store, root / "worktrees")
    plan = {
        "goal": "repair timeout conversion", "affected_files": ["worker.py"],
        "steps": ["edit", "test"], "risks": ["unit mismatch"],
        "allowed_commands": [
            ["python", "-m", "pytest", "-q"], ["git", "diff"],
        ],
    }
    plan_hash = workflow.submit_plan(task_id, plan)
    workflow.approve_plan(task_id, plan_hash, "reviewer", "approved", "plan is scoped")

    records = workflow.memory.list(task_id)
    kinds = [record["kind"] for record in records]
    assert set(kinds).issubset(MEMORY_KINDS)
    assert "test_command" in kinds
    assert kinds.count("approved_decision") == 2
    assert all(record["source_type"] in {"artifact", "approval"} for record in records)
    test_commands = [json.loads(record["content"]) for record in records
                     if record["kind"] == "test_command"]
    assert test_commands == [["python", "-m", "pytest", "-q"]]

    before = len(records)
    workflow.memory.capture_approved_plan(task_id)
    assert len(workflow.memory.list(task_id)) == before


def test_failure_memory_must_reference_test_report_artifact(tmpdir):
    _, _, store, task_id = _task(tmpdir)
    memory = TaskMemory(store)
    report = "FAILED tests/test_worker.py::test_timeout"
    digest = hashlib.sha256(report.encode("utf-8")).hexdigest()
    store.artifact(task_id, "test_report", report, digest)
    artifact = store.latest_artifact(task_id, "test_report")

    record = memory.capture_failure(task_id, "timeout assertion failed", artifact)
    assert record["kind"] == "failure"
    assert record["source_sha256"] == digest
    assert "test_report:" in record["source_ref"]

    fake = dict(artifact)
    fake["kind"] = "plan"
    with pytest.raises(ValueError, match="测试报告"):
        memory.capture_failure(task_id, "unsupported", fake)


def test_memory_context_contains_citations_not_raw_metadata(tmpdir):
    _, workspace, store, task_id = _task(tmpdir)
    source = workspace / "README.md"
    evidence = "Retries stop after three attempts."
    source.write_text(evidence, encoding="utf-8")
    memory = TaskMemory(store)
    memory.remember_sourced_fact(task_id, evidence, "README.md", evidence)
    context = memory.render_context(task_id)
    assert "source=workspace_file:README.md" in context
    assert "sha256=" in context
    assert json.loads(memory.list(task_id)[0]["metadata"])["evidence"] == evidence
