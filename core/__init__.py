"""
core —— Issue-to-PR Agent 的统一核心

从这里导入：
- from core.tools import TOOL_SCHEMAS, ToolRegistry
- from core.agent import run_agent
- from core.usage import Usage

从仓库根目录运行脚本或以 editable mode 安装后可直接导入：
    from core.agent import run_agent
"""
from core.usage import Usage
from core.tools import TOOL_SCHEMAS, build_tool_registry

__all__ = ["run_agent", "Usage", "TOOL_SCHEMAS", "build_tool_registry"]


def __getattr__(name):
    """Avoid importing the optional OpenAI dependency for local policy tests."""
    if name == "run_agent":
        from core.agent import run_agent
        return run_agent
    raise AttributeError(name)
