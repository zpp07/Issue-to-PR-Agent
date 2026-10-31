"""Generate a checksummed four-page/CSS filing visibility update; no cloud writes."""
import hashlib
import importlib.util
import io
from pathlib import Path
import shutil
import tarfile
import tempfile
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]


def package():
    artifacts = ROOT / "artifacts"
    artifacts.mkdir(exist_ok=True)
    spec = importlib.util.spec_from_file_location("release_builder", ROOT / "scripts/prepare_release.py")
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    archive = artifacts / f"zpcode-filing-visibility-{stamp}.tar.gz"
    with tempfile.TemporaryDirectory(prefix="filing-update-", dir=artifacts) as temp:
        staging = Path(temp)
        shutil.copytree(ROOT / "dist", staging / "dist")
        release = builder.prepare(staging, "赣ICP备2026027949号", True)
        payload = {name: (release / name).read_bytes() for name in ("index.html", "project.html", "case.html", "privacy.html", "style.css")}
        for name, data in payload.items():
            if not name.endswith(".html"):
                continue
            text = data.decode("utf-8")
            if "本地预览版本" in text or "ICP 备案尚未完成" in text:
                raise ValueError(f"{name}: 预览标记未移除，拒绝生成补丁")
            if text.count("赣ICP备2026027949号") != 2:
                raise ValueError(f"{name}: 应有顶部与底部两处真实备案号")
        sums = "".join(f"{hashlib.sha256(data).hexdigest()}  {name}\n" for name, data in payload.items())
        payload["SHA256SUMS"] = sums.encode("ascii")
        with archive.open("xb") as stream:
            with tarfile.open(fileobj=stream, mode="w:gz") as tar:
                for name, data in payload.items():
                    info = tarfile.TarInfo(name)
                    info.size, info.mode = len(data), 0o644
                    tar.addfile(info, io.BytesIO(data))
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    with Path(str(archive) + ".sha256").open("x", encoding="ascii") as stream:
        stream.write(f"{digest}  {archive.name}\n")
    print(archive)
    print(f"SHA256: {digest}")
    print("仅包含四个 HTML、style.css 与 SHA256SUMS；未覆盖现有 release，未部署云端。")


if __name__ == "__main__":
    package()
