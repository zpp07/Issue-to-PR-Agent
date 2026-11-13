"""Freeze public validation input and check evidence outside the test process."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import posixpath
import secrets
import shutil
import stat
import subprocess
import sys
from tempfile import TemporaryDirectory

from core.candidate import candidate


_HARNESS = r"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

with tempfile.TemporaryDirectory() as temp:
    report = Path(temp) / "junit.xml"
    collection = Path(temp) / "collection.json"
    env = dict(os.environ, PYTEST_DISABLE_PLUGIN_AUTOLOAD="1")
    env.pop("PYTEST_ADDOPTS", None)
    options = ["-o", "addopts=", "-p", "no:cacheprovider", "-q"] + PYTEST_OPTIONS
    collector = '''
import json
from pathlib import Path
import sys
import pytest
class Collection:
    def pytest_collection_finish(self, session):
        Path(sys.argv[1]).write_text(json.dumps([item.nodeid for item in session.items]), encoding="utf-8")
raise SystemExit(pytest.main(sys.argv[2:], plugins=[Collection()]))
'''
    collected = subprocess.run(
        [sys.executable, "-I", "-B", "-c", collector, str(collection),
         *options, "--collect-only", *sys.argv[2:]], env=env,
    )
    if collected.returncode:
        sys.exit(collected.returncode)
    result = subprocess.run(
        [sys.executable, "-I", "-B", "-m", "pytest", *options, "--junitxml", str(report), *sys.argv[2:]], env=env,
    )
    try:
        if (not report.is_file() or report.stat().st_size > 4_000_000
                or not collection.is_file() or collection.stat().st_size > 4_000_000):
            raise ValueError("missing or oversized report")
        expected = json.loads(collection.read_text(encoding="utf-8"))
        suites = ET.parse(report).getroot().findall("./testsuite")
        cases = [case for suite in suites for case in suite.findall("testcase")]
        declared = sum(int(suite.attrib["tests"]) for suite in suites)
        if not isinstance(expected, list) or not expected or declared != len(cases) or declared != len(expected):
            raise ValueError("inconsistent report")
        passed = sum(not any(case.find(tag) is not None for tag in
                             ("failure", "error", "skipped")) for case in cases)
        failed = sum(int(suite.attrib["failures"]) + int(suite.attrib["errors"]) for suite in suites)
        print(sys.argv[1] + json.dumps({"cases": declared, "collected": len(expected),
                                      "passed": passed, "failed": failed}), flush=True)
        sys.exit(result.returncode or (0 if passed > 0 and failed == 0 else 5))
    except (OSError, ValueError, KeyError, ET.ParseError):
        sys.exit(result.returncode or 5)
"""


def _paths(workspace: Path) -> list[str]:
    result = subprocess.run(
        ["git", "-C", str(workspace), "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        capture_output=True, check=True, timeout=30,
    )
    return sorted(set(result.stdout.decode("utf-8").split("\0")) - {""})


def _gitlinks(workspace: Path) -> dict[str, bytes]:
    result = subprocess.run(
        ["git", "-C", str(workspace), "ls-files", "--stage", "-z"],
        capture_output=True, check=True, timeout=30,
    )
    links = {}
    for entry in result.stdout.split(b"\0"):
        if entry.startswith(b"160000 "):
            metadata, relative = entry.split(b"\t", 1)
            _, commit, stage = metadata.split()
            if stage != b"0":
                raise ValueError("公开验证拒绝存在冲突的 Git 子模块")
            links[relative.decode("utf-8")] = commit
    return links


def _link_target(workspace: Path, path: Path, relative: str) -> str:
    target = os.readlink(path)
    joined = posixpath.normpath(posixpath.join(posixpath.dirname(relative), target))
    if (not target or "\\" in target or ":" in target or target.startswith("/")
            or joined == ".." or joined.startswith("../")
            or any(part.lower() == ".git" for part in target.split("/"))):
        raise ValueError(f"公开验证拒绝指向工作区外或 Git 元数据的链接：{relative}")
    try:
        resolved = path.resolve().relative_to(workspace)
    except (OSError, RuntimeError, ValueError):
        raise ValueError(f"公开验证无法安全解析链接：{relative}") from None
    if any(part.lower() == ".git" for part in resolved.parts):
        raise ValueError(f"公开验证拒绝指向 Git 元数据的链接：{relative}")
    return target


def _manifest(workspace: Path, paths: list[str], gitlinks: dict[str, bytes],
              snapshot: Path | None = None) -> tuple[str, list[str]]:
    digest = hashlib.sha256()
    present = []
    for relative in paths:
        path = workspace / relative
        if path.parent.resolve() != path.parent:
            raise ValueError(f"公开验证拒绝通过链接目录读取文件：{relative}")
        try:
            mode = path.lstat().st_mode
        except FileNotFoundError:
            continue
        present.append(relative)
        if relative in gitlinks:
            if not stat.S_ISDIR(mode) or path.resolve() != path or any(path.iterdir()):
                raise ValueError(f"公开验证仅支持未初始化的空子模块目录：{relative}")
            kind, data = b"gitlink", gitlinks[relative]
        elif stat.S_ISLNK(mode):
            target = _link_target(workspace, path, relative)
            kind, data = b"link", os.fsencode(target)
        elif stat.S_ISREG(mode):
            kind, data = b"file", path.read_bytes()
        else:
            raise ValueError(f"公开验证暂不支持非普通文件：{relative}")
        digest.update(relative.encode("utf-8") + b"\0" + kind + b"\0" + hashlib.sha256(data).digest())
        if snapshot is not None:
            dest = snapshot / relative
            dest.parent.mkdir(parents=True, exist_ok=True)
            if kind == b"gitlink":
                dest.mkdir()
            elif kind == b"link":
                dest.symlink_to(target, target_is_directory=path.is_dir())
            else:
                dest.write_bytes(data)
                shutil.copymode(path, dest)
    return digest.hexdigest(), present


def verify_public(workspace: Path, test_paths: list[str], sandbox, *,
                  base_commit: str | None = None, expected_tree: str | None = None,
                  pythonpath: tuple[str, ...] = (".",), load_conftest: bool = True) -> tuple[int, str, dict]:
    """Report counts detect ordinary empty/aborted runs, not malicious pytest."""
    marker = f"PUBLIC_JUNIT_{secrets.token_hex(12)}="
    python = "python" if sandbox else sys.executable
    options = ["-o", "pythonpath=" + " ".join(pythonpath)]
    if not load_conftest:
        options.append("--noconftest")
    harness = f"PYTEST_OPTIONS = {options!r}\n" + _HARNESS
    argv = [python, "-I", "-B", "-c", harness, marker, *test_paths]
    paths = _paths(workspace)
    gitlinks = _gitlinks(workspace)
    with TemporaryDirectory(prefix="issue-to-pr-verify-") as directory:
        snapshot = Path(directory).resolve()
        # The Docker runner's unprivileged UID needs to traverse this directory.
        snapshot.chmod(0o755)
        before, present = _manifest(workspace, paths, gitlinks, snapshot)
        if (before, present) != _manifest(snapshot, paths, gitlinks):
            return 1, "公开验证快照与工作区清单不一致", {
                "snapshot_sha256": before, "snapshot_unchanged": False,
            }
        if base_commit and candidate(workspace, base_commit, snapshot=snapshot).tree != expected_tree:
            return 1, "公开验证快照与候选 Git tree 不一致", {
                "snapshot_sha256": before, "snapshot_unchanged": False,
            }
        if sandbox:
            result = sandbox.run(snapshot, argv, workdir=snapshot, read_only_workspace=True, timeout=60)
            code, output = result.exit_code, result.output
            image_id = getattr(result, "image_id", None)
        else:
            try:
                result = subprocess.run(argv, cwd=snapshot, capture_output=True, text=True,
                                        encoding="utf-8", errors="replace", timeout=60)
                code, output = result.returncode, result.stdout + result.stderr
            except subprocess.TimeoutExpired:
                code, output = 124, "可信本地验证超时"
            image_id = None
        snapshot_paths = sorted(
            relative for path in snapshot.rglob("*")
            if (relative := path.relative_to(snapshot).as_posix()) in gitlinks
            or path.is_file() or path.is_symlink()
        )
        try:
            unchanged = (snapshot_paths == present
                         and (before, present) == _manifest(snapshot, paths, gitlinks)
                         and paths == _paths(workspace)
                         and gitlinks == _gitlinks(workspace)
                         and (before, present) == _manifest(workspace, paths, gitlinks))
        except (OSError, RuntimeError, ValueError):
            unchanged = False
    matches = [line[len(marker):] for line in output.splitlines() if line.startswith(marker)]
    try:
        counts = json.loads(matches[0]) if len(matches) == 1 else {}
    except ValueError:
        counts = {}
    valid = (isinstance(counts, dict) and type(counts.get("cases")) is int
             and type(counts.get("collected")) is int and counts["collected"] == counts["cases"]
             and type(counts.get("passed")) is int and type(counts.get("failed")) is int
             and counts["cases"] >= counts["passed"] > 0 and counts["failed"] == 0)
    evidence = {"selected_paths": test_paths, "snapshot_sha256": before, "snapshot_unchanged": unchanged,
                "junit": counts if valid else None, "runner_image_id": image_id,
                "execution_mode": "docker" if sandbox else "trusted_local"}
    return code or (0 if unchanged and valid else 1), output or "(无测试输出)", evidence
