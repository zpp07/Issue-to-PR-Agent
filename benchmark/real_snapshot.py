"""Prepare and audit exact repository snapshots for real benchmark cases.

This module deliberately separates cheap dataset filtering from expensive
execution validation.  A case is not called test-verified merely because the
upstream dataset contains test names.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import ssl
import subprocess
import tempfile
from urllib.request import HTTPSHandler, ProxyHandler, Request, build_opener
import zipfile

from benchmark.real_cases import RealCase, read_jsonl
from core.search import HybridCodeSearch, SUPPORTED_SUFFIXES


REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
COMMIT_RE = re.compile(r"^[0-9a-fA-F]{7,40}$")
MAX_ARCHIVE_BYTES = 300 * 1024 * 1024
IGNORED_PARTS = {
    ".git", ".hg", ".svn", ".venv", "venv", "node_modules",
    "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    "dist", "build", ".local",
}


def _ssl_context():
    try:
        import certifi
    except ImportError:
        return ssl.create_default_context()
    return ssl.create_default_context(cafile=certifi.where())


def validate_identity(repo: str, commit: str) -> None:
    if not REPO_RE.fullmatch(repo):
        raise ValueError(f"unsafe repository name: {repo!r}")
    if not COMMIT_RE.fullmatch(commit):
        raise ValueError(f"unsafe commit: {commit!r}")


def snapshot_directory(cache_root: str | Path, case: RealCase) -> Path:
    validate_identity(case.repo, case.base_commit)
    owner, name = case.repo.split("/", 1)
    return Path(cache_root) / "snapshots" / f"{owner}__{name}" / case.base_commit


def _safe_members(archive: zipfile.ZipFile):
    """Yield (member, stripped relative path) while rejecting zip-slip/symlinks."""
    files = [member for member in archive.infolist() if not member.is_dir()]
    roots = {PurePosixPath(member.filename).parts[0] for member in files}
    if len(roots) != 1:
        raise ValueError("archive must contain exactly one top-level directory")
    root = next(iter(roots))
    for member in files:
        parts = PurePosixPath(member.filename).parts
        if not parts or parts[0] != root:
            raise ValueError("archive member escaped its top-level directory")
        relative = PurePosixPath(*parts[1:])
        if not relative.parts or relative.is_absolute() or ".." in relative.parts:
            raise ValueError(f"unsafe archive member: {member.filename!r}")
        unix_mode = member.external_attr >> 16
        if (unix_mode & 0o170000) == 0o120000:
            raise ValueError(f"symbolic link is not allowed: {member.filename!r}")
        yield member, relative


def extract_archive(archive_path: str | Path, destination: str | Path) -> Path:
    destination = Path(destination)
    if destination.exists():
        raise FileExistsError(destination)
    # Extract directly into a newly-created exact cache path.  Renaming large
    # populated directories is unreliable on some Windows/Python versions.
    destination.mkdir(parents=True)
    try:
        with zipfile.ZipFile(archive_path) as archive:
            for member, relative in _safe_members(archive):
                target = destination.joinpath(*relative.parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(member) as source, target.open("wb") as output:
                    shutil.copyfileobj(source, output)
    except Exception:
        shutil.rmtree(destination, ignore_errors=True)
        raise
    return destination


def download_snapshot(case: RealCase, cache_root: str | Path,
                      proxy: str | None = None) -> Path:
    """Download a GitHub archive for the exact base commit and cache it."""
    destination = snapshot_directory(cache_root, case)
    if destination.is_dir():
        return destination
    archive_path = destination.with_suffix(".zip")
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    if archive_path.is_file():
        return extract_archive(archive_path, destination)
    partial = archive_path.with_suffix(".zip.part")
    url = f"https://github.com/{case.repo}/archive/{case.base_commit}.zip"
    handlers = [HTTPSHandler(context=_ssl_context())]
    if proxy:
        handlers.append(ProxyHandler({"http": proxy, "https": proxy}))
    opener = build_opener(*handlers)
    request = Request(url, headers={"User-Agent": "Issue-to-PR-Agent benchmark curator"})
    total = 0
    try:
        with opener.open(request, timeout=120) as response, partial.open("wb") as output:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_ARCHIVE_BYTES:
                    raise ValueError(f"archive exceeds {MAX_ARCHIVE_BYTES} bytes")
                output.write(chunk)
        partial.replace(archive_path)
        return extract_archive(archive_path, destination)
    finally:
        try:
            partial.unlink()
        except FileNotFoundError:
            pass


def searchable_files(root: str | Path) -> list[str]:
    root = Path(root)
    output = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in SUPPORTED_SUFFIXES:
            continue
        relative = path.relative_to(root)
        if any(part in IGNORED_PARTS for part in relative.parts):
            continue
        try:
            if path.stat().st_size <= 300_000:
                path.read_text(encoding="utf-8")
            else:
                continue
        except (OSError, UnicodeDecodeError):
            continue
        output.append(relative.as_posix())
    return output


def patch_applies(root: str | Path, patch: str) -> tuple[bool, str]:
    if not patch.strip():
        return False, "empty patch"
    root = Path(root)
    handle = tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", newline="\n", suffix=".patch",
        prefix="benchmark-", dir=str(root), delete=False,
    )
    try:
        with handle:
            handle.write(patch)
        result = subprocess.run(
            ["git", "apply", "--no-index", "--check", "--whitespace=nowarn",
             Path(handle.name).name],
            cwd=str(root), text=True, capture_output=True, check=False,
        )
        message = (result.stderr or result.stdout).strip()
        return result.returncode == 0, message
    finally:
        try:
            Path(handle.name).unlink()
        except FileNotFoundError:
            pass


@dataclass(frozen=True)
class SnapshotAudit:
    instance_id: str
    repo: str
    base_commit: str
    snapshot_state: str
    searchable_file_count: int
    python_file_count: int
    production_files_exist: bool
    gold_patch_applies: bool
    test_patch_applies: bool
    test_state: str
    retrieval_files: tuple[str, ...]
    context_files: tuple[str, ...]
    gold_file_recall: float
    reasons: tuple[str, ...]

    @property
    def eligible_for_execution(self) -> bool:
        return not self.reasons and self.snapshot_state == "patch_verified"

    def to_dict(self) -> dict:
        output = asdict(self)
        output["eligible_for_execution"] = self.eligible_for_execution
        return output


def audit_snapshot(case: RealCase, root: str | Path,
                   context_files: int = 40) -> SnapshotAudit:
    root = Path(root)
    files = searchable_files(root)
    available = set(files)
    production_exist = all(path in available for path in case.production_files)
    gold_ok, gold_error = patch_applies(root, case.gold_patch)
    test_ok, test_error = patch_applies(root, case.test_patch)
    reasons = []
    if len(files) < 15:
        reasons.append("too_few_searchable_files")
    if not production_exist:
        reasons.append("missing_production_file")
    if not gold_ok:
        reasons.append("gold_patch_does_not_apply")
    if not test_ok:
        reasons.append("test_patch_does_not_apply")

    retrieval = []
    if files:
        try:
            hits = HybridCodeSearch(root).search(case.issue, top_k=10, candidate_k=100)
            for hit in hits:
                path = hit.chunk.path
                if path not in retrieval:
                    retrieval.append(path)
        except (OSError, UnicodeDecodeError, SyntaxError, ValueError):
            reasons.append("retrieval_failed")
    # A realistic context pack contains the retriever's top files plus stable
    # distractors. Gold/test paths are never inserted merely because the
    # curator knows them; they appear only if retrieval found them.
    hidden_paths = set(case.production_files).union(case.test_files)
    distractors = sorted(
        (path for path in files if path not in retrieval and path not in hidden_paths),
        key=lambda path: hashlib.sha256(
            f"{case.instance_id}:{path}".encode("utf-8")
        ).hexdigest(),
    )
    context = (retrieval + distractors)[:context_files]
    recalled = sum(path in retrieval for path in case.production_files)
    recall = recalled / len(case.production_files) if case.production_files else 0.0
    state = "patch_verified" if gold_ok and test_ok and production_exist else "snapshot_verified"
    # Actual tests require a hermetic project environment and are deliberately
    # not inferred from patch applicability.
    return SnapshotAudit(
        instance_id=case.instance_id, repo=case.repo,
        base_commit=case.base_commit, snapshot_state=state,
        searchable_file_count=len(files),
        python_file_count=sum(path.endswith(".py") for path in files),
        production_files_exist=production_exist,
        gold_patch_applies=gold_ok, test_patch_applies=test_ok,
        test_state="not_run",
        retrieval_files=tuple(retrieval), context_files=tuple(context),
        gold_file_recall=recall,
        reasons=tuple(reasons + ([f"gold: {gold_error}"] if gold_error and not gold_ok else [])
                      + ([f"test: {test_error}"] if test_error and not test_ok else [])),
    )


def write_audits(audits: list[SnapshotAudit], path: str | Path) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    content = "".join(json.dumps(audit.to_dict(), ensure_ascii=False) + "\n" for audit in audits)
    destination.write_text(content, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", default="benchmark/real/selected.jsonl")
    parser.add_argument("--cache-root", default=".local/real_benchmark")
    parser.add_argument("--output", default="benchmark/real/snapshot_audit.jsonl")
    parser.add_argument("--proxy")
    parser.add_argument("--limit", type=int, default=6)
    parser.add_argument("--context-files", type=int, default=40)
    args = parser.parse_args()
    if args.limit < 1 or not 15 <= args.context_files <= 50:
        parser.error("limit must be positive; context-files must be between 15 and 50")

    audits = []
    for case in read_jsonl(args.cases)[:args.limit]:
        print(f"preparing {case.instance_id} ...", flush=True)
        root = download_snapshot(case, args.cache_root, args.proxy)
        audit = audit_snapshot(case, root, args.context_files)
        audits.append(audit)
        print(
            f"  {audit.snapshot_state}: files={audit.searchable_file_count} "
            f"retrieval_gold_recall={audit.gold_file_recall:.0%} "
            f"reasons={list(audit.reasons)}",
            flush=True,
        )
    write_audits(audits, args.output)


if __name__ == "__main__":
    main()
