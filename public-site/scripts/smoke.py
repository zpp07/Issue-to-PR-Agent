"""Read-only HTTP verification of a local tunnel or the final HTTPS website."""
import argparse
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, Request, build_opener


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:18080")
    args = parser.parse_args()
    base = args.url.rstrip("/")
    parsed = urlsplit(base)
    if parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path:
        parser.error("只允许不带路径、凭据或查询参数的根地址")
    if parsed.scheme != "https" and not (parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost"}):
        parser.error("公网只使用HTTPS，本地预览只使用回环地址")
    if parsed.scheme == "https" and parsed.hostname != "zpcode.online":
        parser.error("公网验证仅限本项目域名 zpcode.online")
    opener = build_opener(ProxyHandler({}))

    def check(path, expected, method="GET"):
        request = Request(base + path, method=method)
        try:
            response = opener.open(request, timeout=10)
        except HTTPError as error:
            response = error
        with response:
            assert response.status == expected, (path, method, response.status, expected)
            assert response.headers.get("X-Content-Type-Options") == "nosniff", (path, "nosniff")
            assert "connect-src 'none'" in response.headers.get("Content-Security-Policy", ""), (path, "CSP")
            data = response.read()
            if path.endswith(".png") and method != "HEAD" and expected == 200:
                assert response.headers.get_content_type() == "image/png", (path, "image MIME")
                assert data.startswith(b"\x89PNG\r\n\x1a\n"), (path, "PNG signature")
            body = data.decode("utf-8") if not path.endswith(".png") else ""
            if method != "HEAD" and expected == 200 and (path.endswith(".html") or path == "/"):
                assert "代码实践手记" in body
                if parsed.scheme == "https":
                    assert "本地预览版本" not in body
                    assert "https://beian.miit.gov.cn/" in body
        print(f"OK {method} {path} -> {expected}")

    for path in ["/", "/project.html", "/case.html", "/privacy.html", "/style.css", "/favicon.svg", "/robots.txt", "/workbench.png", "/healthz"]:
        check(path, 200)
    check("/", 200, "HEAD")
    for path in ["/tasks", "/tasks/example/events", "/ui-config", "/docs", "/.env", "/agent_tasks.sqlite3", "/deploy/compose.yaml", "/unknown"]:
        check(path, 404)
    check("/tasks", 405, "POST")
    print("静态展示路由与禁用API检查通过；不是Agent执行、ICP备案或完整安全审计的证明。")


if __name__ == "__main__":
    main()
