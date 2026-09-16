"""
eval —— 测评 multi-agent 修复流程

流程：把 case 复制到临时工作区 → 跑 coder+reviewer 修复 → 用 pytest 验证
    → 记录 passed / 轮数 / token / 成本。

运行：
    python eval/eval.py            # 从 multi-agent 目录运行
产出：
    控制台汇总表 + eval/results.jsonl（每条记录一行 JSON）
"""
import os
import sys
import json
import shutil
import subprocess
import tempfile

# Support the legacy direct entry point: python eval/eval.py.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.client import make_client
from core.usage import Usage
from coder_reviewer import multi_agent_review

HERE = os.path.dirname(os.path.abspath(__file__))
CASES_DIR = os.path.join(HERE, "cases")
SEEDS_DIR = os.path.join(HERE, "seeds")
RESULTS_FILE = os.path.join(HERE, "results.jsonl")


def restore_cases():
    """每次 eval 前，用 seeds/ 里的纯净版本恢复 cases/。

    agent 有写文件权限，可能把修复结果写回源 case（沙箱逃逸/测试污染）。
    必须保证 eval 的题目永远是「真正带 bug」的纯净版本。
    """
    if not os.path.isdir(SEEDS_DIR):
        return
    if os.path.isdir(CASES_DIR):
        shutil.rmtree(CASES_DIR, ignore_errors=True)
    shutil.copytree(SEEDS_DIR, CASES_DIR)


def run_tests(case_dir):
    """在工作区跑 pytest，返回 (exit_code, output)。

    用 sys.executable 保证与当前解释器一致（裸 'python' 在子进程里
    可能解析到没装 pytest 的系统 Python）。
    """
    r = subprocess.run(
        [sys.executable, "-m", "pytest", "test_buggy.py", "-q"],
        cwd=case_dir, capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )
    return r.returncode, (r.stdout + r.stderr)


def eval_one_case(client, case_name, model="deepseek-chat", max_rounds=3):
    src = os.path.join(CASES_DIR, case_name)
    skip = {"__pycache__", ".pytest_cache"}
    test_path = os.path.join(src, "test_buggy.py")
    buggy_path = os.path.join(src, "buggy.py")
    with open(test_path, encoding="utf-8") as fh:
        original_test = fh.read()
    with open(buggy_path, encoding="utf-8") as fh:
        original_buggy = fh.read()

    with tempfile.TemporaryDirectory() as tmp:
        # 复制 case 到临时工作区
        for f in os.listdir(src):
            if f in skip:
                continue
            shutil.copy(os.path.join(src, f), os.path.join(tmp, f))

        target = "buggy.py"  # 约定每个 case 的目标文件名固定为 buggy.py
        usage = Usage()
        trace = []
        passed, issues, rounds, usage = multi_agent_review(
            client, os.path.join(tmp, target),
            task="修复代码中的 bug，并用 pytest 验证",
            max_rounds=max_rounds, model=model,
            usage=usage, trace=trace, verbose=False,
        )

        # 检查 agent 是否篡改了测试文件（防止"改测试过关"的自欺行为）
        with open(os.path.join(tmp, "test_buggy.py"), encoding="utf-8") as fh:
            current_test = fh.read()
        test_tampered = current_test != original_test

        # 检查 agent 是否逃出工作区、污染了源 case 文件（沙箱逃逸信号）
        with open(buggy_path, encoding="utf-8") as fh:
            current_buggy = fh.read()
        source_polluted = current_buggy != original_buggy

        # 强制用原始测试验证（不信任 agent 留下的任何测试）
        with open(os.path.join(tmp, "test_buggy.py"), "w", encoding="utf-8") as fh:
            fh.write(original_test)
        code, output = run_tests(tmp)
        tests_pass = code == 0

        record = {
            "case": case_name,
            "agent_passed": passed,
            "tests_pass": tests_pass,
            "review_rounds": rounds,
            "test_tampered": test_tampered,
            "source_polluted": source_polluted,
            "prompt_tokens": usage.prompt_tokens,
            "completion_tokens": usage.completion_tokens,
            "total_tokens": usage.total_tokens,
            "cost_rmb": round(usage.cost_rmb(), 6),
            "calls": usage.calls,
            "test_output": output[-500:],
        }
        return record


def main():
    client = make_client()
    restore_cases()  # 先用纯净 seed 恢复题目
    cases = sorted(os.listdir(CASES_DIR))
    with open(RESULTS_FILE, "a", encoding="utf-8") as f:
        for name in cases:
            print(f"\n▶ 测评用例：{name}", flush=True)
            rec = eval_one_case(client, name)
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            f.flush()
            print(
                f"  agent_passed={rec['agent_passed']}  "
                f"tests_pass={rec['tests_pass']}  "
                f"rounds={rec['review_rounds']}  "
                f"tampered={rec['test_tampered']}  "
                f"polluted={rec['source_polluted']}  "
                f"tokens={rec['total_tokens']}  "
                f"¥{rec['cost_rmb']}"
            )
    print(f"\n结果已追加到 {RESULTS_FILE}")


if __name__ == "__main__":
    main()
