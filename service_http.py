"""Dependency-free HTTP façade for the local Issue-to-PR workflow.

Run: python service_http.py
It is intentionally local-only (127.0.0.1) and has no authentication because
the project scope is a single-user local prototype.
"""
from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from core.audit import TaskStore
from core.client import make_client
from core.execution import ExecutionManager
from core.github import GitHubAPI, GitHubDraftPRPublisher
from core.read_views import artifact_content, artifact_list, task_page
from core.sandbox import DockerSandbox
from core.workflow import IssueToPRWorkflow
from issue_to_pr import IssueToPRService, PlanningUnavailable

ROOT = Path(__file__).parent
configured_data_dir = os.environ.get("AGENT_DATA_DIR")
DATA_DIR = Path(configured_data_dir).resolve() if configured_data_dir else (ROOT / ".local").resolve()
DATA_DIR.mkdir(parents=True, exist_ok=True)
store = TaskStore(DATA_DIR / "agent_tasks.sqlite3")
workflow = IssueToPRWorkflow(store, DATA_DIR / "workspaces")


def service():
    sandbox = DockerSandbox(image=os.environ.get("AGENT_SANDBOX_IMAGE", "issue-to-pr-runner:phase3"))
    return IssueToPRService(
        workflow, make_client(), os.environ.get("AGENT_MODEL", "deepseek-chat"), sandbox=sandbox,
    )


execution_manager = ExecutionManager(store, service)


class Handler(BaseHTTPRequestHandler):
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
                result = workflow.publish_draft_pr(
                    parts[1], body["subject_hash"],
                    GitHubDraftPRPublisher(GitHubAPI.from_env()),
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


if __name__ == "__main__":
    execution_manager.recover_interrupted()
    print("Issue-to-PR local API: http://127.0.0.1:8765")
    ThreadingHTTPServer(("127.0.0.1", 8765), Handler).serve_forever()
