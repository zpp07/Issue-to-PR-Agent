"""State machine for a single-user local Issue-to-PR workflow."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import subprocess
from typing import Any

from core.audit import TaskStore
from core.github import GitHubAPI, GitHubDraftPRPublisher, validate_branch, validate_repository
from core.memory import TaskMemory
from core.policy import CommandPolicy
from core.tools import make_executor


def _digest(value: str | dict[str, Any]) -> str:
    if isinstance(value, dict):
        value = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _git(repo: Path, argv: list[str]) -> str:
    result = subprocess.run(["git", "-C", str(repo), *argv], capture_output=True, text=True,
                            encoding="utf-8", errors="replace", check=True)
    return result.stdout.strip()


@dataclass(frozen=True)
class TaskContext:
    task_id: str
    workspace: Path
    base_commit: str


class IssueToPRWorkflow:
    """Explicit state transitions and immutable approval subjects.

    Commit/push/Draft-PR publication is available only after the exact reviewed
    diff and an exact publication request receive separate approvals.
    """

    def __init__(self, store: TaskStore, workspace_root: str | Path):
        self.store = store
        self.workspace_root = Path(workspace_root).resolve()
        self.workspace_root.mkdir(parents=True, exist_ok=True)
        self.policy = CommandPolicy()
        self.memory = TaskMemory(store)

    def create_task(self, repo_path: str | Path, issue: str) -> TaskContext:
        repo = Path(repo_path).resolve()
        dirty = _git(repo, ["status", "--porcelain"])
        if dirty:
            raise RuntimeError(
                "拒绝从含未提交或未跟踪文件的仓库创建可复现任务；"
                "请先提交/清理变更，或显式实现 working-tree snapshot 模式。"
            )
        base_commit = _git(repo, ["rev-parse", "HEAD"])
        # Allocate id first so a worktree directory is never shared by tasks.
        task_id = hashlib.sha256(f"{repo}|{issue}|{base_commit}".encode()).hexdigest()[:16]
        workspace = self.workspace_root / task_id
        if workspace.exists():
            raise FileExistsError(f"任务工作区已存在：{workspace}")
        subprocess.run(["git", "-C", str(repo), "worktree", "add", "--detach", str(workspace), base_commit],
                       check=True, capture_output=True, text=True, encoding="utf-8", errors="replace")
        stored_id = self.store.create_task(issue, str(repo), str(workspace), base_commit)
        # Store uses a random opaque id; rename worktree to neither expose nor depend on it.
        self.store.event(stored_id, "worktree_ready", {"head": base_commit, "path": str(workspace)})
        return TaskContext(stored_id, workspace, base_commit)

    def submit_plan(self, task_id: str, plan: dict[str, Any]) -> str:
        required = {"goal", "affected_files", "steps", "risks", "allowed_commands"}
        if set(plan) != required or not isinstance(plan["allowed_commands"], list):
            raise ValueError(f"计划必须恰好包含 {sorted(required)}")
        for argv in plan["allowed_commands"]:
            decision = self.policy.evaluate(argv)
            if decision.level.value == "deny":
                raise ValueError(f"计划含被拒绝命令 {argv!r}：{decision.reason}")
        subject_hash = _digest(plan)
        self.store.artifact(task_id, "plan", json.dumps(plan, ensure_ascii=False, indent=2), subject_hash)
        self.store.set_status(task_id, "awaiting_plan_approval")
        self.store.event(task_id, "plan_submitted", {"plan_sha256": subject_hash})
        return subject_hash

    def approve_plan(self, task_id: str, plan_hash: str, actor: str, decision: str, reason: str | None = None) -> None:
        plan = self.store.latest_artifact(task_id, "plan")
        if plan["sha256"] != plan_hash:
            raise ValueError("计划哈希不匹配；请审批当前版本")
        approval = self.store.approve(task_id, "plan", plan_hash, decision, actor, reason)
        self.store.set_status(task_id, "ready_to_execute" if decision == "approved" else "plan_rejected")
        self.memory.capture_approval(approval)
        if decision == "approved":
            self.memory.capture_approved_plan(task_id)

    def executor_for(self, task_id: str, workdir: str | Path | None = None,
                     command_runner=None, code_search=None, writable_files=None):
        task = self.store.task(task_id)
        if task["status"] != "ready_to_execute":
            raise RuntimeError("任务尚未获得计划审批")
        plan = self.store.latest_artifact(task_id, "plan")
        if not self.store.is_approved(task_id, "plan", plan["sha256"]):
            raise RuntimeError("当前计划没有有效审批")

        task_workspace = Path(task["workspace_path"]).resolve()
        execution_workdir = Path(workdir).resolve() if workdir else task_workspace
        if execution_workdir != task_workspace and task_workspace not in execution_workdir.parents:
            raise ValueError("执行目录必须位于任务 worktree 内")

        def command_approval(argv, policy_decision) -> bool:
            command_hash = _digest({"argv": argv})
            self.store.event(task_id, "high_risk_command_requested", {
                "argv": argv, "command_sha256": command_hash, "reason": policy_decision.reason,
            })
            return self.store.is_approved(task_id, "command", command_hash)

        return make_executor(
            execution_workdir, policy=self.policy,
            approval_callback=command_approval, command_runner=command_runner,
            code_search=code_search, writable_files=writable_files,
        )

    def approve_command(self, task_id: str, argv: list[str], actor: str, decision: str, reason: str | None = None) -> str:
        subject_hash = _digest({"argv": argv})
        self.store.approve(task_id, "command", subject_hash, decision, actor, reason)
        return subject_hash

    def record_execution(self, task_id: str, test_report: str, reviewer_report: dict[str, Any]) -> dict[str, str]:
        task = self.store.task(task_id)
        if task["status"] != "ready_to_execute":
            raise RuntimeError("任务不在可执行状态")
        workspace = Path(task["workspace_path"])
        diff = _git(workspace, ["diff", "--no-ext-diff", "--binary", task["base_commit"]])
        diff_hash, test_hash = _digest(diff), _digest(test_report)
        report = {"base_commit": task["base_commit"], "diff_sha256": diff_hash,
                  "test_report_sha256": test_hash, "reviewer": reviewer_report}
        self.store.artifact(task_id, "diff", diff, diff_hash)
        self.store.artifact(task_id, "test_report", test_report, test_hash)
        self.store.artifact(task_id, "review_report", json.dumps(reviewer_report, ensure_ascii=False), _digest(reviewer_report))
        validation_passed = bool(reviewer_report.get("passed")) and reviewer_report.get("tests_exit_code") == 0
        if not validation_passed:
            failure = {
                "reviewer_passed": bool(reviewer_report.get("passed")),
                "tests_exit_code": reviewer_report.get("tests_exit_code"),
                "test_report_sha256": test_hash,
            }
            self.store.set_status(task_id, "validation_failed")
            self.store.event(task_id, "validation_failed", failure)
            self.memory.capture_failure(
                task_id,
                f"独立验证失败：reviewer_passed={failure['reviewer_passed']}，"
                f"tests_exit_code={failure['tests_exit_code']}",
                self.store.latest_artifact(task_id, "test_report"),
            )
            return {"diff_sha256": diff_hash, "test_report_sha256": test_hash,
                    "validation": "failed"}
        review_hash = _digest(report)
        self.store.artifact(task_id, "diff_review", json.dumps(report, ensure_ascii=False, indent=2), review_hash)
        self.store.set_status(task_id, "awaiting_diff_approval")
        self.store.event(task_id, "diff_review_ready", {**report, "review_sha256": review_hash})
        return {"diff_sha256": diff_hash, "test_report_sha256": test_hash, "review_sha256": review_hash}

    def reconcile_failed_validation(self, task_id: str, reason: str) -> None:
        """Repair tasks produced before the validation gate existed."""
        task = self.store.task(task_id)
        if task["status"] != "awaiting_diff_approval":
            raise RuntimeError("只有旧版 awaiting_diff_approval 任务可以回填验证失败")
        self.store.set_status(task_id, "validation_failed")
        self.store.event(task_id, "validation_reconciled", {"reason": reason})

    def approve_diff(self, task_id: str, review_hash: str, actor: str, decision: str, reason: str | None = None) -> None:
        review = self.store.latest_artifact(task_id, "diff_review")
        if review["sha256"] != review_hash:
            raise ValueError("diff 审批对象已过期；请审批最新 diff/test 组合")
        approval = self.store.approve(task_id, "diff", review_hash, decision, actor, reason)
        self.store.set_status(task_id, "approved_for_pr" if decision == "approved" else "diff_rejected")
        self.memory.capture_approval(approval)

    def prepare_draft_pr(
        self, task_id: str, *, repository: str, base_branch: str,
        head_branch: str, title: str, body: str | None = None,
        remote: str = "origin", commit_message: str | None = None,
    ) -> dict[str, Any]:
        """Freeze an exact publication request without changing Git or GitHub state."""
        task = self.store.task(task_id)
        if task["status"] not in {"approved_for_pr", "publish_rejected"}:
            raise RuntimeError("任务尚未获得 diff 审批")
        review_artifact = self.store.latest_artifact(task_id, "diff_review")
        if not self.store.is_approved(task_id, "diff", review_artifact["sha256"]):
            raise RuntimeError("当前 diff/test 组合没有有效审批")
        review = json.loads(review_artifact["content"])
        diff_artifact = self.store.latest_artifact(task_id, "diff")
        current_diff = _git(
            Path(task["workspace_path"]),
            ["diff", "--no-ext-diff", "--binary", task["base_commit"]],
        )
        current_hash = _digest(current_diff)
        if not current_diff or current_hash != diff_artifact["sha256"] or current_hash != review["diff_sha256"]:
            raise ValueError("当前 worktree diff 与已审批对象不一致")

        repository = validate_repository(repository)
        base_branch, head_branch = validate_branch(base_branch), validate_branch(head_branch)
        if not re_fullmatch_remote(remote):
            raise ValueError("remote 名称不合法")
        title = " ".join(title.split()).strip()
        if not title or len(title) > 256:
            raise ValueError("PR title 必须为 1–256 个字符")
        commit_message = " ".join((commit_message or title).split()).strip()
        if not commit_message or len(commit_message) > 256:
            raise ValueError("commit message 必须为 1–256 个字符")
        if body is None:
            plan = json.loads(self.store.latest_artifact(task_id, "plan")["content"])
            test_report = self.store.latest_artifact(task_id, "test_report")["content"].strip()
            body = (
                "## Summary\n\n"
                f"{plan['goal']}\n\n"
                "## Validation\n\n"
                f"```text\n{test_report[:4000]}\n```\n\n"
                "## Audit\n\n"
                f"- Base commit: `{task['base_commit']}`\n"
                f"- Approved diff SHA-256: `{current_hash}`\n"
                f"- Test report SHA-256: `{review['test_report_sha256']}`\n"
            )
        if not isinstance(body, str) or not body.strip() or len(body) > 65_000:
            raise ValueError("PR body 必须为 1–65000 个字符")

        request = {
            "schema_version": 1,
            "repository": repository,
            "remote": remote,
            "base_branch": base_branch,
            "head_branch": head_branch,
            "title": title,
            "body": body,
            "commit_message": commit_message,
            "draft": True,
            "base_commit": task["base_commit"],
            "diff_review_sha256": review_artifact["sha256"],
            "diff_sha256": current_hash,
            "test_report_sha256": review["test_report_sha256"],
        }
        request_hash = _digest(request)
        self.store.artifact(
            task_id, "draft_pr_request",
            json.dumps(request, ensure_ascii=False, indent=2, sort_keys=True), request_hash,
        )
        self.store.set_status(task_id, "awaiting_publish_approval")
        self.store.event(task_id, "draft_pr_request_ready", {
            "request_sha256": request_hash, "repository": repository,
            "base_branch": base_branch, "head_branch": head_branch, "draft": True,
        })
        return {"request_sha256": request_hash, "request": request}

    def approve_publish(self, task_id: str, request_hash: str, actor: str,
                        decision: str, reason: str | None = None) -> None:
        task = self.store.task(task_id)
        if task["status"] != "awaiting_publish_approval":
            raise RuntimeError("任务不在发布审批状态")
        request = self.store.latest_artifact(task_id, "draft_pr_request")
        if request["sha256"] != request_hash:
            raise ValueError("发布审批对象已过期；请审批最新 Draft PR 请求")
        approval = self.store.approve(task_id, "publish", request_hash, decision, actor, reason)
        self.store.set_status(task_id, "ready_to_publish" if decision == "approved" else "publish_rejected")
        self.memory.capture_approval(approval)

    def publish_draft_pr(self, task_id: str, request_hash: str, publisher=None) -> dict[str, Any]:
        """Commit, push and create a Draft PR after both approval gates."""
        task = self.store.task(task_id)
        if task["status"] != "ready_to_publish":
            raise RuntimeError("任务尚未获得发布审批")
        artifact = self.store.latest_artifact(task_id, "draft_pr_request")
        if artifact["sha256"] != request_hash:
            raise ValueError("Draft PR 请求哈希不匹配")
        if not self.store.is_approved(task_id, "publish", request_hash):
            raise RuntimeError("当前 Draft PR 请求没有有效发布审批")
        request = json.loads(artifact["content"])
        if request.get("draft") is not True:
            raise ValueError("只允许创建 Draft PR")
        current_diff = _git(
            Path(task["workspace_path"]),
            ["diff", "--no-ext-diff", "--binary", task["base_commit"]],
        )
        if _digest(current_diff) != request["diff_sha256"]:
            self.store.set_status(task_id, "ready_to_execute")
            self.store.event(task_id, "draft_pr_request_invalidated", {
                "request_sha256": request_hash, "reason": "diff_changed",
            })
            raise ValueError("发布前 diff 已变化；请重新执行 diff 与发布审批")

        publisher = publisher or GitHubDraftPRPublisher(GitHubAPI.from_env())
        try:
            result = publisher.publish(
                workspace=task["workspace_path"], base_commit=task["base_commit"],
                request=request, expected_diff_sha256=request["diff_sha256"],
            )
        except Exception as exc:
            self.store.event(task_id, "draft_pr_publish_failed", {
                "request_sha256": request_hash, "error_type": type(exc).__name__,
            })
            raise
        if (
            not isinstance(result, dict)
            or result.get("draft") is not True
            or str(result.get("repository", "")).lower() != request["repository"].lower()
        ):
            self.store.event(task_id, "draft_pr_publish_failed", {
                "request_sha256": request_hash, "error_type": "InvalidPublisherResult",
            })
            raise RuntimeError("发布器没有返回匹配 repository 的 Draft PR")
        result_hash = _digest(result)
        self.store.artifact(
            task_id, "draft_pr", json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True),
            result_hash,
        )
        self.store.set_status(task_id, "draft_pr_created")
        self.store.event(task_id, "draft_pr_created", {
            "request_sha256": request_hash, "result_sha256": result_hash,
            "repository": request["repository"], "number": result.get("number"),
            "html_url": result.get("html_url"), "draft": True,
        })
        return result


def re_fullmatch_remote(value: str) -> bool:
    """Keep remote names out of option/argument ambiguity."""
    return bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", value))
