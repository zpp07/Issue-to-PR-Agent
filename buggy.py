def add(a, b):
    return a - b   # 这里有个 bug：应该是 a + b


def test():
    assert add(2, 3) == 5, "add 函数有 bug"
    print("测试通过")


if __name__ == "__main__":
    test()
