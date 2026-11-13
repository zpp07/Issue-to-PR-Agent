"""One bundled repository and a bounded, task-scoped model client for trial runs."""

from pathlib import Path
from time import monotonic
from types import SimpleNamespace

from core.audit import TaskStore
from core.candidate import git_run
from core.client import DEFAULT_MODEL, LLMCallError, OpenAI
from core.workflow import IssueToPRWorkflow
from issue_to_pr import IssueToPRService


PRESET = "python-addition"
ISSUE = "buggy.py 的 add(a, b) 执行了减法。请修复为加法，只修改 buggy.py，保留公开测试。"
TARGET = "buggy.py"


def create_preset(root: Path) -> Path:
    repo = root / "repo"
    repo.mkdir()
    git_run(repo, ["init", "-b", "main"])
    (repo / TARGET).write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
    (repo / "test_buggy.py").write_text(
        "from buggy import add\n\ndef test_add():\n    assert add(2, 3) == 5\n", encoding="utf-8",
    )
    git_run(repo, ["add", "--", TARGET, "test_buggy.py"])
    git_run(repo, ["-c", "user.name=Issue-to-PR Demo", "-c", "user.email=demo@example.invalid",
                   "-c", "commit.gpgsign=false", "-c", "core.hooksPath=.git/hooks-disabled",
                   "commit", "-m", "Add public demo fixture"])
    return repo


class TrialModel:
    def __init__(self, key: str, remaining: int = 16):
        import httpx

        if not key or OpenAI is None:
            raise ValueError("试用需要本次请求提供的 DeepSeek Key")
        self.remaining = remaining
        self.deadline = monotonic() + 100
        self.client = OpenAI(api_key=key, base_url="https://api.deepseek.com", timeout=12,
                             max_retries=0, http_client=httpx.Client(trust_env=False, timeout=12))
        self.chat = SimpleNamespace(completions=self)

    def create(self, **kwargs):
        if self.remaining <= 0 or monotonic() >= self.deadline:
            raise LLMCallError("试用已达到模型调用上限")
        self.remaining -= 1
        try:
            return self.client.chat.completions.create(**kwargs, max_tokens=2048)
        except Exception:
            raise LLMCallError("模型调用失败，请检查 Key、账户额度和连接") from None

    def close(self):
        self.client.close()


def preset_plan(root: Path, client, sandbox):
    sandbox.require_available()
    repo = create_preset(root)
    workflow = IssueToPRWorkflow(TaskStore(root / "tasks.sqlite3"), root / "workspaces")
    agent = IssueToPRService(workflow, client, model=DEFAULT_MODEL, sandbox=sandbox)
    context, plan, plan_hash = agent.create_and_plan(repo, ISSUE)
    if plan["affected_files"] != [TARGET]:
        raise ValueError("试用计划只能修改预设源码文件")
    return workflow, context, plan, plan_hash


def execute_preset(workflow, context, plan_hash, client, sandbox):
    task_id = context.task_id
    workflow.approve_plan(task_id, plan_hash, "trial-user", "approved")
    workflow.store.claim_execution(task_id)
    agent = IssueToPRService(workflow, client, model=DEFAULT_MODEL, sandbox=sandbox)
    return agent.execute_after_plan_approval(task_id, TARGET, max_rounds=1)
