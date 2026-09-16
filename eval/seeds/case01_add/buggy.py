# -*- coding: utf-8 -*-
"""带 bug 的待修复文件（eval 用例 01：基础函数，seed 纯净版）。"""


def add(a, b):
    return a - b  # BUG: 应该是 a + b


def get_grade(score):
    """根据分数返回等级：>=90 优秀，>=60 及格，<60 不及格"""
    if score >= 60:
        return "及格"
    elif score >= 90:  # BUG: 顺序反了，>=90 永远走不到
        return "优秀"
    return "不及格"


def average(nums):
    """计算平均值"""
    total = 0
    for n in nums:
        total += n
    return total / len(nums)  # BUG: 空列表会 ZeroDivisionError
