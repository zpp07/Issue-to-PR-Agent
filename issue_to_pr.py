"""Application adapter that joins planner, Coder/Reviewer, and Phase-2 workflow."""
from __future__ import annotations

from pathlib import Path
import subprocess
import sys
from typing import Any

from core.agent import run_agent
from core.client import resolve_model
from core.github import GitHubAPI
from core.policy import CommandPolicy
from core.search import build_code_search
from core.tools import build_tool_registry, make_executor
from core.workflow import IssueToPRWorkflow, TaskContext
from coder_reviewer import multi_agent_review

PLANNER_TOOLS = build_tool_registry("search_code", "read_file", "plan_finish")
SEARCH_CODER_TOOLS = build_tool_registry(
    "search_code", "read_file", "replace_text", "write_file", "run_command", "finish",
)
SEARCH_REVIEWER_TOOLS = build_tool_registry("search_code", "read_file", "review_finish")
PLANNER_PROMPT = """你是 Python 项目的变更规划员。先阅读与 issue 相关的文件，
优先使用 search_code 找到符号、调用方、测试和文档契约，再用 read_file 精读证据。
只做计划，绝不修改代码或运行命令。最后必须调用 plan_finish，给出：目标、可能受影响
文件、可验证的步骤、风险和拟执行的 argv 命令。允许命令只限于：
["python", "-m", "pytest", "-q"]、["pytest", "-q"]、["ruff", "check", "."]、
["git", "status"]、["git", "diff"]。绝不能运行 python 文件、shell、网络或写 Git 命令。
不确定的文件或风险应明确写出，不要编造。"""

# This is a limit on browser-visible progress events, not on audit/status events.
MAX_PROGRESS_EVENTS_PER_TASK = 2000
_PUBLIC_ACTIONS = {
    "search_code", "read_file", "read_symbol", "write_file", "replace_text",
    "run_command", "finish", "review_finish", "plan_finish", "progress_decision",
    "no_progress_intervention", "controller_finish", "candidate_patch_missing",
    "llm_error", "text", "agent_exit",
}
_PUBLIC_STATUSES = {"ok", "error", "blocked", "rejected"}
_PUBLIC_EXIT_REASONS = {
    "llm_error", "max_tokens", "max_cost", "free_text", "no_progress",
    "verified_patch", "finish", "max_steps_unverified_patch", "max_steps_no_patch",
}


def _public_agent_event(record: dict[str, Any]) -> dict[str, Any]:
    """Project a raw Agent trace record onto the local HTTP event schema."""
    action = record.get("action")
    phase = record.get("phase")
    payload: dict[str, Any] = {
        "phase": phase if isinstance(phase, str) and phase in {"coder", "reviewer"} else "unknown",
        "action": action if isinstance(action, str) and action in _PUBLIC_ACTIONS else "other",
    }
    for key in ("round", "step", "result_chars", "command_exit_code",
                "patch_revision", "remaining_steps", "threshold"):
        value = record.get(key)
        if type(value) is int:
            payload[key] = value
    status = record.get("status")
    if isinstance(status, str) and status in _PUBLIC_STATUSES:
        payload["status"] = status
    exit_reason = record.get("exit_reason")
    if isinstance(exit_reason, str) and exit_reason in _PUBLIC_EXIT_REASONS:
        payload["exit_reason"] = exit_reason
    for key in ("mutation_applied", "retryable"):
        value = record.get(key)
        if type(value) is bool:
            payload[key] = value

    args = record.get("args")
    if isinstance(args, dict):
        path = args.get("path")
        if isinstance(path, str) and len(path) <= 256:
            candidate = Path(path)
            if (not candidate.is_absolute() and not candidate.drive
                    and ".." not in path.replace("\\", "/").split("/")):
                payload["path"] = path
        for key in ("start_line", "end_line"):
            value = args.get(key)
            if type(value) is int and value > 0:
                payload[key] = value
        choice = args.get("choice")
        if choice in ("minimal_edit", "targeted_read", "abandon", "finish"):
            payload["choice"] = choice
    return payload


class PlanningUnavailable(RuntimeError):
    """The task/worktree exists, but the LLM plan can safely be retried later."""


def _validate_plan(plan: Any) -> dict[str, Any]:
    if not isinstance(plan, dict):
        raise ValueError("Planner 未返回结构化计划")
    required = {"goal", "affected_files", "steps", "risks", "allowed_commands"}
    if set(plan) != required:
        raise ValueError("Planner 输出字段不符合计划协议")
    if not isinstance(plan["goal"], str) or not plan["goal"].strip():
        raise ValueError("Planner 目标必须是非空文本")
    for field in ("affected_files", "steps", "risks"):
        value = plan[field]
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise ValueError(f"Planner 的 {field} 必须是文本列表")
    if not isinstance(plan["allowed_commands"], list):
        raise ValueError("Planner 的 allowed_commands 必须是 argv 列表")
    policy = CommandPolicy()
    for argv in plan["allowed_commands"]:
        if not isinstance(argv, list) or not argv or not all(
            isinstance(part, str) and part for part in argv
        ):
            raise ValueError("Planner 的每条命令必须是非空 argv 字符串列表")
        if policy.evaluate(argv).level.value == "deny":
            raise ValueError(f"Planner 请求了被拒绝的命令：{argv!r}")
    return plan


def _has_candidate_patch(workspace: Path, base_commit: str) -> bool:
    """Check the tracked diff that record_execution and PR publication use."""
    result = subprocess.run(
        ["git", "-C", str(workspace), "diff", "--no-ext-diff", "--quiet", base_commit],
        capture_output=True, check=False,
    )
    if result.returncode not in (0, 1):
        raise RuntimeError("无法检查任务工作区的候选补丁")
    return result.returncode == 1


class IssueToPRService:
    def __init__(self, workflow: IssueToPRWorkflow, client, model: str | None = None, sandbox=None):
        self.workflow = workflow
        self.client = client
        self.model = resolve_model(model)
        self.sandbox = sandbox

    def create_and_plan(self, repo_path: str | Path, issue: str) -> tuple[TaskContext, dict[str, Any], str]:
        context = self.workflow.create_task(repo_path, issue)
        plan, plan_hash = self.plan_existing(context.task_id)
        return context, plan, plan_hash

    def create_from_github_issue(
        self, repo_path: str | Path, repository: str, issue_number: int,
        github: GitHubAPI | None = None,
    ) -> tuple[TaskContext, dict[str, Any], str, dict[str, Any]]:
        issue = (github or GitHubAPI.from_env()).get_issue(repository, issue_number)
        issue_text = (
            f"GitHub Issue {issue['repository']}#{issue['number']}: {issue['title']}\n\n"
            f"{issue['body']}\n\nSource: {issue['html_url']}"
        ).strip()
        context, plan, plan_hash = self.create_and_plan(repo_path, issue_text)
        self.workflow.store.event(context.task_id, "github_issue_imported", {
            "repository": issue["repository"], "number": issue["number"],
            "html_url": issue["html_url"], "state": issue["state"],
        })
        return context, plan, plan_hash, issue

    def plan_existing(self, task_id: str) -> tuple[dict[str, Any], str]:
        task = self.workflow.store.task(task_id)
        if task["status"] not in {"created", "plan_rejected", "planning_failed"}:
            raise RuntimeError(f"任务状态 {task['status']} 不能重新生成计划")
        planner_state: dict[str, Any] = {}
        result, _ = run_agent(
            self.client, PLANNER_PROMPT, PLANNER_TOOLS,
            self._task_with_memory(task_id, task["issue"]), model=self.model,
            execute_tool=make_executor(
                task["workspace_path"], code_search=build_code_search(task["workspace_path"]),
            ), max_steps=12,
            final_step_prompt=(
                "调查时间已结束。现在必须调用 plan_finish 提交五字段计划：goal、"
                "affected_files、steps、risks、allowed_commands。证据不足时在 risks 中"
                "说明不确定性，不要继续检索或编造。"
            ),
            final_step_tool_names={"plan_finish"}, terminal_tool_reserve=2,
            run_state=planner_state,
        )
        exit_reason = str(planner_state.get("exit_reason", "unknown"))
        steps_used = planner_state.get("budget_state", {}).get("steps_used", 0)

        def planning_failed(reason: str) -> None:
            self.workflow.store.event(task_id, "planning_failed", {
                "reason": reason, "exit_reason": exit_reason, "steps_used": steps_used,
            })
            self.workflow.store.set_status(task_id, "planning_failed")

        if isinstance(result, dict) and result.get("retryable"):
            planning_failed("llm_error")
            raise PlanningUnavailable(result["result"])
        try:
            plan = _validate_plan(result)
        except ValueError as exc:
            planning_failed("invalid_plan" if exit_reason == "finish" else "agent_exit")
            if exit_reason != "finish":
                detail = (f"Planner 未提交 plan_finish（退出原因：{exit_reason}，"
                          f"已用 {steps_used} 步）")
            else:
                detail = f"Planner 提交的计划无效：{exc}"
            raise PlanningUnavailable(
                f"{detail}。请重试现有任务，不要重新创建任务。"
            ) from exc
        plan_hash = self.workflow.submit_plan(task_id, plan)
        return plan, plan_hash

    def execute_after_plan_approval(self, task_id: str, target_file: str, max_rounds: int = 3) -> dict[str, str]:
        task = self.workflow.store.task(task_id)
        workspace = Path(task["workspace_path"])
        target = (workspace / target_file).resolve()
        if workspace not in target.parents or not target.is_file():
            raise ValueError("target_file 必须是任务工作区内的现有文件")
        command_runner = self.sandbox.command_runner(workspace) if self.sandbox else None
        code_search = build_code_search(workspace)
        executor = self.workflow.executor_for(
            task_id, workspace, command_runner=command_runner, code_search=code_search,
        )
        passed, issues, rounds, usage = multi_agent_review(
            self.client, str(target), task=self._task_with_memory(task_id, task["issue"]),
            max_rounds=max_rounds,
            model=self.model, verbose=False, executor=executor, workspace=workspace,
            coder_tools=SEARCH_CODER_TOOLS, reviewer_tools=SEARCH_REVIEWER_TOOLS,
            on_event=self._execution_event_callback(task_id),
            has_candidate_patch=lambda: _has_candidate_patch(workspace, task["base_commit"]),
        )
        # Prefer the conventional target-specific test.  It prevents unrelated
        # benchmark fixtures with duplicate names from poisoning independent
        # validation.  If absent, the approved broad pytest command is used and
        # a non-zero result is a hard validation failure.
        # Benchmark seeds keep test_buggy.py next to buggy.py; conventional
        # repositories usually keep it under tests/.  Support both without
        # falling back to an unsafe, unrelated whole-repository collection.
        sibling_test = target.parent / f"test_{target.stem}.py"
        conventional_test = workspace / "tests" / f"test_{target.stem}.py"
        target_test = sibling_test if sibling_test.is_file() else conventional_test
        python_executable = "python" if self.sandbox else sys.executable
        test_argv = [python_executable, "-m", "pytest", str(target_test.relative_to(workspace)), "-q"] \
            if target_test.is_file() else [python_executable, "-m", "pytest", "-q"]
        if self.sandbox:
            test_result = self.sandbox.run(workspace, test_argv, workdir=workspace, timeout=60)
            test_code, test_report = test_result.exit_code, test_result.output
        else:
            test = subprocess.run(
                test_argv, cwd=workspace, timeout=60,
                capture_output=True, text=True, encoding="utf-8", errors="replace",
            )
            test_code = test.returncode
            test_report = (test.stdout + test.stderr) or "(无测试输出)"
        review = {"passed": passed, "issues": issues, "rounds": rounds,
                  "tokens": usage.total_tokens, "tests_exit_code": test_code,
                  "test_argv": test_argv}
        return self.workflow.record_execution(task_id, test_report, review)

    def _execution_event_callback(self, task_id: str):
        """Persist bounded, public progress; keep all other audit events intact."""
        events = self.workflow.store.events(task_id)
        emitted = sum(event["event_type"] == "agent_step" for event in events)
        truncated = any(event["event_type"] == "agent_trace_truncated" for event in events)

        def persist(record: dict[str, Any]) -> None:
            nonlocal emitted, truncated
            if emitted >= MAX_PROGRESS_EVENTS_PER_TASK:
                if not truncated:
                    self.workflow.store.event(task_id, "agent_trace_truncated", {
                        "limit": MAX_PROGRESS_EVENTS_PER_TASK,
                        "reason": "progress_event_limit",
                        "further_progress_events_omitted": True,
                    })
                    truncated = True
                return
            self.workflow.store.event(task_id, "agent_step", _public_agent_event(record))
            emitted += 1

        return persist

    def _task_with_memory(self, task_id: str, task: str) -> str:
        memory = self.workflow.memory.render_context(task_id)
        return task if not memory else f"{task}\n\n{memory}"
