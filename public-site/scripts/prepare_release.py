"""Create a new static release after the owner has verified actual ICP approval.

No network or service activation. Never invents a filing number or overwrites a release.
"""
import argparse
import html
from pathlib import Path
import re

SITE_ROOT = Path(__file__).resolve().parent.parent
ASSETS = {"index.html", "project.html", "case.html", "privacy.html", "style.css", "favicon.svg", "robots.txt", "workbench.png"}
FILING = re.compile(r"赣ICP备[0-9]{6,20}号(?:-[0-9]{1,4})?\Z")


def prepare(root: Path, icp: str, approved: bool) -> Path:
    if not approved:
        raise ValueError("需要本人核对备案通过通知，并显式传入 --approved；本工具不能验证管局状态")
    if not FILING.fullmatch(icp):
        raise ValueError("请原样填写本站实际获批的江西ICP备案号；不自行添加网站序号，本工具仅检查格式")
    source, target = root / "dist", root / "release"
    if not source.is_dir() or source.is_symlink():
        raise ValueError("展示源目录不可用")
    if target.exists() or target.is_symlink():
        raise ValueError("release 已存在；为保留已发布版本，本工具不会覆盖，请先按运行说明备份")
    if {path.name for path in source.iterdir()} != ASSETS or any(path.is_symlink() or not path.is_file() for path in source.iterdir()):
        raise ValueError("展示目录包含未批准的文件，拒绝发布")
    contents = {}
    for path in source.iterdir():
        if path.suffix == ".png":
            contents[path.name] = path.read_bytes()
            continue
        text = path.read_text(encoding="utf-8")
        if path.suffix == ".html":
            marker = r"<!-- filing:start -->.*?<!-- filing:end -->"
            if len(re.findall(marker, text)) != 1:
                raise ValueError(f"{path.name} 的备案页脚标记异常")
            footer = '<a href="https://beian.miit.gov.cn/" rel="noreferrer">' + html.escape(icp) + '</a>'
            text = re.sub(marker, lambda _: '<!-- filing:start -->' + footer + '<!-- filing:end -->', text)
            text = text.replace('<div class="preview-notice">本地预览版本 · 尚未开放公网，非在线 Agent 服务</div>', '')
            text = text.replace('<meta name="robots" content="noindex,nofollow">', '<meta name="robots" content="index,follow">')
        if path.name == "robots.txt":
            text = "User-agent: *\nAllow: /\n"
        contents[path.name] = text.encode("utf-8")
    target.mkdir()
    try:
        for name, data in contents.items():
            (target / name).write_bytes(data)
    except Exception:
        # Keep partial output as evidence, never remove an existing release recursively.
        raise RuntimeError("写入中断；保留已有输出供核查，勿启动公网服务") from None
    return target


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--icp", required=True)
    parser.add_argument("--approved", action="store_true")
    args = parser.parse_args()
    try:
        target = prepare(SITE_ROOT, args.icp, args.approved)
    except ValueError as error:
        parser.error(str(error))
    print(f"静态 release 已生成：{target}；未配置DNS、未开放端口、未启动公网服务。")


if __name__ == "__main__":
    main()
