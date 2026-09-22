"""Analyze the frozen E2 completion/review experiment without rerunning models."""
from __future__ import annotations

import argparse
from collections import defaultdict
import json
import math
from pathlib import Path
from statistics import median


DEFAULT_RESULTS = Path(
    "benchmark/real/experiments/e2_v6_completion_review_2026-09-22.jsonl"
)
ARM_PROTOCOLS = {
    "A": "single-v6-model-finish-np-off",
    "B": "single-v6-verified-auto-np-off",
    "C": "review_revise-v6-verified-auto-np-off",
}
PROTOCOL_ARMS = {protocol: arm for arm, protocol in ARM_PROTOCOLS.items()}
ARM_DESCRIPTIONS = {
    "A": "single / model finish",
    "B": "single / verified auto-finish",
    "C": "Reviewer + at most one Reviser",
}


def load_rows(path: str | Path = DEFAULT_RESULTS) -> list[dict]:
    rows = []
    for line_number, raw in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        if not raw.strip():
            continue
        try:
            row = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid JSON on line {line_number}: {exc}") from exc
        protocol = row.get("protocol_version")
        if protocol not in PROTOCOL_ARMS:
            raise ValueError(f"line {line_number} is not a formal E2 v6 row: {protocol!r}")
        row = dict(row)
        row["arm"] = PROTOCOL_ARMS[protocol]
        rows.append(row)
    return rows


def validate_rows(rows: list[dict], *, strict: bool = False) -> dict:
    seen = set()
    protocol_hashes: dict[str, set[str]] = defaultdict(set)
    revisions = set()
    evaluation_hashes = set()
    problems = []

    for row in rows:
        key = (row["protocol_version"], row.get("case"), row.get("trial"))
        if key in seen:
            problems.append(f"duplicate run key: {key}")
        seen.add(key)
        protocol_hashes[row["protocol_version"]].add(str(row.get("protocol_hash")))
        revisions.add(str(row.get("code_revision")))
        evaluation_hashes.add(str(row.get("evaluation_protocol_hash")))
        if row.get("tracked_worktree_dirty") is not False:
            problems.append(f"tracked worktree was not clean for {key}")
        evaluation = row.get("evaluation_protocol") or {}
        if evaluation.get("hidden_tests_visible_to_agent") is not False:
            problems.append(f"hidden-test visibility is not false for {key}")

    for protocol, hashes in protocol_hashes.items():
        if len(hashes) != 1 or "None" in hashes:
            problems.append(f"protocol hash drift for {protocol}: {sorted(hashes)}")
    if len(revisions) != 1 or "None" in revisions:
        problems.append(f"code revision drift: {sorted(revisions)}")
    if len(evaluation_hashes) != 1 or "None" in evaluation_hashes:
        problems.append(f"evaluation protocol drift: {sorted(evaluation_hashes)}")

    if strict:
        expected_cases = {
            "iterative__dvc-4124",
            "iterative__dvc-4185",
            "pydantic__pydantic-8977",
            "facebookresearch__hydra-1791",
        }
        for arm in ARM_PROTOCOLS:
            arm_rows = [row for row in rows if row["arm"] == arm]
            actual = {(row.get("case"), row.get("trial")) for row in arm_rows}
            expected = {(case, trial) for case in expected_cases for trial in range(1, 4)}
            if actual != expected:
                problems.append(
                    f"arm {arm} run matrix mismatch: missing={sorted(expected - actual)}, "
                    f"extra={sorted(actual - expected)}"
                )

    if problems:
        raise ValueError("; ".join(problems))
    return {
        "runs": len(rows),
        "code_revision": next(iter(revisions), None),
        "evaluation_protocol_hash": next(iter(evaluation_hashes), None),
        "protocol_hashes": {
            protocol: next(iter(hashes)) for protocol, hashes in protocol_hashes.items()
        },
    }


def wilson_interval(successes: int, runs: int, z: float = 1.959963984540054) -> tuple[float, float]:
    if runs <= 0:
        return 0.0, 0.0
    proportion = successes / runs
    denominator = 1 + z * z / runs
    centre = (proportion + z * z / (2 * runs)) / denominator
    margin = z * math.sqrt(
        proportion * (1 - proportion) / runs + z * z / (4 * runs * runs)
    ) / denominator
    return max(0.0, centre - margin), min(1.0, centre + margin)


def summarize_arm(rows: list[dict]) -> dict:
    runs = len(rows)
    passes = sum(bool(row.get("tests_pass")) for row in rows)
    tokens = [int(row.get("total_tokens", 0)) for row in rows]
    costs = [float(row.get("cost_rmb", 0.0)) for row in rows]
    controller_rows = [row for row in rows if row.get("exit_reason") == "verified_patch"]
    revision_rows = [row for row in rows if row.get("revision_attempted") is True]
    reviews = [row.get("review") for row in rows if isinstance(row.get("review"), dict)]
    review_pass_rows = [
        row for row in rows
        if isinstance(row.get("review"), dict) and row["review"].get("passed") is True
    ]
    low, high = wilson_interval(passes, runs)
    return {
        "runs": runs,
        "passes": passes,
        "pass_rate": passes / runs if runs else 0.0,
        "ci_low": low,
        "ci_high": high,
        "total_tokens": sum(tokens),
        "mean_tokens": sum(tokens) / runs if runs else 0.0,
        "median_tokens": median(tokens) if tokens else 0.0,
        "total_cost_rmb": sum(costs),
        "elapsed_seconds": sum(float(row.get("elapsed_seconds", 0.0)) for row in rows),
        "regressions": sum(row.get("regression_detected") is True for row in rows),
        "agent_completed": sum(row.get("agent_completed") is True for row in rows),
        "controller_triggers": len(controller_rows),
        "controller_passes": sum(bool(row.get("tests_pass")) for row in controller_rows),
        "review_passed": sum(review.get("passed") is True for review in reviews),
        "review_pass_hidden_passes": sum(
            bool(row.get("tests_pass")) for row in review_pass_rows
        ),
        "review_negative": sum(
            review.get("passed") is False and review.get("skipped") is not True
            for review in reviews
        ),
        "review_skipped": sum(review.get("skipped") is True for review in reviews),
        "revision_attempts": len(revision_rows),
        "revision_passes": sum(bool(row.get("tests_pass")) for row in revision_rows),
        "budget_overshoots": sum(int(row.get("total_tokens", 0)) > 700_000 for row in rows),
    }


def summarize(rows: list[dict]) -> dict[str, dict]:
    return {
        arm: summarize_arm([row for row in rows if row["arm"] == arm])
        for arm in ARM_PROTOCOLS
    }


def _percent(value: float) -> str:
    return f"{value * 100:.1f}%"


def _delta_percent(current: float, baseline: float) -> str:
    if not baseline:
        return "n/a"
    return f"{(current / baseline - 1) * 100:+.1f}%"


def markdown_report(rows: list[dict], metadata: dict) -> str:
    summaries = summarize(rows)
    lines = [
        "# E2：确定性终止与单轮 Reviewer/Reviser 实验",
        "",
        "> 结论先行：在 4 个真实仓库任务、每组 12 次 provider-default 重复运行中，",
        "> B 组以更少 token 得到最高的观察通过率，但样本很小且确定性终止触发的",
        "> 3 条运行只有 1 条通过隐藏测试；C 组没有显示出优于 B 的收益。",
        "",
        "## 冻结协议",
        "",
        f"- 正式运行：{metadata['runs']} 条，仅包含 protocol v6",
        f"- 代码版本：`{metadata['code_revision']}`",
        f"- evaluator 协议：`{metadata['evaluation_protocol_hash']}`",
        "- 每个任务每组 3 次独立 provider-default 重复；API 未提供显式随机种子",
        "- Agent 始终看不到 hidden tests 和 gold patch；所有运行的 tracked worktree 均干净",
        "",
        "| 组 | 机制 | Hidden pass | Wilson 95% CI | Tokens | 平均 tokens | 成本（RMB） |",
        "|---|---|---:|---:|---:|---:|---:|",
    ]
    for arm in ARM_PROTOCOLS:
        item = summaries[arm]
        lines.append(
            f"| {arm} | {ARM_DESCRIPTIONS[arm]} | {item['passes']}/{item['runs']} "
            f"({_percent(item['pass_rate'])}) | {_percent(item['ci_low'])}–{_percent(item['ci_high'])} | "
            f"{item['total_tokens']:,} | {item['mean_tokens']:,.0f} | {item['total_cost_rmb']:.4f} |"
        )

    lines.extend([
        "",
        "这是诊断性小样本，不支持显著性或因果结论。B 相比 A 的观察通过率高 16.7 个百分点，",
        f"token 变化 {_delta_percent(summaries['B']['total_tokens'], summaries['A']['total_tokens'])}；"
        "C 相比 B 的观察通过率低 8.3 个百分点，",
        f"token 变化 {_delta_percent(summaries['C']['total_tokens'], summaries['B']['total_tokens'])}。",
        "",
        "## 逐任务结果",
        "",
        "| Case | A pass / tokens | B pass / tokens | C pass / tokens |",
        "|---|---:|---:|---:|",
    ])
    cases = sorted({str(row.get("case")) for row in rows})
    for case in cases:
        values = []
        for arm in ARM_PROTOCOLS:
            group = [row for row in rows if row["arm"] == arm and row.get("case") == case]
            values.append(
                f"{sum(bool(row.get('tests_pass')) for row in group)}/{len(group)} / "
                f"{sum(int(row.get('total_tokens', 0)) for row in group):,}"
            )
        lines.append(f"| `{case}` | " + " | ".join(values) + " |")

    b = summaries["B"]
    c = summaries["C"]
    lines.extend([
        "",
        "## 机制分解",
        "",
        "### B：确定性终止",
        "",
        f"- `verified_patch` 只触发 {b['controller_triggers']}/12 次，且都发生在 Pydantic；",
        f"  其中 hidden pass 为 {b['controller_passes']}/{b['controller_triggers']}。",
        "- 控制器证明的是“最新补丁之后某个可见 pytest 命令退出码为 0”，不是完整任务正确。",
        "  两条隐藏测试失败说明终止证据仍可能过窄，不能把 `verified_patch` 命名理解为",
        "  evaluator 已验证的补丁。",
        "- B 的总 token 明显低于 A，但由于三组不是共享随机种子的配对实验，不能把全部",
        "  token 差异或通过率差异都归因于控制器。",
        "",
        "### C：Reviewer/Reviser",
        "",
        f"- Reviewer 直接通过 {c['review_passed']} 次，这些运行 hidden pass 为 "
        f"{c['review_pass_hidden_passes']}/{c['review_passed']}。",
        f"- Reviewer 给出负面结论 {c['review_negative']} 次，触发 Reviser {c['revision_attempts']} 次；",
        f"  修订后 hidden pass 为 {c['revision_passes']}/{c['revision_attempts']}。",
        f"- 另有 {c['review_skipped']} 次因 Coder 没有候选补丁而跳过审查，均失败。",
        "- C 没有超过 B，并额外消耗约 39% token；因此当前证据不支持默认开启",
        "  Reviewer/Reviser。它更适合作为可选的补丁后检查机制，下一步应保证",
        "  Reviser 失败时可回退到候选补丁，并分别测量 Reviewer 诊断精度与 Reviser 修复收益。",
        "",
        "## 其他观察与限制",
        "",
        f"- 三组 pass-to-pass regression 标记均为 0；这只覆盖协议抽样的回归测试，不代表零回归。",
        f"- 有 {sum(item['budget_overshoots'] for item in summaries.values())} 条运行在一次模型响应后超过 "
        "700k 软上限；runner 在调用前检查预算，无法中断已经发出的响应。",
        "- 基础设施中断不会写入正式 JSONL；正式 36 行均来自同一代码 revision 和三个固定协议哈希。",
        "- 原始 trace 可能包含上游仓库源码和模型输出，但不包含 API key；账本提交前执行了凭据形态扫描。",
        "",
        "## 可复现",
        "",
        "```powershell",
        "python -m benchmark.analyze_e2 --strict",
        "```",
        "",
        "该命令只读取已提交 JSONL，不调用模型、不启动 Docker。",
    ])
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--results", default=str(DEFAULT_RESULTS))
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--output")
    args = parser.parse_args()
    rows = load_rows(args.results)
    metadata = validate_rows(rows, strict=args.strict)
    report = markdown_report(rows, metadata)
    if args.output:
        Path(args.output).write_text(report, encoding="utf-8")
    else:
        print(report, end="")


if __name__ == "__main__":
    main()
