"""Minimal GitHub Issue/Draft-PR integration with explicit publication inputs."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, build_opener


REPOSITORY_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
BRANCH_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,199}$")


class GitHubError(RuntimeError):
    """GitHub or publication operation failed without exposing credentials."""


def validate_repository(repository: str) -> str:
    repository = repository.strip()
    if repository.endswith(".git"):
        repository = repository[:-4]
    if not REPOSITORY_RE.fullmatch(repository) or ".." in repository:
        raise ValueError("repository 必须是 owner/name")
    return repository


def validate_branch(branch: str) -> str:
    branch = branch.strip()
    if (
        not BRANCH_RE.fullmatch(branch)
        or ".." in branch
        or "//" in branch
        or branch.endswith(("/", ".", ".lock"))
        or branch.startswith("/")
        or "@{" in branch
    ):
        raise ValueError("分支名不合法")
    return branch


def repository_from_remote(remote_url: str) -> str:
    value = remote_url.strip()
    if value.startswith("git@github.com:"):
        return validate_repository(value.split(":", 1)[1])
    parsed = urlparse(value)
    if parsed.hostname != "github.com":
        raise ValueError("当前发布器只允许 github.com remote")
    return validate_repository(parsed.path.strip("/"))


class GitHubAPI:
    def __init__(self, token: str | None = None, *, api_url: str = "https://api.github.com", opener=None):
        self._token = token
        self.api_url = api_url.rstrip("/")
        self._opener = opener or build_opener()

    @classmethod
    def from_env(cls) -> "GitHubAPI":
        return cls(os.environ.get("GITHUB_TOKEN"))

    def require_write_auth(self) -> None:
        if not self._token:
            raise GitHubError("创建 Draft PR 需要通过环境变量 GITHUB_TOKEN 提供 token")

    def _request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "issue-to-pr-agent",
        }
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        request = Request(f"{self.api_url}{path}", data=data, headers=headers, method=method)
        try:
            with self._opener.open(request, timeout=30) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            # GitHub error bodies do not need to be echoed; they may contain user data.
            raise GitHubError(f"GitHub API {method} {path} 返回 HTTP {exc.code}") from exc
        except (URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise GitHubError(f"GitHub API {method} {path} 调用失败：{type(exc).__name__}") from exc

    def get_issue(self, repository: str, number: int) -> dict[str, Any]:
        repository = validate_repository(repository)
        if int(number) <= 0:
            raise ValueError("issue number 必须为正整数")
        result = self._request("GET", f"/repos/{repository}/issues/{int(number)}")
        if "pull_request" in result:
            raise ValueError("给定编号是 Pull Request，不是 Issue")
        return {
            "repository": repository,
            "number": int(result["number"]),
            "title": str(result.get("title") or "").strip(),
            "body": str(result.get("body") or "").strip(),
            "html_url": str(result.get("html_url") or ""),
            "state": str(result.get("state") or ""),
        }

    def find_open_pull(self, repository: str, owner: str, branch: str) -> dict[str, Any] | None:
        repository = validate_repository(repository)
        query = urlencode({"state": "open", "head": f"{owner}:{branch}"})
        result = self._request("GET", f"/repos/{repository}/pulls?{query}")
        return result[0] if result else None

    def create_draft_pr(self, repository: str, *, title: str, body: str,
                        head: str, base: str) -> dict[str, Any]:
        self.require_write_auth()
        repository = validate_repository(repository)
        result = self._request("POST", f"/repos/{repository}/pulls", {
            "title": title, "body": body, "head": head, "base": base, "draft": True,
        })
        if result.get("draft") is not True:
            raise GitHubError("GitHub 未返回 draft=true，拒绝把结果视为已完成")
        return {
            "number": int(result["number"]),
            "html_url": str(result["html_url"]),
            "draft": True,
            "head": head,
            "base": base,
        }


def _git(repo: Path, argv: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *argv], check=check, capture_output=True,
        text=True, encoding="utf-8", errors="replace",
    )


def _diff(repo: Path, base_commit: str) -> str:
    return _git(repo, ["diff", "--no-ext-diff", "--binary", base_commit]).stdout.strip()


@dataclass
class GitHubDraftPRPublisher:
    api: GitHubAPI

    def publish(self, *, workspace: str | Path, base_commit: str,
                request: dict[str, Any], expected_diff_sha256: str) -> dict[str, Any]:
        # Fail before any branch/commit/push mutation when publication credentials
        # are unavailable.
        self.api.require_write_auth()
        repo = Path(workspace).resolve()
        repository = validate_repository(request["repository"])
        base_branch = validate_branch(request["base_branch"])
        head_branch = validate_branch(request["head_branch"])
        remote = str(request.get("remote") or "origin")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", remote):
            raise ValueError("remote 名称不合法")

        status = _git(repo, ["status", "--porcelain", "--untracked-files=all"]).stdout.splitlines()
        untracked = [line for line in status if line.startswith("??")]
        if untracked:
            raise GitHubError("发布前发现未审批的未跟踪文件")
        current_diff = _diff(repo, base_commit)
        if hashlib.sha256(current_diff.encode("utf-8")).hexdigest() != expected_diff_sha256:
            raise GitHubError("发布前 diff 已变化，旧审批失效")

        remote_url = _git(repo, ["remote", "get-url", remote]).stdout.strip()
        if repository_from_remote(remote_url).lower() != repository.lower():
            raise GitHubError("发布请求中的 repository 与 Git remote 不一致")

        head_commit = _git(repo, ["rev-parse", "HEAD"]).stdout.strip()
        branch_result = _git(repo, ["symbolic-ref", "--quiet", "--short", "HEAD"], check=False)
        current_branch = branch_result.stdout.strip() if branch_result.returncode == 0 else None
        if head_commit == base_commit:
            if current_branch is None:
                _git(repo, ["checkout", "-b", head_branch])
            elif current_branch != head_branch:
                raise GitHubError("基础提交已位于其他分支，拒绝覆盖")
            _git(repo, ["add", "-u"])
            staged = _git(repo, ["diff", "--cached", "--quiet"], check=False)
            if staged.returncode == 0:
                raise GitHubError("没有可提交的已审批变更")
            _git(repo, ["commit", "-m", request["commit_message"]])
        elif current_branch != head_branch:
            raise GitHubError("任务 worktree 已位于其他提交或分支，拒绝发布")

        if hashlib.sha256(_diff(repo, base_commit).encode("utf-8")).hexdigest() != expected_diff_sha256:
            raise GitHubError("commit 后 diff 与审批对象不一致")

        _git(repo, ["push", "--set-upstream", remote, f"HEAD:refs/heads/{head_branch}"])
        owner = repository.split("/", 1)[0]
        existing = self.api.find_open_pull(repository, owner, head_branch)
        if existing:
            if existing.get("draft") is not True:
                raise GitHubError("相同分支已有非 Draft PR，拒绝复用")
            existing_base = (existing.get("base") or {}).get("ref")
            if existing_base and existing_base != base_branch:
                raise GitHubError("相同分支的 Draft PR 指向不同 base branch")
            if existing.get("title") not in {None, request["title"]}:
                raise GitHubError("已有 Draft PR 的标题与审批对象不一致")
            if existing.get("body") not in {None, request["body"]}:
                raise GitHubError("已有 Draft PR 的正文与审批对象不一致")
            result = {
                "number": int(existing["number"]),
                "html_url": str(existing["html_url"]),
                "draft": True,
                "head": head_branch,
                "base": base_branch,
                "reused": True,
            }
        else:
            result = self.api.create_draft_pr(
                repository, title=request["title"], body=request["body"],
                head=head_branch, base=base_branch,
            )
            result["reused"] = False
        result["commit"] = _git(repo, ["rev-parse", "HEAD"]).stdout.strip()
        result["repository"] = repository
        return result
