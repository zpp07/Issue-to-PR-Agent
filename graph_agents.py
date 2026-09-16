"""
LangGraph 版多 Agent 编排 —— Coder + Reviewer

和 coder_reviewer.py（手写 while 循环）的区别：
1. 用 StateGraph 显式建模「节点 + 条件边」，图结构一目了然
2. 状态（State）显式在节点间流转，用 TypedDict 定义 schema
3. 支持 human-in-loop：每轮后 interrupt，由人决定通过还是继续
4. checkpoint：可持久化、断点续跑

运行：
    python graph_agents.py               # 自动跑，无人工干预
    python graph_agents.py --human       # 每轮审查后暂停，等你敲 y/n

注意：LangGraph 1.x 的 StateGraph 需要 TypedDict 定义状态 schema，
裸 dict 子类不会被识别（字段无法在节点间合并）。
"""
import os
import sys
from typing import TypedDict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from langgraph.graph import StateGraph, END
from langgraph.checkpoint.sqlite import SqliteSaver

from core.client import make_client
from core.agent import run_agent
from core.usage import Usage
from core.tools import build_tool_registry, make_executor

CODERS = build_tool_registry("read_file", "write_file", "run_command", "finish")
REVIEWERS = build_tool_registry("read_file", "review_finish")

CODER_PROMPT = """你是一个程序员，负责修复代码中的 bug。
工作流程：先用 read_file 读代码定位问题，用 write_file 修改，用 run_command 跑测试验证。

重要约定：
- 测试文件 test_buggy.py 就在目标文件同一目录，直接运行 `python -m pytest -q` 即可。
- 以测试用例为准：让 pytest 全部通过，并满足测试里声明的异常行为。
修好并验证通过后，立即调用 finish 工具。"""

REVIEWER_PROMPT = """你是一个认真负责的代码审查员。
用 read_file 读代码，检查 bug、逻辑、边界、健壮性。
只在确实有问题时才提出，不要为了挑刺而挑刺。
最后调用 review_finish：通过=passed:true + issues:[]；有问题=passed:false + issues 列表。"""


# ---------- 图状态（TypedDict 定义 schema，LangGraph 1.x 要求）----------
class State(TypedDict, total=False):
    coder_task: str        # 当前交给 Coder 的任务（首轮是原始任务，之后是审查意见）
    coder_result: str      # Coder 本轮修复结果的文本摘要
    passed: bool           # Reviewer 最新一轮是否判定通过
    issues: list           # Reviewer 最新一轮发现的问题列表
    rounds: int            # 已完成的审查轮数


def _shared_runtime(client, target_path, model):
    """构造节点工厂共享的运行期对象（client / usage / trace）。

    client 是基础设施而非流转数据，故不进 state（不可序列化）；
    usage / trace 需要跨轮累计，用闭包共享同一个实例。
    """
    workdir = os.path.dirname(os.path.abspath(target_path))
    executor = make_executor(workdir)
    usage = Usage()
    trace = []

    def coder(state):
        task = state.get("coder_task") or "修复代码中的 bug，并用 pytest 验证"
        result, _ = run_agent(
            client, CODER_PROMPT, CODERS, task,
            model=model, usage=usage, trace=trace, execute_tool=executor,
        )
        return {"coder_result": str(result)[:200]}

    def reviewer(state):
        review, _ = run_agent(
            client, REVIEWER_PROMPT, REVIEWERS,
            f"请审查文件 {os.path.basename(target_path)} 的代码",
            model=model, usage=usage, trace=trace, execute_tool=executor,
        )
        if isinstance(review, dict):
            passed = bool(review.get("passed"))
            issues = list(review.get("issues") or [])
        else:
            passed, issues = False, [str(review)[:200]]
        rounds = state.get("rounds", 0) + 1
        return {"passed": passed, "issues": issues, "rounds": rounds}

    return coder, reviewer, usage, trace


def make_feedback_router(max_rounds):
    """条件边：通过 → END；未达上限 → 回 coder；达上限 → END。"""
    def router(state):
        if state.get("passed"):
            return END
        if state.get("rounds", 0) >= max_rounds:
            return END
        return "coder"
    return router


def build_graph(client, target_path, task, model="deepseek-chat", max_rounds=3):
    """构建 Coder ↔ Reviewer 的状态图。返回 (graph, runtime)。

    runtime = (usage, trace)，供调用方在 invoke 后读取累计用量与轨迹。
    """
    coder, reviewer, usage, trace = _shared_runtime(client, target_path, model)

    g = StateGraph(State)
    g.add_node("coder", coder)
    g.add_node("reviewer", reviewer)
    g.set_entry_point("coder")
    g.add_edge("coder", "reviewer")
    g.add_conditional_edges(
        "reviewer",
        make_feedback_router(max_rounds),
        {"coder": "coder", END: END},
    )
    return g, (usage, trace)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--human", action="store_true", help="每轮审查后人工确认")
    args = parser.parse_args()

    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    client = make_client()
    target = os.path.join(os.getcwd(), "buggy.py")

    graph, (usage, trace) = build_graph(
        client, target, "修复代码中的 bug，并用 pytest 验证"
    )
    initial = {"coder_task": "修复代码中的 bug，并用 pytest 验证", "rounds": 0}
    cfg = {"configurable": {"thread_id": "demo"}}

    if args.human:
        # human-in-loop：interrupt_after=["reviewer"] —— 每轮审查后暂停
        with SqliteSaver.from_conn_string(":memory:") as saver:
            app = graph.compile(checkpointer=saver, interrupt_after=["reviewer"])
            snapshot = app.invoke(initial, cfg)
            while True:
                s = app.get_state(cfg).values
                print(f"\n[人工确认] 第 {s.get('rounds')} 轮 passed={s.get('passed')}，"
                      f"问题 {len(s.get('issues') or [])} 条")
                ans = input("继续修复？(y/n): ").strip().lower()
                if s.get("passed") or ans != "y":
                    break
                app.invoke(None, cfg)  # 从断点恢复，跑下一轮 coder
            result = app.get_state(cfg).values
    else:
        app = graph.compile()
        result = app.invoke(initial)

    print(f"\n最终：passed={result.get('passed')}, 轮数={result.get('rounds')}")
    print(usage)
