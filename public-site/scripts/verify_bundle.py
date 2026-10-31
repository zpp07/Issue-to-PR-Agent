"""Validate an extracted deployment bundle before starting its services."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FILES = {
    "dist/index.html", "dist/project.html", "dist/case.html", "dist/privacy.html",
    "dist/style.css", "dist/favicon.svg", "dist/robots.txt", "dist/workbench.png",
    "deploy/compose.yaml", "deploy/nginx.conf", "deploy/preview.env", "deploy/public.env", "deploy/Caddyfile",
    "scripts/prepare_release.py", "scripts/smoke.py", "scripts/verify_bundle.py", "README.md", "ICP_GUIDE.md", "VERIFICATION.md",
}


def verify(root):
    data = json.loads((root / "MANIFEST.json").read_text(encoding="utf-8"))
    if set(data["files"]) != FILES:
        raise ValueError("部署清单与固定白名单不符")
    for name, expected in data["files"].items():
        path = root / name
        if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(root.resolve()):
            raise ValueError(f"异常文件：{name}")
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != expected:
            raise ValueError(f"文件哈希不符：{name}")
    dist = root / "dist"
    if {path.name for path in dist.iterdir()} != {Path(name).name for name in FILES if name.startswith("dist/")}:
        raise ValueError("静态目录含未知文件")
    print(f"部署白名单与SHA-256校验通过：{len(FILES)}个文件；此校验不能证明来源可信或ICP已获批。")


if __name__ == "__main__":
    verify(ROOT)
