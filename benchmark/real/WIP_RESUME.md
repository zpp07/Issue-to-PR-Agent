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
regression test passes, and all six snapshots were re-audited with real patch
application.

Four tasks are now `tests_verified` in `test_audit.jsonl`:

- `facebookresearch__hydra-1791`: four regression failures before the fix;
  52 selected tests pass after the fix.
- `facebookresearch__hydra-1006`: six regression failures before the fix;
  35 selected tests pass after the fix.
- `iterative__dvc-4124`: six regression failures before the fix; 11 selected
  tests pass after the fix.
- `iterative__dvc-4185`: seven regression failures before the fix; 12 selected
  tests pass after the fix.

## First action when resuming

Build a pinned Pydantic environment and verify `pydantic__pydantic-8977`.
Select Dask (or another dependency-light task) instead of MONAI for the sixth
case. Keep environment construction network-enabled, but all verification and
later agent runs networkless and read-only.
