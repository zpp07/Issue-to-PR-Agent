"""Read-only startup diagnostics for the local HTTP UI.

Never read a .env file or print the value of a credential.  A file's mere
presence is deliberately reported as *unverified*, not as a working key.
"""
from __future__ import annotations

from dataclasses import dataclass
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import sys

from core.sandbox import DockerSandbox


@dataclass(frozen=True)
class StartupCheck:
    name: str
    status: str
    detail: str


def startup_checks(root: Path) -> list[StartupCheck]:
    """Inspect prerequisites without contacting a model or running a container."""
    checks: list[StartupCheck] = []
    python_ok = sys.version_info >= (3, 10)
    checks.append(StartupCheck(
        "Python", "就绪" if python_ok else "缺失",
        f"{Path(sys.executable).resolve()} ({sys.version.split()[0]})；" + (
            "满足 >=3.10" if python_ok else "需要 >=3.10；服务无法启动"
        ),
    ))

    missing = [name for name, module in (("openai", "openai"), ("python-dotenv", "dotenv"))
               if importlib.util.find_spec(module) is None]
    checks.append(StartupCheck(
        "项目依赖", "就绪" if not missing else "退化",
        "openai、python-dotenv 已安装" if not missing else
        f"缺少 {', '.join(missing)}；页面仍可打开，但模型调用/配置加载使用后备实现。"
        '建议运行 python -m pip install -e ".[dev]"',
    ))

    git_binary = shutil.which("git")
    checks.append(StartupCheck(
        "Git", "就绪" if git_binary else "缺失",
        "已找到 git" if git_binary else "未找到 git；创建任务时无法准备仓库 worktree",
    ))

    docker_binary = DockerSandbox._resolve_docker_binary("docker")
    image = os.environ.get("AGENT_SANDBOX_IMAGE") or "issue-to-pr-runner:phase3"
    if shutil.which(docker_binary) is None:
        checks.append(StartupCheck(
            "Docker", "缺失", "未找到 docker；执行及测试无法在隔离容器中运行",
        ))
        checks.append(StartupCheck(
            "Runner 镜像", "未检查", f"{image}；Docker 缺失，无法检查镜像",
        ))
    else:
        try:
            probe = subprocess.run(
                [docker_binary, "version", "--format", "{{.Server.Version}}"],
                capture_output=True, timeout=10, check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            probe = None
        if probe is None or probe.returncode != 0:
            checks.append(StartupCheck(
                "Docker", "缺失", "docker 命令可见，但 daemon 未就绪；请启动 Docker Desktop，"
                "否则执行及测试会失败",
            ))
            checks.append(StartupCheck(
                "Runner 镜像", "未检查", f"{image}；Docker daemon 未就绪，无法检查镜像",
            ))
        else:
            checks.append(StartupCheck("Docker", "就绪", "Docker daemon 可用"))
            try:
                inspected = subprocess.run(
                    [docker_binary, "image", "inspect", "--format", "{{.Id}}", image],
                    capture_output=True, timeout=10, check=False,
                )
            except (OSError, subprocess.TimeoutExpired):
                inspected = None
            checks.append(StartupCheck(
                "Runner 镜像", "就绪" if inspected and inspected.returncode == 0 else "缺失",
                f"{image} 已存在" if inspected and inspected.returncode == 0 else
                f"找不到 {image}；执行时容器启动会失败。请先运行 "
                "docker build -t issue-to-pr-runner:phase3 -f docker/Dockerfile . "
                "（自定义 AGENT_SANDBOX_IMAGE 时请构建对应标签）",
            ))

    if "DEEPSEEK_API_KEY" in os.environ:
        model_status = "存在"
        model_detail = ("检测到 DEEPSEEK_API_KEY 环境变量名；未读取其值，"
                        "也未验证是否非空或有效。创建任务时模型调用仍可能失败")
    elif (root / ".env").is_file():
        model_status = "未验证"
        model_detail = ("发现 .env 文件但未读取；请确认其中配置了 DEEPSEEK_API_KEY，"
                        "否则创建任务时模型调用会失败")
    else:
        model_status = "缺失"
        model_detail = ("未发现 DEEPSEEK_API_KEY 环境变量或项目 .env 文件；"
                        "创建任务时模型调用会失败")
    checks.append(StartupCheck("模型配置", model_status, model_detail))
    return checks


def print_startup_checks(checks: list[StartupCheck]) -> None:
    print("启动自检（只检查本机前提，不调用模型、不运行容器）：", flush=True)
    for check in checks:
        print(f"  [{check.status}] {check.name}：{check.detail}", flush=True)
