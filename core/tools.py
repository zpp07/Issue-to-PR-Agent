"""
工具定义与注册表。

三个项目都用同一套工具 schema，但通过 ToolRegistry 按角色只暴露
「这个角色能用的工具」（体现多 agent 的权限分工思想）。

额外提供两个通用工具：
- read_file / write_file / run_command：文件与 shell
- finish：富结构化收尾工具（替代旧的 final_answer 自由文本）
"""
import json
import os
from pathlib import Path
import subprocess

# ---------- Phase 1 可靠性：上限与超时 ----------
MAX_FILE_BYTES = 200 * 1024     # 读/写文件大小上限（200KB），防止超大文件塞爆上下文
MAX_COMMAND_OUTPUT = 4000       # 命令输出截断长度
COMMAND_TIMEOUT = 30            # 命令默认超时（秒），防止挂起命令卡死 agent


# ---------- 基础文件/命令工具 ----------
def read_file(path):
    try:
        size = os.path.getsize(path)
    except OSError as e:
        return f"读取失败：{e}"
    if size > MAX_FILE_BYTES:
        return (f"文件过大（{size} 字节，上限 {MAX_FILE_BYTES}），拒绝整体读取。"
                f"请改用 run_command 的 grep/find 定位后再精读。")
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except (OSError, UnicodeDecodeError) as e:
        return f"读取失败：{e}"


def write_file(path, content):
    encoded = content.encode("utf-8")
    if len(encoded) > MAX_FILE_BYTES:
        return (f"写入内容过大（{len(encoded)} 字节，上限 {MAX_FILE_BYTES}），"
                "拒绝写入。")
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return f"已写入 {path}（{len(content)} 字符）"
    except OSError as e:
        return f"写入失败：{e}"


def run_command(argv, cwd=None, timeout=COMMAND_TIMEOUT):
    """Run an already validated argv command without a shell.

    This is deliberately not a sandbox.  It removes shell parsing from the
    agent-controlled input; callers that execute untrusted repositories still
    need a container boundary.
    """
    if not isinstance(argv, (list, tuple)) or not argv or not all(
        isinstance(part, str) and part for part in argv
    ):
        return "命令执行失败：argv 必须是非空字符串数组"
    try:
        r = subprocess.run(
            list(argv), shell=False, capture_output=True, text=True,
            encoding="utf-8", errors="replace", cwd=cwd, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return f"命令超时（>{timeout}s）已终止：{argv!r}"
    except OSError as e:
        return f"命令执行失败：{e}"
    out = (r.stdout + r.stderr) or "(无输出)"
    if len(out) > MAX_COMMAND_OUTPUT:
        total = len(out)
        out = out[:MAX_COMMAND_OUTPUT] + f"\n...[已截断，共 {total} 字符]"
    return out


# ---------- 工具 schema（openai function calling 格式）----------
TOOL_SCHEMAS = {
    "search_code": {
        "type": "function",
        "function": {
            "name": "search_code",
            "description": "在代码库中进行 AST/文档结构切块后的混合检索，返回带文件与行号的证据",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "错误行为、符号、契约或相关关键词"},
                    "top_k": {"type": "integer", "minimum": 1, "maximum": 10, "default": 5},
                },
                "required": ["query"],
            },
        },
    },
    "read_file": {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "读取指定文件的完整内容",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string", "description": "文件路径"}},
                "required": ["path"],
            },
        },
    },
    "write_file": {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "覆盖写入指定文件",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "文件路径"},
                    "content": {"type": "string", "description": "要写入的完整内容"},
                },
                "required": ["path", "content"],
            },
        },
    },
    "run_command": {
        "type": "function",
        "function": {
            "name": "run_command",
            "description": "运行已批准的 argv 命令并返回输出；不能使用 shell、管道或重定向",
            "parameters": {
                "type": "object",
                "properties": {
                    "argv": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "命令和参数，例如 [\"python\", \"-m\", \"pytest\", \"-q\"]",
                    }
                },
                "required": ["argv"],
            },
        },
    },
    # finish：结构化收尾。不同角色可给不同 schema（见 build_tool_registry 用法）。
    "finish": {
        "type": "function",
        "function": {
            "name": "finish",
            "description": "任务完成，调用此工具给出结构化最终结果",
            "parameters": {
                "type": "object",
                "properties": {
                    "result": {"type": "string", "description": "最终结果或总结"},
                },
                "required": ["result"],
            },
        },
    },
    # Reviewer 专用：结构化审查结论
    "review_finish": {
        "type": "function",
        "function": {
            "name": "review_finish",
            "description": "审查完成，用结构化格式给出结论",
            "parameters": {
                "type": "object",
                "properties": {
                    "passed": {"type": "boolean", "description": "代码是否通过审查"},
                    "issues": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "发现的问题列表；通过则为空数组",
                    },
                },
                "required": ["passed", "issues"],
            },
        },
    },
    "plan_finish": {
        "type": "function",
        "function": {
            "name": "plan_finish",
            "description": "规划完成，提交待人工审批的结构化计划",
            "parameters": {
                "type": "object",
                "properties": {
                    "goal": {"type": "string"},
                    "affected_files": {"type": "array", "items": {"type": "string"}},
                    "steps": {"type": "array", "items": {"type": "string"}},
                    "risks": {"type": "array", "items": {"type": "string"}},
                    "allowed_commands": {
                        "type": "array", "items": {"type": "array", "items": {"type": "string"}}
                    },
                },
                "required": ["goal", "affected_files", "steps", "risks", "allowed_commands"],
            },
        },
    },
}


# ---------- 工具执行器 ----------
def execute_tool(name, args):
    if name == "search_code":
        return "代码检索未绑定工作区"
    elif name == "read_file":
        return read_file(args["path"])
    elif name == "write_file":
        return write_file(args["path"], args["content"])
    elif name == "run_command":
        return run_command(args["argv"])
    elif name == "finish":
        return args["result"]
    elif name == "review_finish":
        # 返回 JSON 字符串，让 agent 循环能结构化解析
        return json.dumps(args, ensure_ascii=False)
    elif name == "plan_finish":
        return json.dumps(args, ensure_ascii=False)
    return f"未知工具：{name}"


def make_executor(workdir, policy=None, approval_callback=None, command_runner=None,
                  writable_files=None, code_search=None):
    """返回绑定在指定工作目录上的工具执行器（无容器沙箱）。

    关键点（也是多 agent 里最重要的一个工程点）：
    1. 相对路径（读/写文件）被解析到 workdir 之下，而不是进程 CWD;
    2. 读/写文件的绝对路径若落在 workdir 之外，直接拒绝——这是防止 agent
       "沙箱逃逸"、污染源文件的主要防线（此前 eval 观察到的真实事故）；
    3. run_command 以 workdir 为初始目录执行。注意：命令内部仍可 `cd`
       逃逸（这是无容器沙箱的固有边界，彻底解决需 docker);残余泄漏由
       eval 的 source_polluted 指标兜底检测。

    边界说明：本方案锁定「文件 API」这一主通道;纯 shell agent 要 100%
    防逃逸必须上容器(如 OpenHands 的 docker runtime),这里不做过度承诺。
    """
    workdir = Path(workdir).resolve()

    def resolve(p):
        candidate = Path(p)
        return candidate if candidate.is_absolute() else workdir / candidate

    def check_inside(p):
        """返回 (绝对路径, 是否在 workdir 内)。"""
        # resolve follows symlinks/reparse points.  A link inside workdir that
        # targets elsewhere must not be treated as an in-workspace file.
        p = resolve(p).resolve(strict=False)
        try:
            common = os.path.commonpath([str(p), str(workdir)])
            inside = os.path.normcase(common) == os.path.normcase(str(workdir))
        except ValueError:
            inside = False  # 跨盘符等情况
        return str(p), inside

    allowed_writes = None
    if writable_files is not None:
        allowed_writes = set()
        for item in writable_files:
            path, inside = check_inside(item)
            if not inside:
                raise ValueError(f"允许写入路径必须位于工作区内：{path}")
            allowed_writes.add(os.path.normcase(path))

    def executor(name, args):
        if name == "search_code":
            if code_search is None:
                return "代码检索未启用"
            from core.search import format_hits
            return format_hits(code_search.search(args["query"], args.get("top_k", 5)))
        elif name == "read_file":
            p, inside = check_inside(args["path"])
            if not inside:
                return f"拒绝读取：路径 {p} 在工作区之外"
            return read_file(p)
        elif name == "write_file":
            p, inside = check_inside(args["path"])
            if not inside:
                return f"拒绝写入：路径 {p} 在工作区之外"
            if allowed_writes is not None and os.path.normcase(p) not in allowed_writes:
                return f"拒绝写入：路径 {p} 不在本任务的写入 allowlist"
            return write_file(p, args["content"])
        elif name == "run_command":
            argv = args.get("argv")
            if policy is not None:
                decision = policy.evaluate(argv)
                if decision.requires_approval:
                    if approval_callback is not None and approval_callback(argv, decision):
                        runner = command_runner or run_command
                        return runner(argv, cwd=str(workdir))
                    return f"命令需要人工审批：{decision.reason}；argv={argv!r}"
                if not decision.allowed:
                    return f"命令被策略拒绝：{decision.reason}；argv={argv!r}"
            runner = command_runner or run_command
            return runner(argv, cwd=str(workdir))
        elif name == "finish":
            return args["result"]
        elif name == "review_finish":
            return json.dumps(args, ensure_ascii=False)
        elif name == "plan_finish":
            return json.dumps(args, ensure_ascii=False)
        return f"未知工具：{name}"

    return executor


class ToolRegistry:
    """按名字筛选该角色可用的工具 schema。"""

    def __init__(self, tool_names):
        self.tool_names = list(tool_names)

    def schemas(self):
        return [TOOL_SCHEMAS[name] for name in self.tool_names]

    def has(self, name):
        return name in self.tool_names


def build_tool_registry(*tool_names):
    return ToolRegistry(tool_names)
