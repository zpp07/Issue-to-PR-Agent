"""Fetch and normalize SWE-Gym Lite through the dependency-free dataset API."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import ssl
from urllib.parse import urlencode
from urllib.request import HTTPSHandler, ProxyHandler, build_opener

from benchmark.real_cases import CandidatePolicy, RealCase, select_cases, write_jsonl


API = "https://datasets-server.huggingface.co/rows"
DATASET = "SWE-Gym/SWE-Gym-Lite"


def _ssl_context():
    try:
        import certifi
    except ImportError:
        return ssl.create_default_context()
    return ssl.create_default_context(cafile=certifi.where())


def fetch_rows(proxy: str | None = None, page_size: int = 100) -> list[dict]:
    handlers = [HTTPSHandler(context=_ssl_context())]
    if proxy:
        handlers.append(ProxyHandler({"http": proxy, "https": proxy}))
    opener = build_opener(*handlers)
    rows, offset, total = [], 0, None
    while total is None or offset < total:
        query = urlencode({
            "dataset": DATASET, "config": "default", "split": "train",
            "offset": offset, "length": page_size,
        })
        with opener.open(f"{API}?{query}", timeout=60) as response:
            payload = json.loads(response.read().decode("utf-8"))
        page = [item["row"] for item in payload["rows"]]
        if not page:
            break
        rows.extend(page)
        offset += len(page)
        total = int(payload["num_rows_total"])
    return rows


def normalize(rows) -> list[RealCase]:
    # Intentionally read only row fields used by RealCase.  hints_text is never
    # persisted because it frequently contains the maintainer's exact solution.
    return [RealCase.from_swegym(row) for row in rows]


def summary(cases, policy):
    accepted = [case for case in cases if policy.accepts(case)]
    rejection_counts = {}
    for case in cases:
        for reason in policy.reasons(case):
            rejection_counts[reason] = rejection_counts.get(reason, 0) + 1
    repos = sorted({case.repo for case in accepted})
    return {
        "source_rows": len(cases), "accepted_rows": len(accepted),
        "accepted_repositories": repos, "rejections": rejection_counts,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--proxy", help="optional HTTP proxy, e.g. http://127.0.0.1:7890")
    parser.add_argument("--input", help="optional offline JSON array or dataset-server payload")
    parser.add_argument("--raw-output", default=".local/real_benchmark/swegym_lite.jsonl")
    parser.add_argument("--selected-output", default="benchmark/real/selected.jsonl")
    parser.add_argument("--limit", type=int, default=12)
    parser.add_argument("--max-per-repo", type=int, default=2)
    args = parser.parse_args()
    if args.limit < 1 or args.max_per_repo < 1:
        parser.error("limit and max-per-repo must be positive")
    if args.input:
        payload = json.loads(Path(args.input).read_text(encoding="utf-8"))
        rows = payload.get("rows", payload) if isinstance(payload, dict) else payload
        rows = [item.get("row", item) for item in rows]
    else:
        rows = fetch_rows(proxy=args.proxy)
    cases = normalize(rows)
    policy = CandidatePolicy()
    selected = select_cases(cases, args.limit, args.max_per_repo, policy)
    write_jsonl(cases, args.raw_output)
    write_jsonl(selected, args.selected_output)
    print(json.dumps({**summary(cases, policy), "selected": len(selected)}, ensure_ascii=False, indent=2))
    for case in selected:
        print(f"{case.instance_id:40s} files={len(case.production_files)} lines={case.changed_lines:3d} tests={len(case.fail_to_pass)}")


if __name__ == "__main__":
    main()
