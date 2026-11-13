"""
多 Agent 代码审查团队（core 版）—— Coder + Reviewer + 编排循环

相比旧版 multi_agent_normal.py 的升级：
1. 复用 core 的通用 agent 循环 / 工具注册 / 用量统计（消除复制粘贴）
2. Reviewer 不再靠「不通过」字符串判断，而是结构化输出 {passed, issues}
3. 全程累计 token 与成本，可作为实验指标
4. 提供可导入的 multi_agent_review()，供 eval.py 批量测评

运行：
    python coder_reviewer.py   # 从 multi-agent 目录运行
"""
from __future__ import annotations

import os
from typing import Any, Callable

from core.client import make_client, resolve_model
from core.agent import run_agent
from core.usage import Usage
from core.tools import build_tool_registry, make_executor

CODERS = build_tool_registry("read_file", "write_file", "run_command", "finish")
REVIEWERS = build_tool_registry("read_file", "review_finish")
SEARCH_CODERS = build_tool_registry("search_code", "read_file", "write_file", "run_command", "finish")
SEARCH_REVIEWERS = build_tool_registry("search_code", "read_file", "review_finish")

CODER_PROMPT = """你是一个程序员，负责根据任务要求修改仓库中的代码。
先定位相关实现和测试，再尽早做一个可验证的最小修改；不要把步数全部花在重复检索上。
局部修改优先使用可用的 replace_text；只有确需覆盖完整文件时才用 write_file。
不要假定测试文件与目标文件同目录，按仓库实际布局寻找。运行测试时传 argv
（例如 ["python", "-m", "pytest", "-q"]），不能传 shell 字符串；被策略拒绝的
命令不要反复尝试。不要为了让测试变绿而删改无关测试，也不要安装依赖。
如果未产生实际补丁，不要声称任务已修复。修改并验证后调用 finish，说明变更。"""

REVIEWER_PROMPT = """你是一个认真负责的代码审查员。
对照原始任务检查候选修改是否真的解决问题，再检查逻辑、边界情况和回归风险。
只看见原有测试通过或现有代码没有明显错误，不代表任务已经完成。
只在确实有问题时才提出，不要为了挑刺而挑刺。
最后调用 review_finish 工具：
- 代码通过：passed=true, issues=[]
- 代码有问题：passed=false, issues=[具体问题及修改建议...]"""


def multi_agent_review(client, target_file, task="修复代码中的 bug",
                       max_rounds=3, model=None,
                       usage=None, trace=None, verbose=True, executor=None,
                       workspace=None, coder_tools=None, reviewer_tools=None,
                       on_event: Callable[[dict[str, Any]], None] | None = None,
                       has_candidate_patch: Callable[[], bool] | None = None,
                       review_evidence: Callable[[], str] | None = None,
                       max_tokens=None, max_cost_rmb=None, on_usage=None):
    """Coder 修 → Reviewer 查 → 不通过喂回意见，最多 max_rounds 轮。

    返回 (passed, review_issues, rounds, usage)。
    """
    model = resolve_model(model)
    if usage is None:
        usage = Usage()
    # File tools enforce path scope. A command's initial directory is not isolation.
    workdir = os.path.abspath(workspace) if workspace else os.path.dirname(os.path.abspath(target_file))
    target_display = os.path.relpath(os.path.abspath(target_file), workdir)
    coder_task = f"目标文件：{target_display}\n\n原始任务：{task}"
    coder_tools = coder_tools or CODERS
    reviewer_tools = reviewer_tools or REVIEWERS
    bound_executor = executor or make_executor(workdir)

    def phase_callback(phase: str, round_num: int):
        if on_event is None:
            return None

        def emit(record: dict[str, Any]) -> None:
            on_event({**record, "phase": phase, "round": round_num})

        return emit

    for round_num in range(1, max_rounds + 1):
        if verbose:
            print(f"\n{'='*20} 第 {round_num} 轮 {'='*20}")

        # Coder 修复
        if verbose:
            print("[Coder] 修复中...")
        coder_result, usage = run_agent(
            client, CODER_PROMPT, coder_tools, coder_task,
            model=model, usage=usage, trace=trace,
            execute_tool=bound_executor, max_tokens=max_tokens, max_cost_rmb=max_cost_rmb,
            on_usage=on_usage,
            on_event=phase_callback("coder", round_num),
        )
        if verbose:
            print(f"[Coder] 完成：{str(coder_result)[:80]}")

        # In the Issue-to-PR workflow a Reviewer must never certify unchanged
        # baseline code. The caller supplies the exact publishable-diff check;
        # generic/non-Git users retain their previous behavior.
        if has_candidate_patch is not None and not has_candidate_patch():
            issue = "Coder 未生成可审查的代码补丁，跳过 Reviewer；原有测试通过不代表任务完成"
            if on_event is not None:
                on_event({"action": "candidate_patch_missing", "status": "error",
                          "phase": "coder", "round": round_num})
            return False, [issue], round_num, usage

        # Reviewer 审查（只读 + 结构化结论）
        if verbose:
            print("[Reviewer] 审查中...")
        evidence = review_evidence() if review_evidence is not None else ""
        review, usage = run_agent(
            client, REVIEWER_PROMPT, reviewer_tools,
            f"原始任务：{task}\n\n请审查以 {target_display} 为起点的候选修改。\n\n{evidence}",
            model=model, usage=usage, trace=trace,
            execute_tool=bound_executor, max_tokens=max_tokens, max_cost_rmb=max_cost_rmb,
            on_usage=on_usage,
            on_event=phase_callback("reviewer", round_num),
        )
        if (isinstance(review, dict) and type(review.get("passed")) is bool
                and isinstance(review.get("issues"), list)
                and all(isinstance(issue, str) for issue in review["issues"])):
            passed = review["passed"] and not review["issues"]
            issues = review["issues"]
        else:
            # 兜底：审查员没走 review_finish，保守判不通过
            passed, issues = False, [str(review)[:200]]
        if verbose:
            print(f"[Reviewer] passed={passed}, issues={len(issues)}")

        if passed:
            if verbose:
                print(f"\n✅ 第 {round_num} 轮审查通过，任务完成！")
            return True, issues, round_num, usage

        if verbose:
            print("[编排] 未通过，把意见喂回 Coder 继续改...")
        issue_text = "\n".join(f"- {i}" for i in issues)
        coder_task = (
            f"目标文件：{target_display}\n\n原始任务：{task}\n\n"
            f"你上次的修改被审查员指出了以下问题：\n{issue_text}\n\n"
            f"请根据这些问题重新修改代码。"
        )

    return False, issues, max_rounds, usage


if __name__ == "__main__":
    os.chdir(os.path.dirname(os.path.abspath(__file__)))  # 保证 .env 能被找到
    client = make_client()
    passed, issues, rounds, usage = multi_agent_review(
        client, "buggy.py", verbose=True,
    )
    print(f"\n最终：passed={passed}, 轮数={rounds}")
    print(usage)
