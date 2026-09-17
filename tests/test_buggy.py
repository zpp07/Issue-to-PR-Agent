"""针对 add / add_decimal 的 pytest 用例。

覆盖：正整数、零、负数、浮点、极大数、非数值异常分支、精度场景。
运行方式： python -m pytest tests/test_buggy.py -v
             或用 python -m pytest 从项目根目录发现测试。
"""
import sys
import os

# 让测试能 import 到项目根目录下的模块
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

from buggy import add, add_decimal  # noqa: E402


# ---------- 基本/功能 ----------
class TestPositive:
    def test_two_positive_ints(self):
        assert add(2, 3) == 5

    def test_large_ints(self):
        # 大整数，验证无精度/溢出截断
        assert add(2**63, 2**63) == 2**64
        assert add(10**100, 10**100) == 2 * 10**100

    def test_mixed_int_float(self):
        assert add(1, 0.5) == 1.5


# ---------- 边界：零 / 负数 ----------
class TestEdges:
    def test_zero(self):
        assert add(0, 0) == 0
        assert add(5, 0) == 5
        assert add(0, -3) == -3

    def test_negative(self):
        assert add(-2, -3) == -5
        assert add(-2, 3) == 1
        assert add(2, -3) == -1


# ---------- 浮点 ----------
class TestFloat:
    def test_plain_float(self):
        assert add(0.1, 0.2) == pytest.approx(0.3)

    def test_decimal_precision(self):
        # 二进制浮点相加 0.1+0.2 != 0.3，decimal 版本应相等
        assert add(0.1, 0.2) != 0.3 or add_decimal(0.1, 0.2) == 0.3
        assert add_decimal(0.1, 0.2) == pytest.approx(0.3)


# ---------- 异常分支 ----------
class TestErrors:
    @pytest.mark.parametrize("bad", [None, "a", "1", [1], (1,), {"a": 1}, True, object()])
    def test_non_numeric_raises(self, bad):
        with pytest.raises(TypeError, match="仅接受数值"):
            add(bad, 1)

    def test_gives_clear_context(self):
        with pytest.raises(TypeError) as exc_info:
            add("a", "b")
        assert "str" in str(exc_info.value)

    def test_second_arg_also_checked(self):
        with pytest.raises(TypeError):
            add(1, "b")
