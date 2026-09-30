"""Dependency-free HTTP façade for the local Issue-to-PR workflow.

Run: python service_http.py --open
It is intentionally local-only (127.0.0.1) and has no authentication because
the project scope is a single-user local prototype.
"""
from __future__ import annotations

import argparse
import errno
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import sys
import threading
from urllib.parse import parse_qs, urlsplit
import webbrowser

if sys.version_info < (3, 10):
    raise SystemExit("Issue-to-PR UI 需要 Python >=3.10；请换用受支持的解释器后启动")

from core.audit import TaskStore
from core.client import make_client
from core.execution import ExecutionManager
from core.fake_publisher import FakeDraftPRPublisher
from core.github import GitHubAPI, GitHubDraftPRPublisher
from core.read_views import artifact_content, artifact_list, task_page
from core.sandbox import DockerSandbox
from core.startup import print_startup_checks, startup_checks
from core.workflow import IssueToPRWorkflow
from issue_to_pr import IssueToPRService, PlanningUnavailable

ROOT = Path(__file__).parent
WEB_DIR = ROOT / "web"
STATIC_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/app.css": ("app.css", "text/css; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
}
FAKE_PUBLISHER_MODE = False
REAL_PUBLISHER_MODE = False
configured_data_dir = os.environ.get("AGENT_DATA_DIR")
DATA_DIR = Path(configured_data_dir).resolve() if configured_data_dir else (ROOT / ".local").resolve()
DATA_DIR.mkdir(parents=True, exist_ok=True)
store = TaskStore(DATA_DIR / "agent_tasks.sqlite3")
workflow = IssueToPRWorkflow(store, DATA_DIR / "workspaces")


def service():
    sandbox = DockerSandbox(image=os.environ.get("AGENT_SANDBOX_IMAGE", "issue-to-pr-runner:phase3"))
    return IssueToPRService(
        workflow, make_client(), sandbox=sandbox,
    )


execution_manager = ExecutionManager(store, service)


def _publish_request_error(headers, port: int = 8765) -> str | None:
    """Reject browser cross-origin/form requests before any external writes."""
    host = headers.get("Host", "").lower()
    if host not in {f"127.0.0.1:{port}", f"localhost:{port}"}:
        return "真实发布只接受本地工作台 Host"
    origin = headers.get("Origin")
    if origin is not None and origin != f"http://{host}":
        return "真实发布拒绝跨来源请求"
    if headers.get("Sec-Fetch-Site", "").lower() == "cross-site":
        return "真实发布拒绝跨站点请求"
    if headers.get("Content-Type", "").split(";", 1)[0].strip().lower() != "application/json":
        return "真实发布只接受 application/json"
    return None


class Handler(BaseHTTPRequestHandler):
    def _static(self, path):
        filename, content_type = STATIC_FILES[path]
        content = (WEB_DIR / filename).read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'none'; script-src 'self'; style-src 'self'; "
            "connect-src 'self'; img-src 'self' data:; base-uri 'none'; "
            "form-action 'self'; frame-ancestors 'none'",
        )
        self.end_headers()
        self.wfile.write(content)

    def _body(self):
        length = int(self.headers.get("Content-Length", "0"))
        return json.loads(self.rfile.read(length) or b"{}")

    def _reply(self, status, payload):
        content = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def do_GET(self):
        try:
            parsed = urlsplit(self.path)
            parts = parsed.path.strip("/").split("/")
            if parsed.path in STATIC_FILES:
                return self._static(parsed.path)
            if parsed.path == "/ui-config":
                return self._reply(200, {
                    "fake_publisher": FAKE_PUBLISHER_MODE,
                    "real_publisher": REAL_PUBLISHER_MODE,
                })
            if parts == ["tasks"]:
                query = parse_qs(parsed.query, keep_blank_values=True)
                limit = int(query.get("limit", ["50"])[0])
                offset = int(query.get("offset", ["0"])[0])
                return self._reply(200, task_page(store, limit, offset))
            if len(parts) == 3 and parts[0] == "tasks" and parts[2] == "events":
                query = parse_qs(parsed.query, keep_blank_values=True)
                after = int(query.get("after", ["0"])[0])
                limit = int(query.get("limit", ["100"])[0])
                return self._reply(200, execution_manager.event_page(parts[1], after, limit))
            if len(parts) == 3 and parts[0] == "tasks" and parts[2] == "artifacts":
                return self._reply(200, artifact_list(store, parts[1]))
            if len(parts) == 4 and parts[0] == "tasks" and parts[2] == "artifacts":
                return self._reply(200, artifact_content(store, parts[1], parts[3]))
            if len(parts) == 2 and parts[0] == "tasks":
                task_id = parts[1]
                return self._reply(200, {"task": store.task(task_id), "events": store.events(task_id)})
            if len(parts) == 3 and parts[0] == "tasks" and parts[2] == "memories":
                return self._reply(200, {"memories": workflow.memory.list(parts[1])})
            self._reply(404, {"detail": "not found"})
        except KeyError as exc:
            self._reply(404, {"detail": str(exc)})
        except ValueError as exc:
            self._reply(400, {"detail": str(exc)})

    def do_POST(self):
        try:
            body, parts = self._body(), self.path.strip("/").split("/")
            if parts == ["tasks"]:
                context, plan, plan_hash = service().create_and_plan(body["repo_path"], body["issue"])
                return self._reply(201, {"task_id": context.task_id, "workspace": str(context.workspace),
                                         "plan": plan, "plan_sha256": plan_hash})
            if parts == ["tasks", "github"]:
                context, plan, plan_hash, issue = service().create_from_github_issue(
                    body["repo_path"], body["repository"], body["issue_number"], GitHubAPI.from_env(),
                )
                return self._reply(201, {
                    "task_id": context.task_id, "workspace": str(context.workspace),
                    "plan": plan, "plan_sha256": plan_hash, "github_issue": issue,
                })
            if len(parts) == 3 and parts[0] == "tasks" and parts[2] == "plan":
                plan, plan_hash = service().plan_existing(parts[1])
                return self._reply(200, {"task_id": parts[1], "plan": plan, "plan_sha256": plan_hash})
            if len(parts) == 4 and parts[0] == "tasks" and parts[2:] == ["approvals", "plan"]:
                workflow.approve_plan(parts[1], body["subject_hash"], body.get("actor", "local-user"),
                                      body["decision"], body.get("reason"))
                return self._reply(200, store.task(parts[1]))
            if len(parts) == 3 and parts[0] == "tasks" and parts[2] == "execute":
                result = execution_manager.submit(parts[1], body["target_file"], body.get("max_rounds", 3))
                return self._reply(202, result)
            if len(parts) == 4 and parts[0] == "tasks" and parts[2:] == ["approvals", "diff"]:
                workflow.approve_diff(parts[1], body["subject_hash"], body.get("actor", "local-user"),
                                      body["decision"], body.get("reason"))
                return self._reply(200, store.task(parts[1]))
            if len(parts) == 3 and parts[0] == "tasks" and parts[2] == "draft-pr":
                result = workflow.prepare_draft_pr(
                    parts[1], repository=body["repository"], base_branch=body["base_branch"],
                    head_branch=body["head_branch"], title=body["title"], body=body.get("body"),
                    remote=body.get("remote", "origin"), commit_message=body.get("commit_message"),
                )
                return self._reply(200, result)
            if len(parts) == 4 and parts[0] == "tasks" and parts[2:] == ["approvals", "publish"]:
                workflow.approve_publish(
                    parts[1], body["subject_hash"], body.get("actor", "local-user"),
                    body["decision"], body.get("reason"),
                )
                return self._reply(200, store.task(parts[1]))
            if len(parts) == 4 and parts[0] == "tasks" and parts[2:] == ["draft-pr", "publish"]:
                if not REAL_PUBLISHER_MODE:
                    return self._reply(403, {"detail": "真实发布未启用；请用 --real-publisher 重启服务"})
                port = getattr(getattr(self, "server", None), "server_address", ("127.0.0.1", 8765))[1]
                request_error = _publish_request_error(self.headers, port)
                if request_error:
                    return self._reply(403, {"detail": request_error})
                if body.get("confirmation") != "create-draft-pr":
                    return self._reply(409, {"detail": "缺少真实发布二次确认"})
                result = workflow.publish_draft_pr(
                    parts[1], body["subject_hash"],
                    GitHubDraftPRPublisher(GitHubAPI.from_env()),
                )
                return self._reply(200, result)
            if len(parts) == 4 and parts[0] == "tasks" and parts[2:] == ["draft-pr", "simulate"]:
                if not FAKE_PUBLISHER_MODE:
                    return self._reply(404, {"detail": "not found"})
                result = workflow.publish_draft_pr(
                    parts[1], body["subject_hash"], FakeDraftPRPublisher(),
                )
                return self._reply(200, result)
            if len(parts) == 4 and parts[0] == "tasks" and parts[2:] == ["memories", "facts"]:
                result = workflow.memory.remember_sourced_fact(
                    parts[1], body["statement"], body["source_path"], body["evidence"],
                )
                return self._reply(201, result)
            self._reply(404, {"detail": "not found"})
        except PlanningUnavailable as exc:
            self._reply(503, {"detail": str(exc), "retryable": True})
        except (KeyError, ValueError, RuntimeError, FileExistsError) as exc:
            self._reply(409, {"detail": str(exc)})
        except Exception as exc:
            self._reply(400, {"detail": str(exc)})

    def log_message(self, format, *args):
        return  # audit data is in SQLite; do not add noisy stdout request logs.


def _open_browser(url: str) -> None:
    try:
        if webbrowser.open(url, new=2):
            return
    except Exception:
        pass
    print(f"未能自动打开浏览器；请手动访问 {url}", flush=True)


def main(argv: list[str] | None = None) -> int:
    global FAKE_PUBLISHER_MODE, REAL_PUBLISHER_MODE
    parser = argparse.ArgumentParser(description="Local Issue-to-PR API and UI")
    parser.add_argument("--open", action="store_true", help="Start the service and open the local UI in a browser")
    publisher = parser.add_mutually_exclusive_group()
    publisher.add_argument("--fake-publisher", action="store_true",
                           help="Enable a no-network simulated Draft PR publication button")
    publisher.add_argument("--real-publisher", action="store_true",
                           help="Enable the real commit, push and GitHub Draft PR button")
    args = parser.parse_args(argv)
    FAKE_PUBLISHER_MODE = args.fake_publisher
    REAL_PUBLISHER_MODE = args.real_publisher
    print_startup_checks(startup_checks(ROOT))
    url = "http://127.0.0.1:8765/"
    try:
        server = ThreadingHTTPServer(("127.0.0.1", 8765), Handler)
    except OSError as exc:
        if exc.errno in (errno.EADDRINUSE, 10048) or getattr(exc, "winerror", None) == 10048:
            print("无法启动：127.0.0.1:8765 已被占用。请关闭旧服务或占用该端口的程序后重试。",
                  file=sys.stderr, flush=True)
        else:
            print(f"无法监听 127.0.0.1:8765：{exc}", file=sys.stderr, flush=True)
        return 2
    try:
        execution_manager.recover_interrupted()
        print(f"Issue-to-PR local UI: {url}", flush=True)
        if FAKE_PUBLISHER_MODE:
            print("Fake publisher enabled: simulation only; real publish API disabled", flush=True)
        elif REAL_PUBLISHER_MODE:
            print("Real publisher enabled: the UI can commit, push and create a GitHub Draft PR", flush=True)
        else:
            print("Publisher disabled: use --fake-publisher or --real-publisher explicitly", flush=True)
        if args.open:
            try:
                threading.Thread(target=_open_browser, args=(url,), daemon=True).start()
            except RuntimeError:
                print(f"无法启动浏览器打开任务；请手动访问 {url}", flush=True)
        server.serve_forever()
    except KeyboardInterrupt:
        print("本地服务已停止", flush=True)
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
