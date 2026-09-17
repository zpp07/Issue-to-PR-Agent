"""Generate a dependency-free Markdown benchmark summary."""
from collections import defaultdict
import json
from pathlib import Path


def render(records, title="Phase 3 benchmark dashboard"):
    groups = defaultdict(list)
    for row in records:
        groups[row["method"]].append(row)
    lines = [f"# {title}", "", "| Method | Cases | Hidden pass | Pass rate | Avg tokens | Avg cost (RMB) | Avg seconds | Test tampering |",
             "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for method in sorted(groups):
        rows = groups[method]
        passed = sum(bool(row["tests_pass"]) for row in rows)
        n = len(rows)
        lines.append(
            f"| {method} | {n} | {passed} | {passed/n:.1%} | "
            f"{sum(row['total_tokens'] for row in rows)/n:.0f} | "
            f"{sum(row['cost_rmb'] for row in rows)/n:.4f} | "
            f"{sum(row['elapsed_seconds'] for row in rows)/n:.2f} | "
            f"{sum(bool(row['test_tampered']) for row in rows)} |"
        )
    unique_jobs = {(row["case"], row["method"]) for row in records}
    lines.extend(["", "## Integrity", "",
                  f"- Result rows / unique jobs: {len(records)} / {len(unique_jobs)}",
                  f"- Public-test tampering attempts: {sum(bool(r['test_tampered']) for r in records)}",
                  f"- Container timeouts: {sum(bool(r['container_timeout']) for r in records)}",
                  f"- Total model cost: ¥{sum(r['cost_rmb'] for r in records):.4f}", ""])
    failures = [row for row in records if not row["tests_pass"]]
    lines.extend(["## Hidden-test failures", "",
                  "| Case | Method | Agent claimed pass | Tokens | Verification |",
                  "|---|---|---:|---:|---|"])
    for row in sorted(failures, key=lambda item: (item["case"], item["method"])):
        output_lines = [line.strip() for line in row.get("verification_output", "").splitlines() if line.strip()]
        summary = next((line for line in output_lines if line.startswith("FAILED ")),
                       output_lines[-1] if output_lines else "no output")
        summary = summary.replace("|", "\\|")[:180]
        lines.append(
            f"| {row['case']} | {row['method']} | {bool(row.get('agent_passed'))} | "
            f"{row['total_tokens']} | {summary} |"
        )
    if not failures:
        lines.append("| — | — | — | — | None |")
    lines.append("")
    return "\n".join(lines)


def generate(jsonl_path, output_path, title="Phase 3 benchmark dashboard"):
    rows = [json.loads(line) for line in Path(jsonl_path).read_text(encoding="utf-8").splitlines() if line.strip()]
    Path(output_path).write_text(render(rows, title=title), encoding="utf-8")
