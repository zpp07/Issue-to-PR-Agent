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

## Exact unresolved issue

The downloaded snapshots live below this project's `.git` directory. Git
therefore discovers the parent `multi-agent` repository and reports every
snapshot patch as `Skipped patch` with exit code 0. Passing `--no-index` alone
does not change that behavior.

The last diagnostic established that `GIT_CEILING_DIRECTORIES` must be set to
the **parent of the snapshot root**, not the snapshot root itself. With the
snapshot root as the ceiling, `git rev-parse --show-toplevel` still returns the
main repository; with `root.parent` as the ceiling, it correctly reports that
there is no Git repository.

## First action when resuming

In both `benchmark.real_snapshot.patch_applies` and
`benchmark.real_verify._apply`, pass a copied environment to `subprocess.run`
with:

```python
env["GIT_CEILING_DIRECTORIES"] = str(root.resolve().parent)
```

Then run, in order:

1. `python -m pytest tests/test_phase45.py -q`
2. `python -m benchmark.real_snapshot --limit 6 --context-files 40`
3. `python -m benchmark.real_verify facebookresearch__hydra-1791 --image issue-to-pr-hydra:phase45 --docker-binary <absolute docker.exe> --pass-sample 5`

Do not trust or commit `benchmark/real/test_audit.jsonl` until that sequence
passes: the current file only records diagnostic harness failures.
