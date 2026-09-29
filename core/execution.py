"""Single-process background execution and durable SQLite event polling."""
from __future__ import annotations

import sys
from threading import Thread
import traceback
from typing import Any, Callable

from core.audit import TaskStore


class ExecutionManager:
    """Submit one approved execution at a time; never auto-replay interrupted edits."""

    def __init__(self, store: TaskStore, service_factory: Callable[[], Any]):
        self.store = store
        self.service_factory = service_factory

    def submit(self, task_id: str, target_file: str, max_rounds: int = 3) -> dict[str, Any]:
        if not isinstance(target_file, str) or not target_file.strip():
            raise ValueError("target_file 必须是非空文件路径")
        if type(max_rounds) is not int or not 1 <= max_rounds <= 3:
            raise ValueError("max_rounds 必须在 1–3 之间")
        self.store.claim_execution(task_id)
        worker = Thread(
            target=self._run, args=(task_id, target_file, max_rounds),
            name=f"issue-to-pr-{task_id[:8]}", daemon=True,
        )
        try:
            worker.start()
        except Exception as exc:
            self._fail(task_id, exc)
            raise
        return {
            "task_id": task_id,
            "status": "executing",
            "events_url": f"/tasks/{task_id}/events?after=0",
        }

    def _run(self, task_id: str, target_file: str, max_rounds: int) -> None:
        try:
            result = self.service_factory().execute_after_plan_approval(
                task_id, target_file, max_rounds,
            )
            status = self.store.task(task_id)["status"]
            if status == "executing":
                raise RuntimeError("执行返回时没有落下终态状态")
            self.store.event(task_id, "execution_finished", {
                "status": status,
                "validation": result.get("validation") if isinstance(result, dict) else None,
            })
        except Exception as exc:
            self._fail(task_id, exc)

    def _fail(self, task_id: str, exc: Exception) -> None:
        traceback.print_exception(type(exc), exc, exc.__traceback__, file=sys.stderr)
        if self.store.transition_status(task_id, "executing", "execution_failed"):
            self.store.event(task_id, "execution_failed", {"error_type": type(exc).__name__})

    def recover_interrupted(self) -> list[str]:
        return self.store.recover_interrupted_executions()

    def event_page(self, task_id: str, after: int = 0, limit: int = 100) -> dict[str, Any]:
        events = self.store.events_after(task_id, after, limit)
        next_after = events[-1]["sequence"] if events else after
        has_more = bool(self.store.events_after(task_id, next_after, 1))
        started, terminal = self.store.execution_event_state(task_id)
        return {
            "task_id": task_id,
            "status": self.store.task(task_id)["status"],
            "events": events,
            "next_after": next_after,
            "has_more": has_more,
            "execution_done": started is not None and terminal is not None
                              and next_after >= terminal and not has_more,
        }
