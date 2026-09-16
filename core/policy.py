"""Host-side command policy used before Docker isolation is available.

This module deliberately calls its result a *policy*, not a sandbox.  It
removes shell parsing and limits the commands the agent can request, while
making state-changing Git/network commands explicit approval events.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Sequence


class CommandLevel(str, Enum):
    ALLOW = "allow"
    APPROVAL = "approval"
    DENY = "deny"


@dataclass(frozen=True)
class CommandDecision:
    level: CommandLevel
    reason: str

    @property
    def allowed(self) -> bool:
        return self.level == CommandLevel.ALLOW

    @property
    def requires_approval(self) -> bool:
        return self.level == CommandLevel.APPROVAL


class CommandPolicy:
    """Small, auditable allow-list for the local Python maintenance scope."""

    SAFE_GIT = {"status", "diff", "rev-parse", "log", "show"}
    HIGH_RISK_GIT = {"add", "commit", "push", "reset", "clean", "checkout", "merge"}

    def evaluate(self, argv: Sequence[str] | None) -> CommandDecision:
        if not isinstance(argv, (list, tuple)) or not argv or not all(
            isinstance(item, str) and item for item in argv
        ):
            return CommandDecision(CommandLevel.DENY, "命令必须是非空 argv 字符串数组")

        command = argv[0].lower()
        if command in {"rm", "del", "rmdir", "curl", "wget", "powershell", "cmd", "bash", "sh"}:
            return CommandDecision(CommandLevel.DENY, "禁止危险 shell、删除或网络命令")

        if command in {"pytest", "ruff"}:
            return CommandDecision(CommandLevel.ALLOW, "允许的质量检查命令")

        if command in {"python", "python.exe"}:
            if len(argv) >= 3 and argv[1:3] == ["-m", "pytest"]:
                return CommandDecision(CommandLevel.ALLOW, "允许通过 Python 运行 pytest")
            if len(argv) >= 3 and argv[1:3] == ["-m", "ruff"]:
                return CommandDecision(CommandLevel.ALLOW, "允许通过 Python 运行 ruff")
            return CommandDecision(CommandLevel.DENY, "仅允许 python -m pytest 或 python -m ruff")

        if command == "git":
            if len(argv) < 2:
                return CommandDecision(CommandLevel.DENY, "git 子命令缺失")
            if argv[1] in self.SAFE_GIT:
                return CommandDecision(CommandLevel.ALLOW, "允许的只读 Git 命令")
            if argv[1] in self.HIGH_RISK_GIT:
                return CommandDecision(CommandLevel.APPROVAL, "Git 状态变更需要人工审批")
            return CommandDecision(CommandLevel.DENY, "未在允许列表中的 Git 子命令")

        return CommandDecision(CommandLevel.DENY, "命令不在允许列表中")
