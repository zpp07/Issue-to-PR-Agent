"""Read-only provenance enrichment and failure analysis for real runs."""
from __future__ import annotations

import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path


DEFAULT_RESULTS = Path("benchmark/real/results.jsonl")
DEFAULT_LEGACY = Path("benchmark/real/legacy_provenance.json")


def row_fingerprint(raw_line: str) -> str:
    return hashlib.sha256(raw_line.rstrip("\r\n").encode("utf-8")).hexdigest()


def load_enriched_results(
    results_path: str | Path = DEFAULT_RESULTS,
    legacy_path: str | Path = DEFAULT_LEGACY,
) -> list[dict]:
    legacy_file = Path(legacy_path)
    mapping = json.loads(legacy_file.read_text(encoding="utf-8")) if legacy_file.is_file() else {}
    rows = []
    for raw in Path(results_path).read_text(encoding="utf-8").splitlines():
        if not raw.strip():
            continue
        row = json.loads(raw)
        fingerprint = row_fingerprint(raw)
        inferred = mapping.get(fingerprint)
        if inferred:
            for key, value in inferred.items():
                row.setdefault(key, value)
            row["protocol_inferred"] = True
            row["provenance_source"] = "execution_history_and_git_timeline"
        elif "protocol_hash" not in row:
            row.setdefault("protocol_version", "legacy-unversioned")
            row["protocol_inferred"] = True
            row["provenance_source"] = "unavailable"
        row["row_sha256"] = fingerprint
        rows.append(row)
    return rows


def successful_edit_steps(row: dict) -> list[int]:
    steps = []
    structured = any("mutation_applied" in item for item in row.get("trace", []))
    for item in row.get("trace", []):
        if item.get("action") not in {"replace_text", "write_file"}:
            continue
        if structured:
            applied = bool(item.get("mutation_applied"))
        else:
            preview = str(item.get("result_preview", ""))
            applied = not preview.startswith(("拒绝", "写入失败", "替换失败"))
        if applied and isinstance(item.get("step"), int):
            steps.append(item["step"])
    return steps


def classify_run(row: dict) -> str:
    """Assign the mutually exclusive A-E class, deepest evidence first."""
    if row.get("failure_class") in {"A", "B", "C", "D", "E"}:
        return row["failure_class"]
    if bool(row.get("tests_pass")):
        return "A"
    if row.get("strategy", "single") == "plan_execute" and row.get("planner_completed") is False:
        return "D"
    successful = row.get("successful_mutations")
    if successful is None:
        successful = len(successful_edit_steps(row)) if row.get("modified_files") else 0
    if not successful:
        return "B"
    if row.get("regression_detected") is True or row.get("pass_to_pass_pass") is False:
        return "E"
    return "C"


def analyze(rows: list[dict]) -> list[dict]:
    groups = defaultdict(lambda: {"runs": 0, "tokens": 0, "cost_rmb": 0.0, "first_edits": []})
    for row in rows:
        label = classify_run(row)
        group = groups[label]
        group["runs"] += 1
        group["tokens"] += int(row.get("total_tokens", 0))
        group["cost_rmb"] += float(row.get("cost_rmb", 0.0))
        edits = successful_edit_steps(row)
        if edits:
            group["first_edits"].append(min(edits))
    return [
        {"class": label, **groups[label], "cost_rmb": round(groups[label]["cost_rmb"], 4)}
        for label in "ABCDE" if label in groups
    ]


def markdown_report(summary: list[dict]) -> str:
    lines = [
        "| Class | Runs | Tokens | Cost (RMB) | First successful edit steps |",
        "|---|---:|---:|---:|---|",
    ]
    for item in summary:
        edits = ", ".join(map(str, item["first_edits"])) or "-"
        lines.append(
            f"| {item['class']} | {item['runs']} | {item['tokens']:,} | "
            f"{item['cost_rmb']:.4f} | {edits} |"
        )
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--results", default=str(DEFAULT_RESULTS))
    parser.add_argument("--legacy-map", default=str(DEFAULT_LEGACY))
    parser.add_argument("--show-provenance", action="store_true")
    args = parser.parse_args()
    rows = load_enriched_results(args.results, args.legacy_map)
    if args.show_provenance:
        for row in rows:
            print(json.dumps({
                "row_sha256": row["row_sha256"], "case": row.get("case"),
                "strategy": row.get("strategy", "single"), "trial": row.get("trial"),
                "code_revision": row.get("code_revision"),
                "recorded_in_revision": row.get("recorded_in_revision"),
                "protocol_version": row.get("protocol_version"),
                "protocol_inferred": row.get("protocol_inferred", False),
            }, ensure_ascii=False))
    print(markdown_report(analyze(rows)))


if __name__ == "__main__":
    main()
