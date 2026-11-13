"""Isolated BYOK preview. Run one Uvicorn worker on a dedicated loopback port."""

from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from hashlib import sha256
import asyncio
import json
import os
from pathlib import Path
import re
import secrets
from tempfile import TemporaryDirectory
from threading import BoundedSemaphore, Lock
from time import monotonic
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from starlette.concurrency import run_in_threadpool

from core.sandbox import DockerSandbox
from core.trial import PRESET, TrialModel, execute_preset, preset_plan
from core.trial_invites import valid_invite


WEB = Path(__file__).parent / "web"
MAX_BODY = 4096
PLAN_TTL = 600
MAX_PLANS = 8


@dataclass
class PendingPlan:
    directory: TemporaryDirectory
    workflow: object
    context: object
    plan_hash: str
    remaining_calls: int
    expires_at: float
    invite_id: str | None


class TrialRuns:
    def __init__(self, sandbox, model_factory):
        self.sandbox = sandbox
        self.model_factory = model_factory
        self.slots = BoundedSemaphore(1)
        self.lock = Lock()
        self.pending = {}

    def expire(self, *, all_plans=False):
        with self.lock:
            stale = [key for key, value in self.pending.items() if all_plans or value.expires_at <= monotonic()]
            removed = [self.pending.pop(key) for key in stale]
        for value in removed:
            value.directory.cleanup()

    def plan(self, api_key, invite_id):
        self.expire()
        with self.lock:
            if len(self.pending) >= MAX_PLANS:
                raise HTTPException(429, "待执行计划已满，稍后重试")
            if invite_id and any(plan.invite_id == invite_id for plan in self.pending.values()):
                raise HTTPException(409, "此邀请已有待执行计划，计划十分钟后过期")
        directory = TemporaryDirectory(prefix="issue-to-pr-trial-")
        try:
            model = self.model_factory(api_key)
            try:
                workflow, context, plan, plan_hash = preset_plan(Path(directory.name), model, self.sandbox)
            finally:
                model.close()
            if model.remaining <= 0:
                raise HTTPException(422, "规划已用完本次试用的模型调用额度")
            ticket = secrets.token_urlsafe(32)
            with self.lock:
                self.pending[sha256(ticket.encode()).digest()] = PendingPlan(
                    directory, workflow, context, plan_hash, model.remaining, monotonic() + PLAN_TTL, invite_id,
                )
        except BaseException:
            directory.cleanup()
            raise
        return {"ticket": ticket, "plan": plan, "plan_sha256": plan_hash, "expires_in": PLAN_TTL}

    def execute(self, api_key, ticket, approved_hash, invite_id):
        self.expire()
        token_hash = sha256(ticket.encode()).digest()
        with self.lock:
            value = self.pending.get(token_hash)
            if value is None or value.invite_id != invite_id:
                raise HTTPException(404, "计划不存在、已执行或已过期")
            if not secrets.compare_digest(value.plan_hash, approved_hash):
                raise HTTPException(409, "请批准当前显示的计划")
            del self.pending[token_hash]
        try:
            model = self.model_factory(api_key, remaining=value.remaining_calls)
            try:
                execute_preset(value.workflow, value.context, value.plan_hash, model, self.sandbox)
            finally:
                model.close()
            store = value.workflow.store
            task_id = value.context.task_id
            status = store.task(task_id)["status"]
            review = json.loads(store.latest_artifact(task_id, "review_report")["content"])
            output = {"status": status, "reviewer_passed": review.get("passed"),
                      "tests_exit_code": review.get("tests_exit_code"),
                      "junit": review.get("public_verification", {}).get("junit")}
            if status == "awaiting_diff_approval":
                artifact = store.latest_artifact(task_id, "diff")
                output.update(diff=artifact["content"], diff_sha256=artifact["sha256"])
            return output
        finally:
            value.directory.cleanup()


async def read_input(request, fields):
    if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
        raise HTTPException(415, "请使用 JSON")

    async def collect():
        content = bytearray()
        async for chunk in request.stream():
            if len(content) + len(chunk) > MAX_BODY:
                raise HTTPException(413, "请求过大")
            content.extend(chunk)
        return content

    try:
        data = json.loads(await asyncio.wait_for(collect(), timeout=5))
    except asyncio.TimeoutError:
        raise HTTPException(408, "请求接收超时") from None
    except (ValueError, RecursionError):
        raise HTTPException(400, "无效的 JSON") from None
    if not isinstance(data, dict) or data.keys() != fields:
        raise HTTPException(400, "请求字段无效")
    if not isinstance(data["api_key"], str) or not re.fullmatch(r"[!-~]{8,256}", data["api_key"]):
        raise HTTPException(400, "请提供有效格式的 DeepSeek Key")
    return data


def create_trial_app(origin, *, sandbox=None, model_factory=TrialModel, invite_dir=None):
    parsed = urlsplit(origin)
    if (not parsed.hostname or origin != f"{parsed.scheme}://{parsed.netloc}"
            or parsed.username is not None or parsed.password is not None
            or (parsed.scheme != "https" and not (parsed.scheme == "http" and parsed.hostname == "127.0.0.1"))):
        raise ValueError("TRIAL_ORIGIN 必须为 HTTPS origin，或本机回环 HTTP origin")
    if parsed.scheme == "https" and invite_dir is None:
        raise ValueError("HTTPS 试用须设置 TRIAL_INVITE_DIR")
    if invite_dir is not None:
        invite_dir = Path(invite_dir).resolve(strict=True)
        if not invite_dir.is_dir():
            raise ValueError("TRIAL_INVITE_DIR 必须指向邀请目录")
    runs = TrialRuns(sandbox if sandbox is not None else DockerSandbox(
        image=os.getenv("AGENT_SANDBOX_IMAGE", "issue-to-pr-runner:phase3")), model_factory)

    @asynccontextmanager
    async def lifespan(_):
        async def sweep():
            while True:
                await asyncio.sleep(30)
                await run_in_threadpool(runs.expire)

        cleanup = asyncio.create_task(sweep())
        try:
            yield
        finally:
            cleanup.cancel()
            with suppress(asyncio.CancelledError):
                await cleanup
            await run_in_threadpool(lambda: runs.expire(all_plans=True))

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)

    @app.middleware("http")
    async def boundary(request, call_next):
        if request.headers.get("host", "").lower() != parsed.netloc.lower():
            response = JSONResponse({"detail": "无效的 Host"}, status_code=403)
        elif request.method == "POST" and request.headers.get("origin") != origin:
            response = JSONResponse({"detail": "无效的 Origin"}, status_code=403)
        else:
            response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Content-Security-Policy"] = (
            "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; "
            "frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
        )
        return response

    @app.get("/trial/")
    async def index():
        return FileResponse(WEB / "trial.html", media_type="text/html")

    @app.get("/trial/app.js")
    async def script():
        return FileResponse(WEB / "trial.js", media_type="text/javascript")

    @app.get("/trial/style.css")
    async def css():
        return FileResponse(WEB / "app.css", media_type="text/css")

    def require_invite(request):
        if invite_dir is None:
            return None
        invite_id = valid_invite(invite_dir, request.headers.get("X-Trial-Invite", ""))
        if not invite_id:
            raise HTTPException(403, "请使用有效的邀请链接")
        return invite_id

    @app.get("/trial/access")
    async def access(request: Request):
        require_invite(request)
        return {"access": "granted"}

    async def run_action(action, *args):
        if not runs.slots.acquire(blocking=False):
            raise HTTPException(429, "当前有试用任务在运行，稍后重试")

        def bounded():
            try:
                return action(*args)
            except HTTPException:
                raise
            except Exception:
                raise HTTPException(422, "本次任务未完成，请检查 Key、账户额度或执行环境") from None
            finally:
                runs.slots.release()

        return await run_in_threadpool(bounded)

    @app.post("/trial/plan")
    async def plan(request: Request):
        invite_id = require_invite(request)
        data = await read_input(request, {"api_key", "preset"})
        if data["preset"] != PRESET:
            raise HTTPException(400, "试用只支持预设仓库")
        return await run_action(runs.plan, data["api_key"], invite_id)

    @app.post("/trial/execute")
    async def execute(request: Request):
        invite_id = require_invite(request)
        data = await read_input(request, {"api_key", "ticket", "plan_sha256"})
        ticket, plan_hash = data["ticket"], data["plan_sha256"]
        if (not isinstance(ticket, str) or not re.fullmatch(r"[\w-]{43}", ticket, flags=re.ASCII)
                or not isinstance(plan_hash, str) or not re.fullmatch(r"[a-f0-9]{64}", plan_hash)):
            raise HTTPException(400, "无效的计划凭据")
        return await run_action(runs.execute, data["api_key"], ticket, plan_hash, invite_id)

    return app


app = create_trial_app(os.getenv("TRIAL_ORIGIN", "http://127.0.0.1:8766"),
                       invite_dir=os.getenv("TRIAL_INVITE_DIR") or None)
