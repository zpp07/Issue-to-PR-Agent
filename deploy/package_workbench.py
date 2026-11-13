"""Bundle the workbench sources without local configuration or task data."""

from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
import tarfile


ROOT = Path(__file__).resolve().parents[1]


def package():
    files = [
        "pyproject.toml", "issue_to_pr.py", "coder_reviewer.py", "service_http.py", "service_api.py",
        "service_trial.py", "docker/Dockerfile", "web/index.html", "web/app.js", "web/app.css",
        "web/trial.html", "web/trial.js", "deploy/issue-to-pr.service", "docs/WORKBENCH_DEPLOYMENT.md",
    ]
    files += [path.relative_to(ROOT).as_posix() for path in sorted((ROOT / "core").glob("*.py"))]
    for name in files:
        path = ROOT / name
        if not path.is_file() or path.resolve() != path:
            raise ValueError(f"不是常规源码文件：{name}")
    artifacts = ROOT / ".local" / "artifacts"
    artifacts.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    path = artifacts / f"issue-to-pr-workbench-{stamp}.tar.gz"
    with tarfile.open(path, "x:gz") as archive:
        for name in files:
            archive.add(ROOT / name, arcname=name, recursive=False)
    digest = sha256(path.read_bytes()).hexdigest()
    Path(str(path) + ".sha256").write_text(f"{digest}  {path.name}\n", encoding="ascii")
    print(path)


if __name__ == "__main__":
    package()
