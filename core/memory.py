"""Provenance-constrained task memory.

Only four durable categories are accepted.  Every record is derived from a
verifiable file, artifact, or human approval; there is intentionally no API
that persists an LLM's unsupported free-form inference.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from core.audit import Approval, TaskStore


MEMORY_KINDS = {"sourced_fact", "test_command", "failure", "approved_decision"}
SOURCE_TYPES = {"workspace_file", "artifact", "approval"}


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class TaskMemory:
    def __init__(self, store: TaskStore):
        self.store = store

    def _add(self, task_id: str, kind: str, content: str, source_type: str,
             source_ref: str, source_sha256: str,
             metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        if kind not in MEMORY_KINDS:
            raise ValueError(f"不允许的记忆类型：{kind}")
        if source_type not in SOURCE_TYPES or not source_ref or not source_sha256:
            raise ValueError("长期记忆必须包含可验证来源")
        if not content.strip():
            raise ValueError("不能持久化空记忆")
        return self.store.add_memory(
            task_id, kind, content.strip(), source_type, source_ref,
            source_sha256, metadata,
        )

    def remember_sourced_fact(self, task_id: str, statement: str,
                              source_path: str, evidence: str) -> dict[str, Any]:
        """Persist a fact only when the cited excerpt exists in a workspace file."""
        task = self.store.task(task_id)
        workspace = Path(task["workspace_path"]).resolve()
        candidate = Path(source_path)
        source = candidate.resolve() if candidate.is_absolute() else (workspace / candidate).resolve()
        try:
            inside = os.path.normcase(os.path.commonpath([str(source), str(workspace)])) == os.path.normcase(str(workspace))
        except ValueError:
            inside = False
        if not inside or not source.is_file():
            raise ValueError("事实来源必须是任务工作区内的现有文件")
        text = source.read_text(encoding="utf-8")
        if not evidence.strip() or evidence not in text:
            raise ValueError("引用证据必须逐字存在于来源文件")
        if statement.strip() != evidence.strip():
            raise ValueError("文件事实必须逐字等于引用证据；模型摘要或推断不能持久化")
        relative = source.relative_to(workspace).as_posix()
        return self._add(
            task_id, "sourced_fact", statement, "workspace_file", relative,
            _digest(text), {"evidence": evidence},
        )

    def capture_approved_plan(self, task_id: str) -> list[dict[str, Any]]:
        plan = self.store.latest_artifact(task_id, "plan")
        if not self.store.is_approved(task_id, "plan", plan["sha256"]):
            raise RuntimeError("只有已批准计划可以进入任务记忆")
        parsed = json.loads(plan["content"])
        records = [self._add(
            task_id, "approved_decision", f"已批准计划：{parsed['goal']}",
            "artifact", f"plan:{plan['id']}", plan["sha256"],
            {"affected_files": parsed["affected_files"]},
        )]
        for argv in parsed["allowed_commands"]:
            is_test = (argv and argv[0] == "pytest") or (
                len(argv) >= 3 and argv[0] in {"python", "python.exe"}
                and argv[1:3] == ["-m", "pytest"]
            )
            if is_test:
                records.append(self._add(
                    task_id, "test_command", json.dumps(argv, ensure_ascii=False),
                    "artifact", f"plan:{plan['id']}", plan["sha256"],
                ))
        return records

    def capture_approval(self, approval: Approval) -> dict[str, Any]:
        content = f"{approval.kind} 审批结果：{approval.decision}"
        if approval.reason:
            content += f"；理由：{approval.reason}"
        return self._add(
            approval.task_id, "approved_decision", content, "approval", approval.id,
            _digest(json.dumps({
                "kind": approval.kind, "subject_hash": approval.subject_hash,
                "decision": approval.decision, "actor": approval.actor,
                "reason": approval.reason,
            }, ensure_ascii=False, sort_keys=True)),
            {"actor": approval.actor, "subject_hash": approval.subject_hash},
        )

    def capture_failure(self, task_id: str, summary: str,
                        artifact: dict[str, Any]) -> dict[str, Any]:
        if artifact.get("task_id") != task_id or artifact.get("kind") != "test_report":
            raise ValueError("失败记忆必须引用当前任务的测试报告 artifact")
        return self._add(
            task_id, "failure", summary, "artifact", f"test_report:{artifact['id']}",
            artifact["sha256"],
        )

    def list(self, task_id: str, kinds: list[str] | None = None,
             limit: int = 100) -> list[dict[str, Any]]:
        if kinds and any(kind not in MEMORY_KINDS for kind in kinds):
            raise ValueError("包含不允许的记忆类型")
        task = self.store.task(task_id)
        workspace = Path(task["workspace_path"])
        records = self.store.memories(task_id, kinds=kinds, limit=limit)
        for record in records:
            record["stale"] = False
            if record["source_type"] == "workspace_file":
                source = workspace / record["source_ref"]
                try:
                    current = _digest(source.read_text(encoding="utf-8"))
                except (OSError, UnicodeDecodeError):
                    current = ""
                record["stale"] = current != record["source_sha256"]
        return records

    def render_context(self, task_id: str, limit: int = 30) -> str:
        records = [record for record in self.list(task_id, limit=limit) if not record["stale"]]
        if not records:
            return ""
        lines = ["Trusted task memory (facts only; each line has provenance):"]
        for record in records:
            lines.append(
                f"- [{record['kind']}] {record['content']} "
                f"(source={record['source_type']}:{record['source_ref']} "
                f"sha256={record['source_sha256'][:12]})"
            )
        return "\n".join(lines)
