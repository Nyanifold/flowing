def test_ok():
    assert 1 + 1 == 2


def test_fail():
    assert "demo" == "not-demo", "Deliberate failure: a non-zero exit code is not an error"
