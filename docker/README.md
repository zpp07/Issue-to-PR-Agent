# Phase 3 sandbox image

From the repository root:

```powershell
docker build -t issue-to-pr-runner:phase3 -f docker/Dockerfile .
```

The image contains Python 3.11, pinned pytest/ruff versions, and Git for
worktree/read-only Git operations. The host runtime adds `--network none`,
non-root UID 65532, dropped Linux
capabilities, `no-new-privileges`, read-only container root, PID/CPU/memory
limits and a wall-clock timeout. Hidden tests are a verifier-only read-only
mount and are absent from the agent container.
