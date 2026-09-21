"""Stable protocol manifests and run provenance for real evaluations.

The protocol hash intentionally describes behavior, not a Git commit.  Code
revision and dirty state are recorded alongside it so documentation-only
commits do not fragment otherwise identical experimental groups.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
from typing import Any


PROTOCOL_SCHEMA_VERSION = 1
TRACE_SCHEMA_VERSION = 2


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def file_sha256(path: str | Path) -> str:
    return sha256_bytes(Path(path).read_bytes())


def canonical_hash(value: Any) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return sha256_bytes(payload)


def prompt_digest(text: str) -> dict[str, Any]:
    return {"sha256": sha256_bytes(text.encode("utf-8")), "chars": len(text)}


def build_agent_protocol(
    *,
    strategy: str,
    model: str,
    prompts: dict[str, str],
    tool_schemas: dict[str, list[dict]],
    max_steps: int,
    planner_steps: int,
    max_tokens: int | None,
    max_cost_rmb: float | None,
    search_budget: int,
    no_progress: dict[str, Any],
) -> dict[str, Any]:
    """Return the canonical behavior definition used by ``protocol_hash``."""
    return {
        "schema_version": PROTOCOL_SCHEMA_VERSION,
        "strategy": strategy,
        "model": {
            "provider": "openai-compatible",
            "name": model,
            # The client currently sends no sampling overrides.  Make that
            # explicit instead of allowing an undocumented local default.
            "sampling": {
                "temperature": "provider_default",
                "top_p": "provider_default",
                "seed": None,
                "tool_choice": "provider_default",
            },
        },
        "prompts": {name: prompt_digest(text) for name, text in sorted(prompts.items())},
        "tool_schemas": tool_schemas,
        "budgets": {
            "max_steps": max_steps,
            "planner_steps": planner_steps,
            "max_tokens": max_tokens,
            "max_cost_rmb": max_cost_rmb,
            "search_calls": search_budget,
        },
        "retrieval": {
            "chunk_max_lines": 120,
            "initial_evidence_file_limit": 3,
            "default_top_k": 5,
            "candidate_k": 20,
            "hybrid_weights": {"bm25": 0.65, "dense": 0.35},
            "final_weights": {"hybrid": 0.45, "reranker": 0.55},
            "dense_backend": "hashing",
            "reranker_backend": "lexical",
        },
        "tool_behavior": {
            "max_tool_output_chars": 12000,
            "max_read_lines": 200,
            "write_scope": "existing-production-python-allowlist",
            "command_policy": "pytest-and-ruff-no-shell",
        },
        "no_progress": no_progress,
    }


def protocol_record(definition: dict[str, Any], version: str) -> dict[str, Any]:
    return {
        "protocol_version": version,
        "protocol_hash": canonical_hash(definition),
        "protocol": definition,
    }


def git_state(repo_root: str | Path) -> dict[str, Any]:
    root = Path(repo_root).resolve()

    def git(*args: str) -> str:
        return subprocess.run(
            ["git", "-C", str(root), *args], check=True,
            capture_output=True, text=True, encoding="utf-8",
        ).stdout.strip()

    revision = git("rev-parse", "HEAD")
    dirty_output = git("status", "--porcelain", "--untracked-files=all")
    record: dict[str, Any] = {
        "code_revision": revision,
        "worktree_dirty": bool(dirty_output),
    }
    if dirty_output:
        tracked_diff = subprocess.run(
            ["git", "-C", str(root), "diff", "--binary", "HEAD"], check=True,
            capture_output=True,
        ).stdout
        record["tracked_diff_hash"] = sha256_bytes(tracked_diff)
    return record


def build_evaluation_protocol(
    *, manifest_path: str | Path, cases_path: str | Path, docker_images: list[str],
) -> dict[str, Any]:
    definition = {
        "schema_version": 1,
        "manifest_sha256": file_sha256(manifest_path),
        "cases_sha256": file_sha256(cases_path),
        "docker_image_refs": sorted(docker_images),
        "hidden_tests_visible_to_agent": False,
        "pass_to_pass_sample": 5,
        "regression_check": "rerun-pass-to-pass-separately-after-combined-failure",
        "container": {"cpus": 2.0, "memory": "2g", "pids": 256, "timeout": 300},
    }
    return {
        "evaluation_protocol_hash": canonical_hash(definition),
        "evaluation_protocol": definition,
    }
