# 1-5 语义断言：针对留档与工程文件，不调用真实 provider。
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def test_project_files_complete():
    for name in ("main.py", "root.fya", "providers.yaml", "models.yaml",
                 "model-tags.yaml", "agents/greeter/agent.fya",
                 "demo_boundary.py"):
        assert (ROOT / name).is_file(), f"缺工程文件: {name}"


def test_root_provides_locale():
    root_fya = read("root.fya")
    script = root_fya.split("$script:", 1)[1]
    assert 'self.provide("locale", locale)' in script


def test_greeter_injects_locale():
    greeter = read("agents/greeter/agent.fya")
    assert 'self.inject("locale")' in greeter
    assert "{% if locale == 'zh' %}" in greeter, \
        "问候员模板应按 locale 分支切换语言"


def test_transcripts_present():
    for name in ("repl_input.txt", "repl_output.txt",
                 "repl_input_run2.txt", "repl_output_run2.txt",
                 "demo_output.txt"):
        p = ROOT / name
        assert p.is_file() and p.stat().st_size > 0, f"留档缺失或为空: {name}"


def test_locale_en_greeter_english():
    out = read("repl_output.txt")
    assert "subagent-invoke" in out, "问候任务应经 subagent-invoke 派单"
    assert "Hello" in out, "locale=en 时问候员应使用英文"


def test_locale_zh_greeter_chinese():
    out = read("repl_output_run2.txt")
    assert "你好" in out, "locale=zh（默认）时问候员应使用中文"


def test_missing_provide_error():
    out = read("demo_output.txt")
    assert "MissingProvideError" in out, "inject 未命中应抛 MissingProvideError"
