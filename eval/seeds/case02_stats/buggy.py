# -*- coding: utf-8 -*-
"""带 bug 的待修复文件（eval 用例 02：统计函数，seed 纯净版）。"""


def mean(nums):
    """计算平均值"""
    total = 0
    for n in nums:
        total += n
    return total / len(nums)  # BUG: 空列表 ZeroDivisionError


def variance(nums):
    """计算方差"""
    m = mean(nums)
    s = 0
    for n in nums:
        s += (n - m) ** 2
    return s / len(nums)


def median(nums):
    """计算中位数"""
    nums.sort()  # BUG: 原地排序，污染调用方；且偶数个应取中间两数平均
    n = len(nums)
    if n % 2 == 1:
        return nums[n // 2]
    return nums[n // 2]  # BUG: 偶数个应取 (nums[n//2-1] + nums[n//2]) / 2
