"""Docker execution boundary for untrusted repository commands.

The LLM and file tools stay on the host, but every repository command runs in
an ephemeral, networkless, non-root container.  Hidden tests are mounted only
for the independent verification pass and are never present in the agent's
container invocation.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import shutil
import subprocess
from typing import Sequence

from core.tools import COMMAND_TIMEOUT, MAX_COMMAND_OUTPUT, ToolResult


class DockerUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class SandboxResult:
    exit_code: int
    output: str
    timed_out: bool = False


@dataclass(frozen=True)
class DockerLimits:
    cpus: float = 1.0
    memory: str = "512m"
    pids: int = 128
    timeout: int = 60


class DockerSandbox:
    def __init__(self, image: str = "issue-to-pr-runner:phase3",
                 docker_binary: str = "docker", limits: DockerLimits | None = None):
        self.image = image
        self.docker_binary = self._resolve_docker_binary(docker_binary)
        self.limits = limits or DockerLimits()

    @staticmethod
    def _resolve_docker_binary(docker_binary: str) -> str:
        """Resolve Docker Desktop installs that have not reached this shell's PATH yet."""
        resolved = shutil.which(docker_binary)
        if resolved:
            return resolved
        if docker_binary != "docker":
            return docker_binary

        candidates: list[Path] = []
        local_app_data = os.environ.get("LOCALAPPDATA")
        program_files = os.environ.get("ProgramFiles")
        if local_app_data:
            candidates.extend([
                Path(local_app_data) / "Programs/DockerDesktop/resources/bin/docker.exe",
                Path(local_app_data) / "Programs/Docker/Docker/resources/bin/docker.exe",
            ])
        if program_files:
            candidates.append(Path(program_files) / "Docker/Docker/resources/bin/docker.exe")
        for candidate in candidates:
            try:
                if candidate.is_file():
                    return str(candidate)
            except OSError:
                # A workspace sandbox may forbid stat() outside the repository.
                # Keep the unresolved command so normal PATH/error handling applies.
                continue
        return docker_binary

    def available(self) -> bool:
        return shutil.which(self.docker_binary) is not None

    def require_available(self) -> None:
        if not self.available():
            raise DockerUnavailable(
                "未找到 docker。Phase 3 禁止把不可信代码静默回退到宿主机；"
                "请安装并启动 Docker Desktop 后重试。"
            )
        probe = subprocess.run(
            [self.docker_binary, "version", "--format", "{{.Server.Version}}"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10,
        )
        if probe.returncode != 0:
            raise DockerUnavailable(
                "docker 命令存在，但 daemon 不可用：" + (probe.stderr or probe.stdout).strip()[:300]
            )

    def container_argv(self, workspace: str | Path, command: Sequence[str], *,
                       workdir: str | Path | None = None, read_only_workspace: bool = False,
                       hidden_tests: str | Path | None = None) -> list[str]:
        workspace = Path(workspace).resolve()
        current = Path(workdir).resolve() if workdir else workspace
        if current != workspace and workspace not in current.parents:
            raise ValueError("容器工作目录必须位于 workspace 内")
        relative = current.relative_to(workspace).as_posix()
        container_workdir = "/workspace" if relative == "." else f"/workspace/{relative}"
        workspace_mount = f"type=bind,src={workspace},dst=/workspace"
        if read_only_workspace:
            workspace_mount += ",readonly"
        argv = [
            self.docker_binary, "run", "--rm", "--network", "none",
            "--user", "65532:65532", "--cpus", str(self.limits.cpus),
            "--memory", self.limits.memory, "--pids-limit", str(self.limits.pids),
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
            "--read-only", "--tmpfs", "/tmp:rw,noexec,nosuid,size=64m",
            "--env", "PYTHONDONTWRITEBYTECODE=1", "--env", "PYTHONPATH=/workspace",
            "--mount", workspace_mount,
            "--workdir", container_workdir,
        ]
        if hidden_tests is not None:
            hidden = Path(hidden_tests).resolve()
            argv.extend(["--mount", f"type=bind,src={hidden},dst=/hidden,readonly"])
        argv.extend([self.image, *command])
        return argv

    def run(self, workspace: str | Path, command: Sequence[str], *,
            workdir: str | Path | None = None, read_only_workspace: bool = False,
            hidden_tests: str | Path | None = None, timeout: int | None = None) -> SandboxResult:
        self.require_available()
        argv = self.container_argv(
            workspace, command, workdir=workdir,
            read_only_workspace=read_only_workspace, hidden_tests=hidden_tests,
        )
        effective_timeout = timeout or self.limits.timeout
        try:
            proc = subprocess.run(
                argv, capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=effective_timeout,
            )
            output = (proc.stdout + proc.stderr) or "(无输出)"
            if len(output) > MAX_COMMAND_OUTPUT:
                output = output[:MAX_COMMAND_OUTPUT] + f"\n...[已截断，共 {len(output)} 字符]"
            return SandboxResult(proc.returncode, output)
        except subprocess.TimeoutExpired as exc:
            partial = ((exc.stdout or "") + (exc.stderr or "")) if isinstance(exc.stdout, str) else ""
            return SandboxResult(124, partial + f"\n容器命令超时（>{effective_timeout}s）", True)

    def command_runner(self, workspace: str | Path):
        workspace = Path(workspace).resolve()

        def runner(argv, cwd=None, timeout=COMMAND_TIMEOUT):
            result = self.run(workspace, argv, workdir=cwd or workspace, timeout=timeout)
            return ToolResult(
                result.output,
                "error" if result.timed_out else "ok",
                {
                    "command_exit_code": result.exit_code,
                    "command_succeeded": result.exit_code == 0,
                    "command_timed_out": result.timed_out,
                },
            )

        return runner

    def run_hidden_tests(self, workspace: str | Path, hidden_tests: str | Path,
                         test_command: Sequence[str] | None = None) -> SandboxResult:
        command = list(test_command or ["python", "-m", "pytest", "-q", "/hidden", "-p", "no:cacheprovider"])
        return self.run(
            workspace, command, read_only_workspace=True,
            hidden_tests=hidden_tests, timeout=self.limits.timeout,
        )
