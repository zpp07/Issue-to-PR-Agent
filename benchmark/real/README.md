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
`selected.jsonl`; seven have been snapshot-audited and six form the frozen
scored set in `verified_manifest.json`.

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
| `iterative__dvc-4124` | 372 | patch verified | no | verified: 6 fail before, 11 pass after |
| `facebookresearch__hydra-1791` | 1,122 | patch verified | no | verified: 4 fail before, 52 pass after |
| `iterative__dvc-4185` | 374 | patch verified | no | verified: 7 fail before, 12 pass after |
| `Project-MONAI__MONAI-3326` | 777 | patch verified | yes | not run |
| `pydantic__pydantic-8977` | 401 | patch verified | no | verified: 4 fail before, 288 pass after |
| `facebookresearch__hydra-1006` | 723 | patch verified | no | verified: 6 fail before, 35 pass after |
| `dask__dask-7894` | 338 | patch verified | yes | verified: 5 fail before, 26 pass after |

All seven audited exact snapshots and patches are structurally valid. Six tasks
from Hydra, DVC, Pydantic and Dask pass the full pre-fix/post-fix transition in
networkless containers; MONAI remains a structurally valid reserve task. The
current dependency-free hybrid retriever recalls the edited production file
for only 1/6 verified tasks at top 10. The reserve MONAI case is also a
retrieval hit, but is not included in the scored denominator. This is a useful
negative result: the earlier six-task synthetic suite was too small/easy to
establish retrieval quality.

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

Download exact snapshots and audit the default first six:

```powershell
python -m benchmark.real_snapshot `
  --proxy http://127.0.0.1:7890 `
  --limit 6 --context-files 40
```

Downloaded archives and worktrees live under `.local/real_benchmark/` and are
git-ignored. The normalized candidate manifest and audit evidence are tracked.
Use `--instances id1,id2` to audit additional selected cases without replacing
existing audit rows.

## Scored-run gate

The official prebuilt SWE-Gym images were not anonymously pullable from the
documented registry during this run. A pinned Hydra environment is therefore
provided in `docker/hydra.Dockerfile`; the DVC, Pydantic and Dask Dockerfiles
pin their corresponding historical dependency sets. Together they verify six
tasks with network disabled during execution. MONAI is deferred because a
credible environment requires a much larger Torch image. A task moves to
`tests_verified` only after the pre-fix/post-fix transition is recorded. The
six-task Phase 4.5 gate is now complete; Phase 6 can proceed without using the
earlier toy suite as its only quality signal.

Run one inexpensive smoke trial before a full repeated evaluation:

```powershell
python -m benchmark.real_run --cases dask__dask-7894 --trials 1
```

Then run three independent stochastic trials per verified task, with resumable
JSONL output:

```powershell
python -m benchmark.real_run --trials 3 --resume
```

The provider does not expose a reliable random-seed control here, so these are
recorded as independent `trial` values rather than falsely claiming seeded
reproducibility. Each row records success, test tampering, modified/gold-file
overlap, tool usage, tokens, estimated cost and elapsed time.

## Smoke calibration

The first high-budget Dask smoke exposed a tooling failure rather than a model
repair failure: the retriever found the correct file, but the old whole-file
read/write interface produced 27 searches, no edit, and a failed score after
461,167 tokens. After adding bounded line reads and exact local replacement,
the same case modified the correct `dask/array/overlap.py`, left tests intact,
and passed all 26 evaluator-selected tests. It used 325,328 tokens and an
estimated RMB 0.6915. This before/after result is evidence that tool ergonomics
must be calibrated before interpreting an Agent benchmark as model quality.

The first standardized trial across all six verified tasks passed 3/6: both
DVC tasks and Dask passed, while both Hydra tasks and Pydantic failed. Full
per-run metrics and source-free navigation traces are stored in `results.jsonl`;
the compact table is in `results.md`. The run used 1,349,318 tokens and an
estimated RMB 2.8051 over 19.1 cumulative minutes. Its failure traces motivated
an eight-call search budget before additional trials: two failures reached the
correct implementation but spent the remaining steps searching instead of
editing.

A targeted second trial on the three failures produced 0/3 additional passes.
Hydra-1791 and Pydantic still made no edit; Hydra-1006 edited the correct file
but failed regression tests again. Across the nine recorded runs the score is
3/9, with 2,203,578 tokens and estimated RMB 4.5622. This falsifies the idea
that a search-call cap alone creates a reliable transition from investigation
to action. The next strategy is an explicit read-only planning phase followed
by a search-free execution phase.

The runner now supports that strategy with `--strategy plan_execute`. Planning
can search/read and must terminate in a structured `plan_finish`; execution
receives the approved plan but has no search tool, so its budget is reserved for
confirming exact code, editing and testing. Results include the strategy and
phase-tagged traces, keeping comparisons separate from the original `single`
baseline.

The first Hydra-1791 `plan_execute` smoke stopped in planning after 10 steps:
it found and read the correct implementation but never called `plan_finish`.
This cost 60,914 tokens (estimated RMB 0.1274) and did not start execution.
The trace exposed a second tooling mismatch: bounded reads could return more
than the Agent loop's old 2,000-character tool-output limit. The limits are now
aligned at 12,000 characters/bytes, whole-file reads above that size require an
explicit line range, and the Planner receives a final-step instruction that
requires `plan_finish` instead of another read/search.

On the next Hydra-1791 trial, that fix worked: planning completed and execution
modified the correct `hydra/core/default_element.py`. The candidate still
failed because its implementation broke an existing global-package test; the
Executor detected the regression but could not repair it before 25 steps. The
run used 478,796 tokens (estimated RMB 1.0112). This moves the bottleneck from
phase transition to patch correctness/revision. Future rows now retain the
structured repair plan and a capped unified Agent diff, and retryable provider
disconnects receive one whole-job retry without becoming model failures.
