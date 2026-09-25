def test_ok():
    assert 1 + 1 == 2


def test_fail():
    assert "演示" == "非演示", "刻意失败：演示非零退出码不是 error"
