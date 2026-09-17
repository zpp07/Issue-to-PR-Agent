"""Application adapter that joins planner, Coder/Reviewer, and Phase-2 workflow."""
from __future__ import annotations

from pathlib import Path
import subprocess
import sys
from typing import Any

from core.agent import run_agent
from core.policy import CommandPolicy
from core.search import build_code_search
from core.tools import build_tool_registry, make_executor
from core.workflow import IssueToPRWorkflow, TaskContext
from coder_reviewer import multi_agent_review

PLANNER_TOOLS = build_tool_registry("search_code", "read_file", "plan_finish")
SEARCH_CODER_TOOLS = build_tool_registry("search_code", "read_file", "write_file", "run_command", "finish")
SEARCH_REVIEWER_TOOLS = build_tool_registry("search_code", "read_file", "review_finish")
PLANNER_PROMPT = """你是 Python 项目的变更规划员。先阅读与 issue 相关的文件，
优先使用 search_code 找到符号、调用方、测试和文档契约，再用 read_file 精读证据。
只做计划，绝不修改代码或运行命令。最后必须调用 plan_finish，给出：目标、可能受影响
文件、可验证的步骤、风险和拟执行的 argv 命令。允许命令只限于：
["python", "-m", "pytest", "-q"]、["pytest", "-q"]、["ruff", "check", "."]、
["git", "status"]、["git", "diff"]。绝不能运行 python 文件、shell、网络或写 Git 命令。
不确定的文件或风险应明确写出，不要编造。"""


class PlanningUnavailable(RuntimeError):
    """The task/worktree exists, but the LLM plan can safely be retried later."""


def _validate_plan(plan: Any) -> dict[str, Any]:
    if not isinstance(plan, dict):
        raise ValueError("Planner 未返回结构化计划")
    required = {"goal", "affected_files", "steps", "risks", "allowed_commands"}
    if set(plan) != required:
        raise ValueError("Planner 输出字段不完整")
    policy = CommandPolicy()
    for argv in plan["allowed_commands"]:
        if policy.evaluate(argv).level.value == "deny":
            raise ValueError(f"Planner 请求了被拒绝的命令：{argv!r}")
    return plan


class IssueToPRService:
    def __init__(self, workflow: IssueToPRWorkflow, client, model: str = "deepseek-chat", sandbox=None):
        self.workflow = workflow
        self.client = client
        self.model = model
        self.sandbox = sandbox

    def create_and_plan(self, repo_path: str | Path, issue: str) -> tuple[TaskContext, dict[str, Any], str]:
        context = self.workflow.create_task(repo_path, issue)
        plan, plan_hash = self.plan_existing(context.task_id)
        return context, plan, plan_hash

    def plan_existing(self, task_id: str) -> tuple[dict[str, Any], str]:
        task = self.workflow.store.task(task_id)
        if task["status"] not in {"created", "plan_rejected"}:
            raise RuntimeError(f"任务状态 {task['status']} 不能重新生成计划")
        result, _ = run_agent(
            self.client, PLANNER_PROMPT, PLANNER_TOOLS,
            self._task_with_memory(task_id, task["issue"]), model=self.model,
            execute_tool=make_executor(
                task["workspace_path"], code_search=build_code_search(task["workspace_path"]),
            ), max_steps=8,
        )
        if isinstance(result, dict) and result.get("retryable"):
            self.workflow.store.event(task_id, "planning_failed", {"error": result.get("result")})
            raise PlanningUnavailable(result["result"])
        plan = _validate_plan(result)
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

    def _task_with_memory(self, task_id: str, task: str) -> str:
        memory = self.workflow.memory.render_context(task_id)
        return task if not memory else f"{task}\n\n{memory}"
