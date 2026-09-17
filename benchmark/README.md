# Phase 3 benchmark

The catalog contains 22 deterministic Python repair tasks. Each task is
materialized into two sibling directories:

- `workspace/`: `buggy.py` and public tests visible to the agent;
- `hidden_tests/`: verifier-only tests, never mounted in an agent container.

Build the sandbox image and run a small smoke matrix first:

```powershell
docker build -t issue-to-pr-runner:phase3 -f docker\Dockerfile .
python -m benchmark.run --cases case01_add,case02_grade --methods single,reviewer,planner
```

Then run the full 22 × 3 matrix:

```powershell
python -m benchmark.run --cases all --methods single,reviewer,planner
```

Every row records hidden-test success, test tampering, timeout, review rounds,
token usage, estimated cost and elapsed time. `results.md` is generated from
the append-only JSONL output. The runner refuses to run when Docker is absent;
there is intentionally no host-execution fallback for untrusted code.

## Phase 4 harder suite

`harder_catalog.py` contains six cross-file tasks whose contract is split
between implementation, configuration and documentation. Run the controlled
browse-vs-hybrid ablation with:

```powershell
python -m benchmark.harder_run --cases all --methods browse,hybrid `
  --output benchmark/harder_controlled_results.jsonl
```

The final controlled run passed 6/6 for both methods. Hybrid retrieval used
5.3% more tokens, but 6.6% less wall time and 8.1% fewer file reads. The earlier
agent-driven search pilot is retained as `harder_results.*`; repeated search
was materially worse than one system-controlled retrieval. These are honest
negative/neutral findings, not evidence that retrieval improves success on a
small repository.
