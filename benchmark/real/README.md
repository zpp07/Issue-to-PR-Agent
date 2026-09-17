# Phase 4.5: real-repository benchmark calibration

This directory is the evaluator side of the real-task benchmark. It is never
mounted into the agent's target worktree because `selected.jsonl` contains the
upstream reference patch and regression-test patch.

## Why SWE-Gym Lite

The first calibration set uses
[SWE-Gym Lite](https://github.com/SWE-Gym/SWE-Gym), which is derived from real
GitHub issues and pull requests. The importer reads the 230-row Lite split,
normalizes the fields and deliberately discards `hints_text`: sampled hints
contained solution-level information and would contaminate an agent run.

The deterministic metadata filter currently accepts 177 rows and rejects
tasks that have no regression-test patch, no fail-to-pass tests, an unsuitable
issue length, or an overly large/non-Python production patch. It then selects
at most two tasks from one repository. This produces 12 curator candidates in
`selected.jsonl`; the first six have been snapshot-audited.

## Validation states

The benchmark uses explicit states rather than treating upstream metadata as
proof of reproducibility:

1. `metadata_only`: normalized and accepted by the cheap filter.
2. `snapshot_verified`: the exact base commit was downloaded and expected
   production files were found.
3. `patch_verified`: both the reference fix and regression-test patch pass
   `git apply --check` on that snapshot.
4. `tests_verified`: fail-to-pass tests fail before the fix, pass after it,
   and selected pass-to-pass tests remain green in a hermetic environment.

Only state 4 is ready for a scored end-to-end agent comparison. State 3 is a
curated execution candidate, not a validated benchmark task.

## Current audit (2026-09-17)

| Instance | Repository files | Patch state | Top-10 retrieval finds gold file | Test state |
|---|---:|---|---:|---|
| `iterative__dvc-4124` | 372 | patch verified | no | not run |
| `facebookresearch__hydra-1791` | 1,122 | patch verified | no | verified: 4 fail before, 52 pass after |
| `iterative__dvc-4185` | 374 | patch verified | no | not run |
| `Project-MONAI__MONAI-3326` | 777 | patch verified | yes | not run |
| `pydantic__pydantic-8977` | 401 | patch verified | no | not run |
| `facebookresearch__hydra-1006` | 723 | patch verified | no | verified: 6 fail before, 35 pass after |

All six exact snapshots and patches are structurally valid. Two Hydra tasks
also pass the full pre-fix/post-fix transition in a networkless container. The current
dependency-free hybrid retriever recalls the edited production file for only
1/6 tasks at top 10. This is a useful negative result: the earlier six-task
synthetic suite was too small/easy to establish retrieval quality.

For every audited case, `snapshot_audit.jsonl` also contains a 40-file context
pack: top retrieval results followed by stable distractors. Gold and test file
paths are never inserted by the curator merely because the reference solution
knows them. The full repository remains the source of truth for execution.

## Reproduce

Import and select candidates (proxy is optional):

```powershell
python -m benchmark.import_swegym `
  --proxy http://127.0.0.1:7890 `
  --limit 12 --max-per-repo 2
```

Download exact snapshots and audit the first six:

```powershell
python -m benchmark.real_snapshot `
  --proxy http://127.0.0.1:7890 `
  --limit 6 --context-files 40
```

Downloaded archives and worktrees live under `.local/real_benchmark/` and are
git-ignored. The normalized candidate manifest and audit evidence are tracked.

## Remaining gate before scored runs

The official prebuilt SWE-Gym images were not anonymously pullable from the
documented registry during this run. A pinned Hydra environment is therefore
provided in `docker/hydra.Dockerfile`; it has verified two tasks with network
disabled during execution. The next step is an equivalent DVC environment for
the two selected DVC tasks. A task moves to `tests_verified` only after the
pre-fix/post-fix transition is recorded. Phase 6 should wait until at least
three, preferably six, tasks clear this gate.
