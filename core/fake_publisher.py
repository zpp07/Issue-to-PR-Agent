"""Explicitly simulated Draft PR publication for the local UI demo only."""
from __future__ import annotations

from typing import Any


class FakeDraftPRPublisher:
    """Satisfy the workflow publisher interface without Git writes or network I/O."""

    def publish(self, *, workspace: str, base_commit: str,
                request: dict[str, Any], expected_diff_sha256: str) -> dict[str, Any]:
        return {
            "repository": request["repository"],
            "head": request["head_branch"],
            "base": request["base_branch"],
            "draft": True,
            "simulated": True,
            "number": None,
            "html_url": None,
            "note": "No commit, push, or GitHub PR was created.",
        }
