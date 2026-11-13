"""Select bounded GitHub candidates and reproduce local pytest failures."""

from pathlib import Path
import re
import shutil
import subprocess
import uuid

from core.candidate import candidate, git_run
from core.github import GitHubError, repository_from_remote, validate_repository
from core.verification import verify_public


def repository_name(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("请填写 GitHub 仓库")
    value = value.strip()
    name = repository_from_remote(value) if "://" in value else validate_repository(value)
    if "." in name.split("/"):
        raise ValueError("请填写有效的 GitHub 仓库")
    return name


def recommend_issues(github, repository: str) -> list[dict]:
    repository = repository_name(repository)
    ranked = []
    for issue in github.list_open_issues(repository):
        body = issue["body"]
        if issue["assigned"] or not body:
            continue
        labels = {" ".join(re.split(r"[-:_\s]+", name.lower())) for name in issue["labels"]}
        reasons = []
        score = 0
        if labels & {"bug", "type bug", "regression"}:
            reasons.append("标记为缺陷或回归")
            score += 4
        if "good first issue" in labels:
            reasons.append("维护者标记为适合入门")
            score += 3
        if re.search(r"reproduc|steps to|traceback|复现|重现", body, re.IGNORECASE):
            reasons.append("正文有复现或异常线索")
            score += 3
        if re.search(r"\b[\w./-]+\.py\b|\bpytest\b", body):
            reasons.append("正文提及 Python 文件或测试")
            score += 2
        if reasons:
            ranked.append((score, {"number": issue["number"], "title": issue["title"],
                                   "url": f"https://github.com/{repository}/issues/{issue['number']}",
                                   "excerpt": body[:450], "reasons": reasons}))
    ranked.sort(key=lambda item: (-item[0], item[1]["number"]))
    return [issue for _, issue in ranked[:5]]


def clone_public_repository(repository: str, directory: Path) -> Path:
    repository = repository_name(repository)
    directory = directory.resolve()
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / uuid.uuid4().hex
    try:
        git_run(directory, ["-c", "credential.helper=", "-c", "protocol.file.allow=never",
                            "-c", "protocol.ext.allow=never", "clone", "--depth", "1", "--no-tags",
                            "--", f"https://github.com/{repository}.git", str(destination)])
    except (OSError, subprocess.SubprocessError):
        if destination.is_dir() and destination.resolve() == destination and destination.parent == directory:
            shutil.rmtree(destination)
        raise GitHubError("无法浅克隆公开仓库，请检查仓库名和网络，或填写已有仓库路径") from None
    return destination


def scan_pytest(repo: Path, sandbox) -> dict:
    repo = repo.resolve()
    if Path(git_run(repo, ["rev-parse", "--show-toplevel"]).stdout.strip()).resolve() != repo:
        raise ValueError("请填写 Git 仓库根目录")
    if git_run(repo, ["status", "--porcelain"]).stdout:
        raise ValueError("请从干净的 Git 工作区扫描")
    base = git_run(repo, ["rev-parse", "HEAD"]).stdout.strip()
    patch = candidate(repo, base)
    if patch.diff:
        raise ValueError("工作区在扫描前发生变化")
    runs = []
    for _ in range(2):
        code, output, evidence = verify_public(repo, [], sandbox, base_commit=base, expected_tree=patch.tree)
        summaries = re.findall(r"^FAILED[ \t]+(.+)$", output, flags=re.MULTILINE)
        failures = {summary.split(" - ", 1)[0].strip() for summary in summaries if "::" in summary}
        runs.append(failures if code == 1 and evidence["snapshot_unchanged"] else set())
        if not runs[-1]:
            break
    failures = sorted(runs[0] & runs[1])[:10] if len(runs) == 2 else []
    status = "reproduced" if failures else "passed" if code == 0 and len(runs) == 1 else "inconclusive"
    return {"base_commit": base, "failures": failures, "status": status}
