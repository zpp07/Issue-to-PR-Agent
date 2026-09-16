"""eval 用例 02 的 pytest 用例（seed 纯净版）。"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pytest
from buggy import mean, variance, median


def test_mean():
    assert mean([1, 2, 3]) == 2
    assert mean([5]) == 5
    with pytest.raises(ValueError):
        mean([])  # 空列表应抛 ValueError，而不是 ZeroDivisionError


def test_variance():
    assert variance([1, 2, 3]) == pytest.approx(2 / 3)


def test_median_odd():
    assert median([3, 1, 2]) == 2


def test_median_even():
    # 偶数个：应取中间两数平均
    assert median([1, 2, 3, 4]) == 2.5


def test_median_does_not_mutate():
    nums = [3, 1, 2]
    median(nums)
    assert nums == [3, 1, 2]  # 不应原地排序污染调用方
