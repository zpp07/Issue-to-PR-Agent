"""
统一创建 LLM 客户端（默认 DeepSeek，可切其它 OpenAI 兼容 provider）。

Phase 1 可靠性：
- timeout / max_retries 显式配置（不再依赖 SDK 隐式默认）
- LLMCallError 分类异常，供上层区分「可重试」与「致命」
- 记录实际加载的 .env 路径，便于排障
"""
import logging
import os
import json
from types import SimpleNamespace
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

try:
    from dotenv import load_dotenv
except ImportError:
    def load_dotenv(path, override=False):
        """Small fallback for local KEY=value .env files when dotenv is absent."""
        try:
            lines = open(path, encoding="utf-8").read().splitlines()
        except OSError:
            return False
        for line in lines:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key, value = key.strip(), value.strip().strip('"').strip("'")
            if key and (override or key not in os.environ):
                os.environ[key] = value
        return True
try:  # The normal path for development and production installs.
    from openai import OpenAI
except ImportError:  # A dependency-free fallback keeps the local demo runnable.
    OpenAI = None

log = logging.getLogger("core.client")


class LLMCallError(RuntimeError):
    """LLM 调用失败的分类异常。

    retryable 标记这次失败是否属于「换个时间重试可能成功」的类别
    （网络抖动、限流、5xx），便于上层决定是否整体重跑。
    """

    def __init__(self, message, retryable=False, cause=None):
        super().__init__(message)
        self.retryable = retryable
        self.cause = cause


def _object(value):
    """Turn a JSON response into the tiny attribute surface run_agent needs."""
    if isinstance(value, dict):
        return SimpleNamespace(**{key: _object(item) for key, item in value.items()})
    if isinstance(value, list):
        return [_object(item) for item in value]
    return value


def _message_dict(message):
    if isinstance(message, dict):
        return message
    result = {"role": getattr(message, "role", "assistant"), "content": getattr(message, "content", None)}
    calls = getattr(message, "tool_calls", None)
    if calls:
        result["tool_calls"] = [
            {"id": call.id, "type": "function", "function": {
                "name": call.function.name, "arguments": call.function.arguments,
            }} for call in calls
        ]
    return result


class _StdlibCompletions:
    def __init__(self, client):
        self.client = client

    def create(self, *, model, messages, tools=None):
        payload = {"model": model, "messages": [_message_dict(item) for item in messages]}
        if tools is not None:
            payload["tools"] = tools
        request = Request(
            self.client.base_url.rstrip("/") + "/chat/completions",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Authorization": f"Bearer {self.client.api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.client.timeout) as response:
                body = json.loads(response.read().decode("utf-8"))
                result = _object(body)
                result._request_id = response.headers.get("x-request-id")
                return result
        except HTTPError as exc:
            raise LLMCallError(f"HTTP {exc.code}: {exc.read().decode('utf-8', 'replace')[:300]}",
                               retryable=exc.code in {408, 409, 429} or exc.code >= 500, cause=exc) from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise LLMCallError(f"网络或超时：{exc}", retryable=True, cause=exc) from exc


class StdlibOpenAI:
    """Minimal OpenAI-compatible client used only when the SDK is unavailable.

    It intentionally has no retry loop: Phase 1's single retry owner is the
    official SDK when installed.  This fallback reports a retryable error to
    the caller instead of silently layering a second policy.
    """
    def __init__(self, api_key, base_url, timeout=60.0, max_retries=0):
        self.api_key, self.base_url, self.timeout, self.max_retries = api_key, base_url, timeout, max_retries
        self.chat = SimpleNamespace(completions=_StdlibCompletions(self))


def _load_env():
    """从 CWD 逐级向上查找并加载 .env（避免 find_dotenv 从 core/ 向上找错目录）。

    返回实际加载到的 .env 路径列表（可能为空），用于日志与排障。
    """
    loaded = []
    d = os.getcwd()
    while True:
        candidate = os.path.join(d, ".env")
        if os.path.exists(candidate):
            load_dotenv(candidate, override=False)
            loaded.append(candidate)
        parent = os.path.dirname(d)
        if parent == d:  # 到达根目录
            break
        d = parent
    return loaded


def make_client(provider="deepseek", api_key=None, base_url=None,
                timeout=60.0, max_retries=2):
    """返回一个 OpenAI 兼容客户端。

    provider 目前支持 'deepseek'（默认）。传入 api_key / base_url 可覆盖。
    优先级：显式参数 > 已存在的环境变量 > .env 文件。

    可靠性参数：
        timeout     单次请求读超时（秒），防止调用永久挂起
        max_retries SDK 内置重试次数（对网络抖动/429/5xx 做指数退避重试）
    """
    loaded = _load_env()

    if provider == "deepseek":
        api_key = api_key or os.environ.get("DEEPSEEK_API_KEY")
        base_url = base_url or "https://api.deepseek.com"
    else:
        raise ValueError(f"未知 provider：{provider}")
    if not api_key:
        raise ValueError(
            "未找到 DEEPSEEK_API_KEY：请设置环境变量，或在项目目录放一个 .env 文件"
        )

    log.info(
        "创建 %s 客户端 base_url=%s timeout=%ss max_retries=%s env_files=%s",
        provider, base_url, timeout, max_retries,
        loaded or "(无 .env，用环境变量)",
    )

    if OpenAI is None:
        log.warning("openai SDK 未安装，使用标准库兼容客户端（无 SDK 自动重试）")
        return StdlibOpenAI(api_key=api_key, base_url=base_url, timeout=timeout, max_retries=0)
    return OpenAI(api_key=api_key, base_url=base_url, timeout=timeout, max_retries=max_retries)
