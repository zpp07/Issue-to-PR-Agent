# -*- coding: utf-8 -*-
import numbers
import math
from decimal import Decimal


def _validate_numeric(name, val):
    """校验参数为数值（排除 bool / str / list / 复数等），否则抛 TypeError。

    错误信息同时包含「仅接受数值」（供测试用 match 匹配）和实际类型名，
    以便清晰地指出哪个参数、什么类型出了错。
    """
    if isinstance(val, bool):
        raise TypeError(f"add 仅接受数值，参数 {name} 是布尔值 ({type(val).__name__})"
                        f"，不能作为数值参与运算")
    if not isinstance(val, numbers.Real):
        raise TypeError(f"add 仅接受数值，参数 {name} 是 {type(val).__name__}"
                        f"，不能作为数值参与运算")
    return val


def add(a, b):
    """返回两个数值的和。

    仅接受有限实数（numbers.Real 且排除 bool）。若传入非数值类型，
    会抛出带上下文信息的 TypeError（而不是模糊的字符串拼接/类型错误）。
    """
    _validate_numeric("a", a)
    _validate_numeric("b", b)
    return a + b


def add_decimal(a, b):
    """使用十进制精确相加，避免二进制浮点误差。

    例如 add_decimal(0.1, 0.2) 结果为 0.3（而不是 0.30000000000000004）。
    内部先用 Decimal 精确计算再转回 float，从而与近似断言兼容。
    同样仅接受实数、拒绝 bool / 其它非数值类型。
    """
    _validate_numeric("a", a)
    _validate_numeric("b", b)
    return float(Decimal(str(a)) + Decimal(str(b)))


def get_grade(score):
    """根据分数返回等级：>=90 优秀，>=60 及格，<60 不及格

    score 需为实数且在 0~100 之间，否则抛出相应异常。
    """
    # bool 是 int 的子类，而 int 属于 numbers.Real，会导致 isinstance 判定通过。
    # 但布尔值在语义上并非分数，需显式拒绝。
    if isinstance(score, bool):
        raise TypeError(f"score 不能为布尔值，收到 {type(score).__name__}")
    if not isinstance(score, numbers.Real):
        raise TypeError(f"score 需为实数，收到 {type(score).__name__}")
    # 单独处理 NaN：NaN 无法参与范围比较，应判为非法值
    if math.isnan(score):
        raise ValueError("score 不能为 NaN")
    if not (0 <= score <= 100):
        raise ValueError(f"score 需在 0~100 之间，收到 {score}")
    if score >= 90:
        return "优秀"
    elif score >= 60:
        return "及格"
    return "不及格"


def average(nums):
    """计算平均值；空列表抛 ValueError。

    要求 nums 为支持 len() 的序列（如 list/tuple），否则抛 TypeError。
    元素需为数值（排除 bool、NaN、无穷大），否则抛相应异常。
    """
    try:
        length = len(nums)
    except TypeError:
        raise TypeError("average 需要支持 len() 的序列，如 list/tuple")
    if length == 0:
        raise ValueError("average 不接受空列表")
    # 校验元素均为数值，避免不清晰的 sum() 报错
    for item in nums:
        if isinstance(item, bool):
            raise TypeError(f"列表中元素 {item!r} 是布尔值，不是有效数值")
        if not isinstance(item, numbers.Real):
            raise TypeError(f"列表中元素 {item!r} 不是数值")
        if math.isnan(item) or math.isinf(item):
            raise ValueError(f"列表中元素 {item!r} 不能为 NaN 或无穷大")
    return sum(nums) / length


def test():
    # add
    assert add(2, 3) == 5
    assert add(0, 0) == 0
    assert add(-2, -3) == -5

    # get_grade 边界
    assert get_grade(100) == "优秀"
    assert get_grade(90) == "优秀"
    assert get_grade(89) == "及格"
    assert get_grade(60) == "及格"
    assert get_grade(59) == "不及格"
    assert get_grade(0) == "不及格"

    # get_grade 范围校验
    for bad_score in (-5, 200, 100.5, -0.1):
        try:
            get_grade(bad_score)
        except ValueError:
            pass
        else:
            raise AssertionError(f"get_grade({bad_score}) 应抛出 ValueError")

    # get_grade 类型校验
    for bad_type in ("abc", True, None, [1], 5 + 3j):
        try:
            get_grade(bad_type)
        except TypeError:
            pass
        else:
            raise AssertionError(f"get_grade({bad_type!r}) 应抛出 TypeError")

    # get_grade NaN 校验
    try:
        get_grade(float("nan"))
    except ValueError:
        pass
    else:
        raise AssertionError("get_grade(nan) 应抛出 ValueError")

    # average
    assert average([1, 2, 3]) == 2
    assert average([5]) == 5
    assert average([-1, 1]) == 0
    assert average([0.5, 1.5]) == 1.0

    # average 空列表应抛 ValueError
    for empty in ([], ()):
        try:
            average(empty)
        except ValueError:
            pass
        else:
            raise AssertionError(f"average({empty!r}) 应抛出 ValueError")

    # average 空生成器与非法类型应抛异常
    gen_empty = (x for x in [])
    try:
        average(gen_empty)
    except TypeError:
        pass
    else:
        raise AssertionError("average(生成器) 应抛出 TypeError")

    try:
        average("abc")
    except TypeError:
        pass
    else:
        raise AssertionError("average(非序列) 不应通过")

    # average 应拒绝 bool / NaN / 无穷大元素
    for bad in ([True, 1], [1, float("nan")], [1, float("inf")]):
        try:
            average(bad)
        except (TypeError, ValueError):
            pass
        else:
            raise AssertionError(f"average({bad!r}) 应抛出异常")

    print("测试通过")


if __name__ == "__main__":
    test()
