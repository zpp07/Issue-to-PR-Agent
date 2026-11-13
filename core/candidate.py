"""Stage candidate files in a private Git index."""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path, PureWindowsPath
import subprocess
from tempfile import TemporaryDirectory


def git_run(repo, args, *, check=True, env=None):
    settings = dict(os.environ if env is None else env)
    settings.update(GIT_TERMINAL_PROMPT="0", GCM_INTERACTIVE="never")
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=check, capture_output=True,
        text=True, encoding="utf-8", errors="replace", timeout=30, env=settings,
    )


@dataclass(frozen=True)
class Candidate:
    diff: str
    tree: str
    paths: tuple[str, ...]


def candidate(repo: Path, base: str, *, snapshot: Path | None = None) -> Candidate:
    prefix = []
    stage_dir = repo
    if snapshot is not None:
        git_dir = git_run(repo, ["rev-parse", "--absolute-git-dir"]).stdout.strip()
        prefix = [f"--git-dir={git_dir}", f"--work-tree={snapshot}"]
        stage_dir = snapshot
    with TemporaryDirectory(prefix="issue-to-pr-index-") as directory:
        env = dict(os.environ, GIT_INDEX_FILE=str(Path(directory) / "index"))
        git_run(stage_dir, [*prefix, "read-tree", base], env=env)
        git_run(stage_dir, [*prefix, "add", "-A", "--", "."], env=env)
        tree = git_run(stage_dir, [*prefix, "write-tree"], env=env).stdout.strip()
    diff = committed_diff(repo, base, tree)
    names = git_run(repo, ["diff", "--name-only", "--no-renames", "-z", base, tree]).stdout
    return Candidate(diff, tree, tuple(names.split("\0")[:-1]))


def committed_diff(repo: Path, base: str, tree: str) -> str:
    return git_run(repo, ["diff", "--no-ext-diff", "--no-textconv", "--no-renames",
                          "--binary", base, tree]).stdout.strip()


def plan_paths(workspace: Path, files: list[str]) -> set[str]:
    paths = set()
    for raw in files:
        parts = raw.replace("\\", "/").split("/")
        if any(not part or part in {".", ".."} or part.lower() == ".git"
               or ":" in part or part.rstrip(" .") != part
               or (os.name == "nt" and PureWindowsPath(part).is_reserved()) for part in parts):
            raise ValueError(f"计划仅支持工作区相对文件路径：{raw!r}")
        relative = "/".join(parts)
        file = workspace / relative
        if file.resolve() != file or file.is_dir():
            raise ValueError(f"计划仅支持普通文件路径：{raw!r}")
        paths.add(os.path.normcase(relative))
    return paths
