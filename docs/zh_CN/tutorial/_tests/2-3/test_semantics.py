# 2-3 语义断言：针对留档与工程文件，不调用真实 provider。
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def test_project_files_complete():
    for name in ("main.py", "root.fya", "providers.yaml", "models.yaml",
                 "model-tags.yaml", "tools/todo.py", "tools/todo_impl.py",
                 "tools/todo-fn/TOOL.fya", "tools/shout.py",
                 "demo_channels.py"):
        assert (ROOT / name).is_file(), f"缺工程文件: {name}"


def test_tool_authoring_contract():
    todo = read("tools/todo.py")
    assert 'name = "todo"' in todo, "name 必填（不由类名推断）"
    assert "args_model = TodoArgs" in todo, "args_model 声明即模型"
    assert "async def execute(self, *, tasks_text: str) -> dict" in todo, \
        "execute：零散参数进、普通值出"


def test_root_declares_todo_by_file_path():
    head = read("root.fya").split("---", 1)[0]
    assert "- ./tools/todo.py" in head, "文件实现工具以显式文件路径声明"


def test_transcripts_present():
    for name in ("repl_input.txt", "repl_output.txt", "demo_output.txt"):
        p = ROOT / name
        assert p.is_file() and p.stat().st_size > 0, f"留档缺失或为空: {name}"


def test_todo_parsed_and_counted():
    out = read("repl_output.txt")
    assert "[tool_call] todo" in out
    assert "共 3 项、未结 3 项" in out, \
        "todo 应解析出去空行后的 3 项任务并汇报未结计数"


def test_invalid_input_goes_error_channel():
    out = read("repl_output.txt")
    assert "[tool:error] todo" in out, "空清单应走 error 结果而非异常"
    assert "任务清单为空" in out, "error 原因（LLM 可见）应被如实转告"


def test_three_channels_resolve():
    out = read("demo_output.txt")
    assert "通道A 手写子类" in out and "name='todo'" in out
    assert "通道C callable 指针" in out and "name='todo-fn'" in out
    assert "通道B 打标函数" in out and "name='shout'" in out
    assert "同参 schema: True" in out
