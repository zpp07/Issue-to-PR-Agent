"""
Token 用量与成本统计。

每个 agent 运行结束后都产生一个 Usage，累计 prompt/completion token，
并按 DeepSeek 价目表估算费用（人民币）。
"""


class Usage:
    """累计一次运行（可能跨越多次 API 调用）的 token 用量。"""

    def __init__(self):
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.calls = 0

    def add(self, prompt_tokens, completion_tokens):
        self.prompt_tokens += prompt_tokens
        self.completion_tokens += completion_tokens
        self.calls += 1

    @property
    def total_tokens(self):
        return self.prompt_tokens + self.completion_tokens

    def cost_rmb(self, prompt_price=None, completion_price=None):
        """按每百万 token 价格（元）估算费用。

        默认用 DeepSeek V3（deepseek-chat）官方价：
        输入 2 元 / 百万 token（命中缓存 0.5，这里按未命中保守估计），
        输出 8 元 / 百万 token。价格可能变动，以官网为准。
        """
        prompt_price = prompt_price if prompt_price is not None else 2.0
        completion_price = completion_price if completion_price is not None else 8.0
        return (
            self.prompt_tokens / 1_000_000 * prompt_price
            + self.completion_tokens / 1_000_000 * completion_price
        )

    def __str__(self):
        return (
            f"Usage(calls={self.calls}, prompt={self.prompt_tokens}, "
            f"completion={self.completion_tokens}, total={self.total_tokens}, "
            f"~¥{self.cost_rmb():.4f})"
        )

    def __repr__(self):
        return self.__str__()
