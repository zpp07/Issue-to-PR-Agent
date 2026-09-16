"""eval 用例 01 的 pytest 用例（seed 纯净版）。"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pytest
from buggy import add, get_grade, average


def test_add():
    assert add(2, 3) == 5
    assert add(-2, -3) == -5
    assert add(0, 0) == 0


def test_add_non_numeric():
    with pytest.raises(TypeError):
        add("a", 1)


def test_get_grade():
    assert get_grade(100) == "优秀"
    assert get_grade(90) == "优秀"
    assert get_grade(89) == "及格"
    assert get_grade(60) == "及格"
    assert get_grade(59) == "不及格"


def test_average():
    assert average([1, 2, 3]) == 2
    assert average([5]) == 5
    with pytest.raises(ValueError):
        average([])
