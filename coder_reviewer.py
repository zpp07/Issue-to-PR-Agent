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
import os

from core.client import make_client
from core.agent import run_agent
from core.usage import Usage
from core.tools import build_tool_registry, make_executor

CODERS = build_tool_registry("read_file", "write_file", "run_command", "finish")
REVIEWERS = build_tool_registry("read_file", "review_finish")

CODER_PROMPT = """你是一个程序员，负责修复代码中的 bug。
工作流程：先用 read_file 读代码定位问题，用 write_file 修改，用 run_command 跑测试验证。

重要约定：
- 测试文件 test_buggy.py 就在目标文件同一目录，调用 run_command 时传入
  argv，例如 ["python", "-m", "pytest", "-q"]；不能传 shell 字符串。
  不要浪费时间去找 tests/ 子目录或其他路径。
- 以测试用例为准：让 pytest 全部通过，并满足测试里声明的异常行为（如空输入抛 ValueError）。
- 跳过依赖下载/环境配置，专注改代码和跑测试。

修好并验证通过后，立即调用 finish 工具，简要说明你改了哪里。"""

REVIEWER_PROMPT = """你是一个认真负责的代码审查员。
用 read_file 读代码，仔细检查：bug 和逻辑错误、边界情况、健壮性、性能。
只在确实有问题时才提出，不要为了挑刺而挑刺。
最后调用 review_finish 工具：
- 代码通过：passed=true, issues=[]
- 代码有问题：passed=false, issues=[具体问题及修改建议...]"""


def multi_agent_review(client, target_file, task="修复代码中的 bug",
                       max_rounds=3, model="deepseek-chat",
                       usage=None, trace=None, verbose=True, executor=None):
    """Coder 修 → Reviewer 查 → 不通过喂回意见，最多 max_rounds 轮。

    返回 (passed, review_issues, rounds, usage)。
    """
    if usage is None:
        usage = Usage()
    coder_task = task
    # 推导工作目录：目标文件所在目录。agent 的所有文件/命令操作都被限制在这里，
    # 避免它用 `cd` 逃出沙箱、污染工作区之外的文件。
    workdir = os.path.dirname(os.path.abspath(target_file))

    for round_num in range(1, max_rounds + 1):
        if verbose:
            print(f"\n{'='*20} 第 {round_num} 轮 {'='*20}")

        # Coder 修复
        if verbose:
            print("[Coder] 修复中...")
        coder_executor = executor or make_executor(workdir)
        coder_task = coder_task.replace(target_file, os.path.basename(target_file))
        coder_result, usage = run_agent(
            client, CODER_PROMPT, CODERS, coder_task,
            model=model, usage=usage, trace=trace,
            execute_tool=coder_executor,
        )
        if verbose:
            print(f"[Coder] 完成：{str(coder_result)[:80]}")

        # Reviewer 审查（只读 + 结构化结论）
        if verbose:
            print("[Reviewer] 审查中...")
        review_executor = executor or make_executor(workdir)
        review, usage = run_agent(
            client, REVIEWER_PROMPT, REVIEWERS,
            f"请审查文件 {os.path.basename(target_file)} 的代码",
            model=model, usage=usage, trace=trace,
            execute_tool=review_executor,
        )
        if isinstance(review, dict):
            passed = bool(review.get("passed"))
            issues = list(review.get("issues") or [])
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
