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
from core.tools import execute_tool as default_execute_tool, normalize_tool_result

log = logging.getLogger("core.agent")

# 喂回模型的工具输出上限：超过则截断，防止一个 run_command 的输出塞爆上下文
MAX_TOOL_OUTPUT = 12000


def _is_executing_pytest(argv):
    """Accept actual pytest execution, not metadata/collection-only probes."""
    if not isinstance(argv, list):
        return False
    direct = bool(argv and str(argv[0]).lower() == "pytest")
    module = bool(
        len(argv) >= 3
        and str(argv[0]).lower() in {"python", "python.exe"}
        and argv[1:3] == ["-m", "pytest"]
    )
    if not (direct or module):
        return False
    non_executing = {"--collect-only", "--co", "--help", "-h", "--version"}
    return not any(str(item).lower() in non_executing for item in argv)


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
    final_step_prompt=None,
    run_state=None,
    no_progress_after=None,
    no_progress_prompt=None,
    auto_finish_after_verified_patch=False,
    final_step_tool_names=None,
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
    if run_state is None:
        run_state = {}

    def set_exit(reason):
        run_state["exit_reason"] = reason

    successful_mutation = False
    successful_mutations = 0
    patch_revision = 0
    verified_revision = None
    intervention = None
    pending_direction = None

    run_id = uuid.uuid4().hex[:8]
    log.info("run_agent 开始 run_id=%s model=%s max_steps=%s max_tokens=%s",
             run_id, model, max_steps, max_tokens)

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": task},
    ]

    run_state["completion_policy"] = {
        "auto_finish_after_verified_patch": bool(auto_finish_after_verified_patch),
        "requires_successful_mutation": True,
        "accepted_verification": "pytest exit code 0 after latest mutation",
    }

    def update_budget_state(step_index):
        run_state["budget_state"] = {
            "max_steps": max_steps,
            "steps_used": step_index + 1,
            "remaining_steps": max(0, max_steps - step_index - 1),
            "max_tokens": max_tokens,
            "tokens_used": usage.total_tokens,
            "remaining_tokens": (
                None if max_tokens is None else max(0, max_tokens - usage.total_tokens)
            ),
            "max_cost_rmb": max_cost_rmb,
            "cost_rmb": usage.cost_rmb(),
        }

    def update_patch_state():
        run_state["patch_state"] = {
            "successful_mutations": successful_mutations,
            "patch_revision": patch_revision,
            "verified_revision": verified_revision,
            "latest_patch_verified": (
                patch_revision > 0 and verified_revision == patch_revision
            ),
        }

    update_patch_state()

    for step in range(max_steps):
        if (no_progress_after is not None and not successful_mutation
                and intervention is None and step >= no_progress_after):
            prompt = no_progress_prompt or (
                "Investigation has made no successful production edit. Call "
                "progress_decision now and choose exactly one next direction."
            )
            messages.append({"role": "user", "content": prompt})
            intervention = {
                "step": step, "action": "no_progress_intervention", "status": "ok",
                "threshold": no_progress_after,
            }
            if trace is not None:
                trace.append(intervention)
            run_state["no_progress"] = intervention
        if final_step_prompt and step == max_steps - 1:
            messages.append({"role": "user", "content": final_step_prompt})
        try:
            visible_tools = tools.schemas()
            if intervention is None:
                visible_tools = [
                    schema for schema in visible_tools
                    if schema.get("function", {}).get("name") != "progress_decision"
                ]
            if final_step_tool_names is not None and step == max_steps - 1:
                allowed_final = set(final_step_tool_names)
                visible_tools = [
                    schema for schema in visible_tools
                    if schema.get("function", {}).get("name") in allowed_final
                ]
                if not visible_tools:
                    raise ValueError("final_step_tool_names did not match any registered tool")
            message, (pt, ct), request_id, provider_request_id = _llm_call(
                client, messages, visible_tools, model
            )
        except LLMCallError as exc:
            log.error("LLM 调用失败 run_id=%s retryable=%s: %s", run_id, exc.retryable, exc)
            if trace is not None:
                trace.append({"step": step, "action": "llm_error", "retryable": exc.retryable,
                              "error": str(exc), "status": "error"})
            set_exit("llm_error")
            return {"result": f"LLM 调用失败：{exc}", "retryable": exc.retryable}, usage
        usage.add(pt, ct)
        update_budget_state(step)

        # 预算上限检查：token / 成本
        if max_tokens is not None and usage.total_tokens > max_tokens:
            log.warning("token 预算耗尽 run_id=%s total=%s > %s",
                        run_id, usage.total_tokens, max_tokens)
            set_exit("max_tokens")
            return {"result": f"达到 token 预算上限({max_tokens})，未完成"}, usage
        if max_cost_rmb is not None and usage.cost_rmb() > max_cost_rmb:
            log.warning("成本预算耗尽 run_id=%s cost=%.4f > %s",
                        run_id, usage.cost_rmb(), max_cost_rmb)
            set_exit("max_cost")
            return {"result": f"达到成本预算上限({max_cost_rmb} 元)，未完成"}, usage

        tool_calls = getattr(message, "tool_calls", None)
        if not tool_calls:
            # 兜底：自由文本输出当作完成
            if trace is not None:
                trace.append({"step": step, "action": "text", "content": getattr(message, "content", None),
                              "request_id": request_id, "provider_request_id": provider_request_id,
                              "status": "ok"})
            if intervention is not None and "choice" not in intervention:
                intervention["choice"] = "abandon"
                intervention["next_action"] = "text"
            set_exit("free_text")
            return getattr(message, "content", None), usage

        messages.append(message)
        for tool_call in tool_calls:
            name = tool_call.function.name
            try:
                args = json.loads(tool_call.function.arguments)
            except json.JSONDecodeError as e:
                args = {"result": f"参数解析失败：{e}"}
            if trace is not None:
                trace_record = {"step": step, "action": name, "args": args,
                                "request_id": request_id,
                                "provider_request_id": provider_request_id}
                trace.append(trace_record)
            else:
                trace_record = None

            rejected_by_intervention = False
            if intervention is not None and name != "progress_decision":
                allowed = {
                    "minimal_edit": {"replace_text", "write_file"},
                    "targeted_read": {"read_file", "read_symbol"},
                }.get(pending_direction)
                if "choice" not in intervention:
                    result = "拒绝操作：请先调用 progress_decision 做结构化选择"
                    rejected_by_intervention = True
                elif allowed is not None and name not in allowed:
                    result = (
                        f"拒绝操作：你选择了 {pending_direction}，下一步只允许 "
                        f"{', '.join(sorted(allowed))}"
                    )
                    rejected_by_intervention = True
                else:
                    try:
                        result = execute_tool(name, args)
                    except Exception as e:
                        result = f"工具执行出错：{e}"
                    pending_direction = None
            else:
                try:
                    result = execute_tool(name, args)
                except Exception as e:
                    result = f"工具执行出错：{e}"

            structured_result = normalize_tool_result(result, name)

            if trace_record is not None:
                rendered = structured_result.content
                trace_record["result_chars"] = len(rendered)
                trace_record["status"] = structured_result.status
                trace_record.update(structured_result.metadata)
                # File/search results may contain source code. Keep only their
                # size; mutation and command outcomes are safe and useful for
                # diagnosing rejected edits or failed verification commands.
                if name not in {"read_file", "search_code"} or rendered.startswith(
                    "代码检索预算已耗尽"
                ):
                    trace_record["result_preview"] = rendered[:300]

            if (intervention is not None and name != "progress_decision"):
                if rejected_by_intervention:
                    intervention.setdefault("rejected_next_actions", []).append(name)
                elif "next_action" not in intervention:
                    intervention["next_action"] = name

            if name == "progress_decision":
                choice = args.get("choice")
                if intervention is None:
                    if trace_record is not None:
                        trace_record["status"] = "rejected"
                        trace_record["result_preview"] = "progress_decision 尚未被无进展检测启用"
                    structured_result = normalize_tool_result(
                        "拒绝决策：progress_decision 尚未被无进展检测启用", name
                    )
                else:
                    intervention["choice"] = choice
                    intervention["reason"] = str(args.get("reason", ""))[:300]
                    if choice in {"minimal_edit", "targeted_read"}:
                        pending_direction = choice
                if intervention is not None and choice in {"abandon", "finish"}:
                    set_exit("no_progress")
                    return {
                        "result": args.get("reason", "no-progress terminal decision"),
                        "no_progress_choice": choice,
                    }, usage

            if structured_result.metadata.get("mutation_applied"):
                successful_mutation = True
                successful_mutations += 1
                patch_revision += 1
                verified_revision = None
                update_patch_state()

            argv = args.get("argv") if name == "run_command" else None
            is_pytest = _is_executing_pytest(argv)
            if (
                is_pytest
                and structured_result.metadata.get("command_exit_code") == 0
                and patch_revision > 0
            ):
                verified_revision = patch_revision
                update_patch_state()
                evidence = {
                    "kind": "post_mutation_pytest",
                    "patch_revision": patch_revision,
                    "argv": argv,
                    "exit_code": 0,
                    "step": step,
                }
                run_state["completion_evidence"] = evidence
                if auto_finish_after_verified_patch:
                    controller_event = {
                        "step": step,
                        "action": "controller_finish",
                        "status": "ok",
                        "reason": "latest_patch_verified",
                        "patch_revision": patch_revision,
                        "remaining_steps": max(0, max_steps - step - 1),
                    }
                    if trace is not None:
                        trace.append(controller_event)
                    set_exit("verified_patch")
                    return {
                        "result": "最新补丁已通过修改后的 pytest，控制器结束运行",
                        "auto_finished": True,
                        "completion_evidence": evidence,
                    }, usage

            # 终态工具：finish / review_finish / plan_finish —— 立即返回结构化结果
            if name in ("finish", "review_finish", "plan_finish"):
                final = args.get("result") if name == "finish" else args
                # finish 的 result 直接是字符串；若是 dict 也原样返回
                set_exit("finish")
                return final, usage

            # 工具输出截断：防止超大输出塞爆上下文
            content = structured_result.content
            if len(content) > MAX_TOOL_OUTPUT:
                total = len(content)
                content = content[:MAX_TOOL_OUTPUT] + f"\n...[已截断，共 {total} 字符]"

            messages.append({
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": content,
            })

    log.warning("达到最大步数 run_id=%s max_steps=%s", run_id, max_steps)
    set_exit("max_steps_unverified_patch" if successful_mutation else "max_steps_no_patch")
    update_patch_state()
    return {"result": "达到最大步数，未完成"}, usage
