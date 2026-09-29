"""SQLite-backed task, approval, artifact and append-only audit events."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import uuid
from typing import Any


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class Approval:
    id: str
    task_id: str
    kind: str
    subject_hash: str
    decision: str
    actor: str
    reason: str | None
    created_at: str


class TaskStore:
    """A small application-owned audit store; graph checkpoints do not replace it."""

    def __init__(self, database: str | Path):
        self.database = str(database)
        self._init_schema()

    def _connect(self):
        conn = sqlite3.connect(self.database)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript("""
            CREATE TABLE IF NOT EXISTS tasks (
                id TEXT PRIMARY KEY, issue TEXT NOT NULL, repo_path TEXT NOT NULL,
                workspace_path TEXT NOT NULL, base_commit TEXT NOT NULL,
                status TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS approvals (
                id TEXT PRIMARY KEY, task_id TEXT NOT NULL, kind TEXT NOT NULL,
                subject_hash TEXT NOT NULL, decision TEXT NOT NULL, actor TEXT NOT NULL,
                reason TEXT, created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS artifacts (
                id TEXT PRIMARY KEY, task_id TEXT NOT NULL, kind TEXT NOT NULL,
                sha256 TEXT NOT NULL, content TEXT NOT NULL, created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS audit_events (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL,
                event_type TEXT NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS audit_events_task_sequence
                ON audit_events(task_id, sequence);
            CREATE TABLE IF NOT EXISTS memories (
                id TEXT PRIMARY KEY, task_id TEXT NOT NULL, kind TEXT NOT NULL,
                content TEXT NOT NULL, source_type TEXT NOT NULL, source_ref TEXT NOT NULL,
                source_sha256 TEXT NOT NULL, metadata TEXT NOT NULL, created_at TEXT NOT NULL,
                UNIQUE(task_id, kind, content, source_ref, source_sha256)
            );
            """)

    def create_task(self, issue: str, repo_path: str, workspace_path: str, base_commit: str) -> str:
        task_id = uuid.uuid4().hex
        now = _now()
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO tasks VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (task_id, issue, repo_path, workspace_path, base_commit, "created", now, now),
            )
        self.event(task_id, "task_created", {"base_commit": base_commit, "workspace": workspace_path})
        return task_id

    def task(self, task_id: str) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if row is None:
            raise KeyError(f"未知 task_id：{task_id}")
        return dict(row)

    def set_status(self, task_id: str, status: str) -> None:
        with self._connect() as conn:
            conn.execute("UPDATE tasks SET status=?, updated_at=? WHERE id=?", (status, _now(), task_id))
        self.event(task_id, "status_changed", {"status": status})

    def transition_status(self, task_id: str, expected: str, status: str) -> bool:
        """Atomically change state and append its audit event, or do nothing."""
        with self._connect() as conn:
            changed = conn.execute(
                "UPDATE tasks SET status=?, updated_at=? WHERE id=? AND status=?",
                (status, _now(), task_id, expected),
            ).rowcount == 1
            if changed:
                conn.execute(
                    "INSERT INTO audit_events(task_id,event_type,payload,created_at) VALUES (?, ?, ?, ?)",
                    (task_id, "status_changed", json.dumps({"status": status}), _now()),
                )
        return changed

    def claim_execution(self, task_id: str) -> None:
        """Only one caller can move an approved task into execution."""
        with self._connect() as conn:
            changed = conn.execute(
                "UPDATE tasks SET status='executing', updated_at=? "
                "WHERE id=? AND status='ready_to_execute'",
                (_now(), task_id),
            ).rowcount == 1
            if not changed:
                raise RuntimeError("任务不在可执行状态，或已有执行在进行")
            for event_type, payload in (
                ("status_changed", {"status": "executing"}),
                ("execution_started", {}),
            ):
                conn.execute(
                    "INSERT INTO audit_events(task_id,event_type,payload,created_at) VALUES (?, ?, ?, ?)",
                    (task_id, event_type, json.dumps(payload), _now()),
                )

    def recover_interrupted_executions(self) -> list[str]:
        """At single-server startup, close abandoned jobs without replaying edits."""
        recovered = []
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute("SELECT id, status FROM tasks").fetchall()
            for row in rows:
                task_id = row["id"]
                started = conn.execute(
                    "SELECT MAX(sequence) FROM audit_events "
                    "WHERE task_id=? AND event_type='execution_started'",
                    (task_id,),
                ).fetchone()[0]
                if started is None:
                    continue
                terminal = conn.execute(
                    "SELECT 1 FROM audit_events WHERE task_id=? AND sequence>? "
                    "AND event_type IN "
                    "('execution_finished','execution_failed','execution_interrupted') LIMIT 1",
                    (task_id, started),
                ).fetchone()
                if terminal is not None:
                    continue
                if row["status"] == "executing":
                    conn.execute(
                        "UPDATE tasks SET status='execution_interrupted', updated_at=? WHERE id=?",
                        (_now(), task_id),
                    )
                    changes = (
                        ("status_changed", {"status": "execution_interrupted"}),
                        ("execution_interrupted", {"reason": "server_restarted", "replayed": False}),
                    )
                    recovered.append(task_id)
                else:
                    # The process died after workflow status changed but before
                    # its final execution event. Preserve the already-written status.
                    changes = (("execution_finished", {
                        "status": row["status"], "recovered": True,
                    }),)
                for event_type, payload in changes:
                    conn.execute(
                        "INSERT INTO audit_events(task_id,event_type,payload,created_at) VALUES (?, ?, ?, ?)",
                        (task_id, event_type, json.dumps(payload), _now()),
                    )
        return recovered

    def event(self, task_id: str, event_type: str, payload: dict[str, Any]) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO audit_events(task_id,event_type,payload,created_at) VALUES (?, ?, ?, ?)",
                (task_id, event_type, json.dumps(payload, ensure_ascii=False, sort_keys=True), _now()),
            )

    def artifact(self, task_id: str, kind: str, content: str, sha256: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO artifacts VALUES (?, ?, ?, ?, ?, ?)",
                (uuid.uuid4().hex, task_id, kind, sha256, content, _now()),
            )
        self.event(task_id, "artifact_recorded", {"kind": kind, "sha256": sha256})

    def latest_artifact(self, task_id: str, kind: str) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM artifacts WHERE task_id=? AND kind=? ORDER BY created_at DESC LIMIT 1",
                (task_id, kind),
            ).fetchone()
        if row is None:
            raise KeyError(f"task {task_id} 没有 {kind} artifact")
        return dict(row)

    def approve(self, task_id: str, kind: str, subject_hash: str, decision: str, actor: str, reason: str | None = None) -> Approval:
        if decision not in {"approved", "rejected"}:
            raise ValueError("decision 必须为 approved 或 rejected")
        approval = Approval(uuid.uuid4().hex, task_id, kind, subject_hash, decision, actor, reason, _now())
        with self._connect() as conn:
            conn.execute("INSERT INTO approvals VALUES (?, ?, ?, ?, ?, ?, ?, ?)", tuple(asdict(approval).values()))
        self.event(task_id, "approval_recorded", asdict(approval))
        return approval

    def is_approved(self, task_id: str, kind: str, subject_hash: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM approvals WHERE task_id=? AND kind=? AND subject_hash=? AND decision='approved' ORDER BY created_at DESC LIMIT 1",
                (task_id, kind, subject_hash),
            ).fetchone()
        return row is not None

    def events(self, task_id: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM audit_events WHERE task_id=? ORDER BY sequence", (task_id,)).fetchall()
        return [dict(row) for row in rows]

    def events_after(self, task_id: str, after: int = 0, limit: int = 100) -> list[dict[str, Any]]:
        if type(after) is not int or not 0 <= after <= 9_223_372_036_854_775_807:
            raise ValueError("after 必须是 SQLite 序号范围内的非负整数")
        if type(limit) is not int or not 1 <= limit <= 200:
            raise ValueError("limit 必须在 1–200 之间")
        self.task(task_id)
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM audit_events WHERE task_id=? AND sequence>? "
                "ORDER BY sequence LIMIT ?",
                (task_id, after, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def execution_event_state(self, task_id: str) -> tuple[int | None, int | None]:
        """Return the latest execution start and its terminal event sequence."""
        self.task(task_id)
        with self._connect() as conn:
            started = conn.execute(
                "SELECT MAX(sequence) FROM audit_events "
                "WHERE task_id=? AND event_type='execution_started'",
                (task_id,),
            ).fetchone()[0]
            if started is None:
                return None, None
            terminal = conn.execute(
                "SELECT MAX(sequence) FROM audit_events "
                "WHERE task_id=? AND sequence>? AND event_type IN "
                "('execution_finished','execution_failed','execution_interrupted')",
                (task_id, started),
            ).fetchone()[0]
        return started, terminal

    def add_memory(self, task_id: str, kind: str, content: str, source_type: str,
                   source_ref: str, source_sha256: str,
                   metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        """Insert an immutable, provenance-bearing memory or return its existing row."""
        memory_id = uuid.uuid4().hex
        metadata_json = json.dumps(metadata or {}, ensure_ascii=False, sort_keys=True)
        with self._connect() as conn:
            cursor = conn.execute(
                "INSERT OR IGNORE INTO memories VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (memory_id, task_id, kind, content, source_type, source_ref,
                 source_sha256, metadata_json, _now()),
            )
            row = conn.execute(
                "SELECT * FROM memories WHERE task_id=? AND kind=? AND content=? "
                "AND source_ref=? AND source_sha256=?",
                (task_id, kind, content, source_ref, source_sha256),
            ).fetchone()
        result = dict(row)
        if cursor.rowcount:
            self.event(task_id, "memory_recorded", {
                "memory_id": result["id"], "kind": kind, "source_type": source_type,
                "source_ref": source_ref, "source_sha256": source_sha256,
            })
        return result

    def memories(self, task_id: str, kinds: list[str] | None = None,
                 limit: int = 100) -> list[dict[str, Any]]:
        self.task(task_id)  # consistent unknown-task behavior
        limit = max(1, min(int(limit), 500))
        parameters: list[Any] = [task_id]
        where = "task_id=?"
        if kinds:
            placeholders = ",".join("?" for _ in kinds)
            where += f" AND kind IN ({placeholders})"
            parameters.extend(kinds)
        parameters.append(limit)
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM memories WHERE {where} ORDER BY created_at, id LIMIT ?",
                parameters,
            ).fetchall()
        return [dict(row) for row in rows]
