"""
多 Agent 代码审查团队

Coder Agent（程序员，修代码）+ Reviewer Agent（审查员，找问题）
+ 编排循环（修 → 查 → 改，最多 3 轮）

核心思想：把"修代码"和"查代码"拆成两个独立 agent，各自有专门的
prompt 和工具，通过编排循环协作。
"""
import os
import json
import subprocess
from dotenv import load_dotenv
load_dotenv()
from openai import OpenAI

client = OpenAI(
    api_key=os.environ["DEEPSEEK_API_KEY"],
    base_url="https://api.deepseek.com",
)


# ---------- 基础工具函数 ----------
def read_file(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def write_file(path, content):
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return f"已写入 {path}"


def run_command(cmd):
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    return (r.stdout + r.stderr) or "(无输出)"


# ---------- 工具定义（JSON schema，供所有 agent 共用）----------
TOOL_SCHEMAS = {
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
            "description": "运行 shell 命令并返回输出",
            "parameters": {
                "type": "object",
                "properties": {"cmd": {"type": "string", "description": "要执行的命令"}},
                "required": ["cmd"],
            },
        },
    },
    "final_answer": {
        "type": "function",
        "function": {
            "name": "final_answer",
            "description": "给出最终回答，任务完成时调用",
            "parameters": {
                "type": "object",
                "properties": {"answer": {"type": "string", "description": "最终回答的内容"}},
                "required": ["answer"],
            },
        },
    },
}


def execute_tool(name, args):
    """根据工具名执行对应函数"""
    if name == "read_file":
        return read_file(args["path"])
    elif name == "write_file":
        return write_file(args["path"], args["content"])
    elif name == "run_command":
        return run_command(args["cmd"])
    elif name == "final_answer":
        return args["answer"]
    return f"未知工具：{name}"


# ---------- 通用 agent 循环（关键：一个循环，两个角色复用）----------
def run_agent(system_prompt, tool_names, task, max_steps=15):
    """
    通用 agent 循环。
    tool_names 是该 agent 能用的工具名列表（体现角色分工：不同角色工具不同）。
    返回 final_answer 的文本。
    """
    tools = [TOOL_SCHEMAS[name] for name in tool_names]  # 只给这个角色它能用的工具

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": task},
    ]

    for step in range(max_steps):
        message = client.chat.completions.create(
            model="deepseek-chat", messages=messages, tools=tools
        ).choices[0].message

        if message.tool_calls:
            messages.append(message)
            for tool_call in message.tool_calls:
                name = tool_call.function.name
                args = json.loads(tool_call.function.arguments)
                try:
                    result = execute_tool(name, args)
                except Exception as e:
                    result = f"工具执行出错：{e}"

                if name == "final_answer":
                    return result

                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": str(result),
                })
        else:
            return message.content

    return "达到最大步数"


# ---------- 两个 agent 的角色定义 ----------
CODER_PROMPT = """你是一个程序员，负责修复代码中的 bug。
工作流程：先用 read_file 读代码定位问题，用 write_file 修改，用 run_command 跑测试验证。
修好后调用 final_answer，简要说明你改了哪里。"""

REVIEWER_PROMPT = """你是一个极其挑剔的代码审查员，永远能找出改进空间。
用 read_file 读代码，除了检查 bug 和逻辑错误，还要严格审查：
- 代码风格和可读性（变量命名、注释是否清晰）
- 边界情况（空输入、极值、负数等）
- 健壮性和潜在隐患
- 性能（重复计算、低效写法）

即使代码功能正确，你也要至少指出一处可以改进的地方。
调用 final_answer 时，先说"不通过"，再具体说明问题在哪里、怎么改。
只有当代码真的无可挑剔时，才回答"通过"。"""


# ---------- 编排循环（修 → 查 → 改）----------
def multi_agent_review(task, target_file, max_rounds=3):
    coder_task = task

    for round_num in range(1, max_rounds + 1):
        print(f"\n{'='*20} 第 {round_num} 轮 {'='*20}")

        # Coder 修代码（能读、写、跑命令）
        print("[Coder] 修复代码中...")
        coder_result = run_agent(
            CODER_PROMPT,
            ["read_file", "write_file", "run_command", "final_answer"],
            coder_task,
        )
        print(f"[Coder] 完成：{coder_result[:80]}")

        # Reviewer 审查（只能读，不能改 —— 角色分工！）
        print("\n[Reviewer] 审查中...")
        review = run_agent(
            REVIEWER_PROMPT,
            ["read_file", "final_answer"],
            f"请审查文件 {target_file} 的代码，检查是否有 bug 或问题",
        )
        print(f"[Reviewer] 意见：{review[:80]}")

        # 判断是否通过（注意：要排除"不通过"这个包含"通过"的情况）
        if "不通过" not in review:
            print(f"\n✅ 第 {round_num} 轮审查通过，任务完成！")
            return review

        # 没通过：把审查意见喂回 Coder，进入下一轮
        print(f"\n[编排] 审查未通过，把意见喂回 Coder 继续改...")
        coder_task = f"你上次的修改被审查员指出了以下问题：\n{review}\n\n请根据这些问题重新修改代码。"

    return "达到最大轮数，仍未通过审查"


if __name__ == "__main__":
    multi_agent_review("修复 buggy.py 里的 bug", "buggy.py")
