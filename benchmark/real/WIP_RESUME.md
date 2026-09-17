# Phase 4.5 resume point (2026-09-17 noon)

Work is paused on branch `phase4.5-real-benchmark`.

## Completed before the pause

- Commit `e46a6c5` contains the SWE-Gym Lite importer, deterministic selection,
  six exact repository snapshots audits, 40-file context packs, and docs.
- Docker Desktop is running. The CLI is located at
  `C:\Users\32744\AppData\Local\Programs\DockerDesktop\resources\bin\docker.exe`.
- `m.daocloud.io/docker.io/library/python:3.8-slim` is present locally.
- `issue-to-pr-hydra:phase45` was built successfully from
  `docker/hydra.Dockerfile`, using the Tsinghua PyPI mirror.
- The image can generate Hydra's ANTLR parsers in a networkless container.
- `real_verify.py` distinguishes collection/environment failures from genuine
  regression-test failures and runs the before/after transition in a
  read-only, networkless container.

## Resolved during the short continuation

Patch application now sets `GIT_CEILING_DIRECTORIES` to the parent of each
snapshot and uses an explicit temporary patch file. The nested-repository
regression test passes, and all seven snapshots were audited with real patch
application.

Six tasks are now `tests_verified` in `test_audit.jsonl`:

- `facebookresearch__hydra-1791`: four regression failures before the fix;
  52 selected tests pass after the fix.
- `facebookresearch__hydra-1006`: six regression failures before the fix;
  35 selected tests pass after the fix.
- `iterative__dvc-4124`: six regression failures before the fix; 11 selected
  tests pass after the fix.
- `iterative__dvc-4185`: seven regression failures before the fix; 12 selected
  tests pass after the fix.
- `pydantic__pydantic-8977`: four regression failures before the fix; 288
  selected tests pass after the fix.
- `dask__dask-7894`: five regression failures before the fix; 26 selected
  tests pass after the fix.

## First action when resuming

Phase 4.5's six-task verification gate is complete and the scored set is frozen
in `verified_manifest.json` (excluding the unverified MONAI reserve). The next
session should run the current agent on it with repeated independent trials,
using `python -m benchmark.real_run --trials 3 --resume`, and only then
use those failures to scope Phase 6. Keep environment construction
network-enabled, but all verification and agent runs networkless and read-only.

The first Dask smoke exposed a real tooling bottleneck: search found
`dask/array/overlap.py`, but whole-file reads only returned the beginning of
the file and whole-file writes made a surgical fix impractical. The runner now
offers bounded line-range reads plus exact `replace_text` edits and records a
compact, source-free navigation trace for subsequent diagnosis.
