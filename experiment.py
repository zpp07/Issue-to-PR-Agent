"""
对照实验：独立 Reviewer vs 自我审查（Self-Review）

回答的问题（也是 mini-paper 的研究问题）：
    把「修代码」和「查代码」拆成两个独立 agent，相比让同一个 agent
    自我审查，到底值不值？具体来说：
    - 修复率（tests_pass）有没有提升？
    - 多花多少 token / 成本？
    - 审查判定（agent_passed）和真实结果（tests_pass）是否一致（有没有误判）？

两个被测对象：
    A) multi-review ：Coder 修 → 独立 Reviewer 审查 → 反馈回 Coder（多轮）
    B) self-review  ：同一个 agent 修完自己检查一遍（单 agent，prompt 要求自查）
两者都用同样的 seed 用例 + 同样的独立 pytest 验证，公平对比。

运行：
    python experiment.py            # 跑 2 个用例 × 2 种方式的矩阵（会调 API）
    python experiment.py --cases case01_add --methods self,mreview
"""
import os
import sys
import json
import shutil
import subprocess
import tempfile
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.client import make_client
from core.agent import run_agent
from core.usage import Usage
from core.tools import build_tool_registry, make_executor

HERE = os.path.dirname(os.path.abspath(__file__))
SEEDS = os.path.join(HERE, "eval", "seeds")
RESULTS = os.path.join(HERE, "eval", "experiment_results.jsonl")

CODERS = build_tool_registry("read_file", "write_file", "run_command", "finish")
REVIEWERS = build_tool_registry("read_file", "review_finish")

CODER_PROMPT = """你是一个程序员，负责修复代码中的 bug。
工作流程：先用 read_file 读代码定位问题，用 write_file 修改，用 run_command 跑测试验证。
测试文件 test_buggy.py 就在目标文件同一目录，直接运行 `python -m pytest -q` 即可。
以测试为准，让 pytest 全部通过并满足测试声明的异常行为。修好后调用 finish 工具。"""

REVIEWER_PROMPT = """你是一个认真负责的代码审查员。
用 read_file 读代码，检查 bug、逻辑、边界、健壮性，只在确实有问题时才提出。
最后调用 review_finish：通过=passed:true + issues:[]；有问题=passed:false + issues 列表。"""

# self-review 的提示词：要求同一个 agent 修完后自查
SELF_REVIEW_PROMPT = """你是一个程序员，负责修复代码中的 bug。
工作流程：先用 read_file 读代码定位问题，用 write_file 修改，用 run_command 跑测试验证。
测试文件 test_buggy.py 就在目标文件同一目录，直接运行 `python -m pytest -q` 即可。

修完后，你必须再像审查员一样仔细自查一遍：重新 read_file 检查 bug、逻辑、边界、健壮性，
确认没有遗漏才算完成。自查通过后调用 finish 工具。"你"既当码农也当审查员。"""


# ---------- 独立 pytest 验证（不信任 agent 自说自话）----------
def verify(workdir):
    r = subprocess.run(
        [sys.executable, "-m", "pytest", "test_buggy.py", "-q"],
        cwd=workdir, capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )
    return r.returncode == 0, (r.stdout + r.stderr)[-300:]


def copy_seed(case, dst):
    src = os.path.join(SEEDS, case)
    for f in os.listdir(src):
        shutil.copy(os.path.join(src, f), os.path.join(dst, f))


# ---------- 方式 A：多 agent（Coder + Reviewer）----------
def run_multi_review(client, target_path, model, max_rounds=3):
    workdir = os.path.dirname(os.path.abspath(target_path))
    coder_exec = make_executor(workdir)
    review_exec = make_executor(workdir)
    usage = Usage()
    trace = []
    coder_task = "修复代码中的 bug，并用 pytest 验证"

    for rnd in range(1, max_rounds + 1):
        _, usage = run_agent(
            client, CODER_PROMPT, CODERS, coder_task,
            model=model, usage=usage, trace=trace, execute_tool=coder_exec,
        )
        review, usage = run_agent(
            client, REVIEWER_PROMPT, REVIEWERS,
            f"请审查文件 {os.path.basename(target_path)} 的代码",
            model=model, usage=usage, trace=trace, execute_tool=review_exec,
        )
        if isinstance(review, dict):
            passed, issues = bool(review.get("passed")), list(review.get("issues") or [])
        else:
            passed, issues = False, [str(review)[:200]]
        agent_passed = passed
        if passed:
            break
        issue_text = "\n".join(f"- {i}" for i in issues)
        coder_task = f"你上次的修改被审查员指出了以下问题：\n{issue_text}\n\n请根据这些问题重新修改代码。"

    tests_pass, _ = verify(workdir)
    return {
        "agent_passed": agent_passed,
        "review_rounds": rnd,
        "tests_pass": tests_pass,
        "usage": usage,
        "trace": trace,
    }


# ---------- 方式 B：单 agent 自我审查 ----------
def run_self_review(client, target_path, model):
    workdir = os.path.dirname(os.path.abspath(target_path))
    executor = make_executor(workdir)
    usage = Usage()
    trace = []
    result, usage = run_agent(
        client, SELF_REVIEW_PROMPT, CODERS, "修复代码中的 bug，并用 pytest 验证",
        model=model, usage=usage, trace=trace, execute_tool=executor,
    )
    tests_pass, _ = verify(workdir)
    agent_claimed_done = True  # 单 agent 走到 finish 即视为"自认为完成"
    return {
        "agent_passed": agent_claimed_done,
        "review_rounds": 1,
        "tests_pass": tests_pass,
        "usage": usage,
        "trace": trace,
    }


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", default="case01_add,case02_stats")
    parser.add_argument("--methods", default="mreview,self")
    args = parser.parse_args()

    client = make_client()
    cases = [c.strip() for c in args.cases.split(",")]
    methods = [m.strip() for m in args.methods.split(",")]

    print(f"{'='*60}")
    print(f"对照实验：独立 Reviewer vs 自我审查  ({datetime.now():%H:%M:%S})")
    print(f"cases={cases}  methods={methods}\n")

    for case in cases:
        for method in methods:
            with tempfile.TemporaryDirectory() as tmp:
                copy_seed(case, tmp)
                target = os.path.join(tmp, "buggy.py")
                label = f"{case} [{method}]"

                if method == "mreview":
                    r = run_multi_review(client, target, "deepseek-chat")
                elif method == "self":
                    r = run_self_review(client, target, "deepseek-chat")
                else:
                    print(f"未知方法 {method}，跳过")
                    continue

                u = r["usage"]
                row = {
                    "case": case,
                    "method": method,
                    "agent_passed": r["agent_passed"],
                    "tests_pass": r["tests_pass"],
                    "review_rounds": r["review_rounds"],
                    "total_tokens": u.total_tokens,
                    "cost_rmb": round(u.cost_rmb(), 5),
                    "ts": datetime.now().isoformat(timespec="seconds"),
                }
                print(f"▶ {label:28s} "
                      f"agent_passed={row['agent_passed']}  "
                      f"tests_pass={row['tests_pass']}  "
                      f"rounds={row['review_rounds']}  "
                      f"tokens={row['total_tokens']}  ¥{row['cost_rmb']:.4f}")
                with open(RESULTS, "a", encoding="utf-8") as f:
                    f.write(json.dumps(row, ensure_ascii=False) + "\n")

    print(f"\n结果已追加到 {RESULTS}")


if __name__ == "__main__":
    main()
