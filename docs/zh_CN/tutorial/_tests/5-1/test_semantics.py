# 5-1 语义断言：针对留档与工程文件，不调用真实 provider。
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def test_project_files_complete():
    for name in ("main.py", "root.fya", "providers.yaml", "models.yaml",
                 "model-tags.yaml", "tools/payment.py", "tools/audit.py",
                 "agents/greeter/agent.fya", "notes/handwritten.py",
                 "demo_fya.py"):
        assert (ROOT / name).is_file(), f"缺工程文件: {name}"


def test_root_fya_full_fields():
    head = read("root.fya").split("---", 1)[0]
    assert "name: root" in head, "name 一致性断言"
    assert "user_id: str" in head, "args 糖"
    assert "default: zh" in head, "args 完整展开"
    assert "./tools/*.py" in head, "glob 展开"
    assert "as greet" in head, "as 别名"
    assert "subagent-invoke" in head


def test_transcripts_present():
    for name in ("demo_output.txt", "repl_input.txt", "repl_output.txt"):
        p = ROOT / name
        assert p.is_file() and p.stat().st_size > 0, f"留档缺失或为空: {name}"


def test_compiled_equals_handwritten():
    out = read("demo_output.txt")
    seg = out.split("== ②")[0]
    assert seg.count(": True") == 3, \
        "编译产物与手写子类应逐项等价（description/模板/args 键集）"


def test_glob_and_alias_entry():
    out = read("demo_output.txt")
    seg = out.split("== ②")[1].split("== ③")[0]
    assert "tools（glob 展开）: ['audit', 'payment', 'subagent-invoke']" in seg
    assert "specified={'tone': 'casual'}" in seg, "别名条目的 specified 固定值"
    assert "name='greet'" in seg and "无 tone" in seg, \
        "catalog 视图：specified 参数移出 LLM 可见面"


def test_negative_assertions():
    out = read("demo_output.txt")
    seg = out.split("== ③")[1]
    assert "name 一致性断言: NameMismatchError" in seg
    assert "UnknownHookPointError" in seg, "@on 未声明钩子点 → 创建期 fail-fast"


def test_repl_end_to_end():
    out = read("repl_output.txt")
    assert "subagent-invoke" in out and "greet" in out
    assert "[hook] before_tool_call" in out, "$script 的 @on 钩子应触发并打印"
    assert "u-7" in out, "args 经 setup 进模板（{{ user_id }}）"
