"""
通用 agent 循环（三个项目共享）。

和原来最大的区别：
1. 把 tool 执行从循环里抽出来，交给传入的 execute_tool / 或默认 core.tools.execute_tool
2. 全程累计 token 用量（Usage）
3. 结束信号统一为「终态工具」：finish / review_finish 或自由文本兜底
4. 支持自定义 provider / model（默认 DeepSeek deepseek-chat）

Phase 1 新增可靠性：
5. LLM 调用异常分类（限流/超时/网络/鉴权/参数错误），不再无声崩掉
6. 工具输出截断，防止超大输出塞爆上下文
7. token / step / 成本三重预算上限
8. 结构化日志 + request_id（可观测）
"""
import json
import logging
import uuid

try:
    import openai
except ImportError:
    openai = None

from core.client import LLMCallError
from core.usage import Usage
from core.tools import execute_tool as default_execute_tool

log = logging.getLogger("core.agent")

# 喂回模型的工具输出上限：超过则截断，防止一个 run_command 的输出塞爆上下文
MAX_TOOL_OUTPUT = 2000


def _llm_call(client, messages, tools, model):
    """一次 LLM 调用，带异常分类与日志。

    返回 (message, (prompt_tokens, completion_tokens))。
    抛出的都是 LLMCallError（带 retryable 标记），便于上层决定是否整体重跑。
    """
    request_id = uuid.uuid4().hex[:8]
    log.info("LLM 调用 request_id=%s model=%s 工具数=%s",
             request_id, model, len(tools))
    try:
        resp = client.chat.completions.create(
            model=model, messages=messages, tools=tools
        )
    except LLMCallError:
        raise
    except Exception as e:
        # Supports both the official SDK and core.client's no-dependency fallback.
        status = getattr(e, "status_code", None)
        name = type(e).__name__
        retryable = status in {408, 409, 429} or (isinstance(status, int) and status >= 500)
        retryable = retryable or name in {"APITimeoutError", "APIConnectionError", "InternalServerError"}
        if name in {"AuthenticationError", "PermissionDeniedError", "BadRequestError", "NotFoundError"}:
            retryable = False
        raise LLMCallError(f"LLM 调用失败：{e}", retryable=retryable, cause=e) from e

    usage = getattr(resp, "usage", None)
    used = (0, 0)
    if usage:
        used = (usage.prompt_tokens, usage.completion_tokens)
    log.info("LLM 完成 request_id=%s prompt=%s completion=%s",
             request_id, used[0], used[1])
    provider_request_id = getattr(resp, "_request_id", None)
    return resp.choices[0].message, used, request_id, provider_request_id


def run_agent(
    client,
    system_prompt,
    tools,
    task,
    model="deepseek-chat",
    max_steps=15,
    execute_tool=None,
    usage=None,
    trace=None,
    max_tokens=None,
    max_cost_rmb=None,
):
    """
    通用 agent 循环。

    参数：
        client       OpenAI 兼容客户端
        system_prompt 系统提示词
        tools        ToolRegistry（注册了该角色可用工具）
        task         用户任务
        model        模型名
        max_steps    最大步数
        execute_tool 工具执行函数（默认 core.tools.execute_tool，可传入自定义）
        usage        Usage 实例（外部传入可跨 agent 累计）
        trace        list（外部传入，记录每步动作，用于观测）
        max_tokens   token 预算上限（可选，超过则停止）
        max_cost_rmb 成本预算上限（元，可选，超过则停止）

    返回：(final_result, usage)
        final_result：终态工具的参数 dict 或自由文本字符串
    """
    if execute_tool is None:
        execute_tool = default_execute_tool
    if usage is None:
        usage = Usage()

    run_id = uuid.uuid4().hex[:8]
    log.info("run_agent 开始 run_id=%s model=%s max_steps=%s max_tokens=%s",
             run_id, model, max_steps, max_tokens)

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": task},
    ]

    for step in range(max_steps):
        try:
            message, (pt, ct), request_id, provider_request_id = _llm_call(
                client, messages, tools.schemas(), model
            )
        except LLMCallError as exc:
            log.error("LLM 调用失败 run_id=%s retryable=%s: %s", run_id, exc.retryable, exc)
            if trace is not None:
                trace.append({"step": step, "action": "llm_error", "retryable": exc.retryable,
                              "error": str(exc)})
            return {"result": f"LLM 调用失败：{exc}", "retryable": exc.retryable}, usage
        usage.add(pt, ct)

        # 预算上限检查：token / 成本
        if max_tokens is not None and usage.total_tokens > max_tokens:
            log.warning("token 预算耗尽 run_id=%s total=%s > %s",
                        run_id, usage.total_tokens, max_tokens)
            return {"result": f"达到 token 预算上限({max_tokens})，未完成"}, usage
        if max_cost_rmb is not None and usage.cost_rmb() > max_cost_rmb:
            log.warning("成本预算耗尽 run_id=%s cost=%.4f > %s",
                        run_id, usage.cost_rmb(), max_cost_rmb)
            return {"result": f"达到成本预算上限({max_cost_rmb} 元)，未完成"}, usage

        tool_calls = getattr(message, "tool_calls", None)
        if not tool_calls:
            # 兜底：自由文本输出当作完成
            if trace is not None:
                trace.append({"step": step, "action": "text", "content": getattr(message, "content", None),
                              "request_id": request_id, "provider_request_id": provider_request_id})
            return getattr(message, "content", None), usage

        messages.append(message)
        for tool_call in tool_calls:
            name = tool_call.function.name
            try:
                args = json.loads(tool_call.function.arguments)
            except json.JSONDecodeError as e:
                args = {"result": f"参数解析失败：{e}"}
            if trace is not None:
                trace.append({"step": step, "action": name, "args": args,
                              "request_id": request_id, "provider_request_id": provider_request_id})

            try:
                result = execute_tool(name, args)
            except Exception as e:
                result = f"工具执行出错：{e}"

            # 终态工具：finish / review_finish / plan_finish —— 立即返回结构化结果
            if name in ("finish", "review_finish", "plan_finish"):
                final = args.get("result") if name == "finish" else args
                # finish 的 result 直接是字符串；若是 dict 也原样返回
                return final, usage

            # 工具输出截断：防止超大输出塞爆上下文
            content = str(result)
            if len(content) > MAX_TOOL_OUTPUT:
                total = len(content)
                content = content[:MAX_TOOL_OUTPUT] + f"\n...[已截断，共 {total} 字符]"

            messages.append({
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": content,
            })

    log.warning("达到最大步数 run_id=%s max_steps=%s", run_id, max_steps)
    return {"result": "达到最大步数，未完成"}, usage
