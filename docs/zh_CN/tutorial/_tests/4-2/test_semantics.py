# 4-2 语义断言：针对留档与工程文件，不调用真实 provider。
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def test_project_files_complete():
    for name in ("main.py", "root.fya", "providers.yaml", "models.yaml",
                 "model-tags.yaml", "tools/prefs.py", "tools/todo.py",
                 "tools/app_info.py"):
        assert (ROOT / name).is_file(), f"缺工程文件: {name}"


def test_tool_file_names_match_identity():
    # 文件通道：文件名（snake→kebab）即身份，须与类 name 一致
    for fname, tool in (("tools/prefs.py", '"prefs"'),
                        ("tools/todo.py", '"todo"'),
                        ("tools/app_info.py", '"app-info"')):
        src = read(fname)
        assert f"name = {tool}" in src, f"{fname} 的 name 须为 {tool}"


def test_setup_registers_state():
    root_fya = read("root.fya")
    script = root_fya.split("$script:", 1)[1]
    for key in ("user_prefs", "todo_items", "visit_count"):
        assert f'self.state.register("{key}"' in script
    assert 'runtime.register_state("app")' in read("main.py")


def test_transcripts_present():
    for name in ("repl_input.txt", "repl_output.txt",
                 "repl_input_run2.txt", "repl_output_run2.txt"):
        p = ROOT / name
        assert p.is_file() and p.stat().st_size > 0, f"留档缺失或为空: {name}"


def test_run1_set_values():
    out = read("repl_output.txt")
    assert "prefs" in out and "todo" in out and "app-info" in out
    assert "深色" in out and "写周报" in out
    assert "第 1 次启动" in out or "启动次数" in out


def test_run2_restored_values():
    out = read("repl_output_run2.txt")
    assert "深色" in out, "偏好应跨会话恢复（状态袋持久值优先）"
    assert "写周报" in out and "买牛奶" in out, "任务清单应原样恢复"
    assert "2" in out, "启动次数应为 2（Runtime 全局袋跨会话累计）"


def test_state_files_persisted():
    state = (ROOT / ".flowing" / "agent-main" / "state.jsonl").read_text(encoding="utf-8")
    assert '"key": "user_prefs"' in state
    assert '"key": "todo_items"' in state
    assert '"visit_count", "value": 2' in state.replace('"', '"') or \
           '"visit_count"' in state
    app = (ROOT / ".flowing" / "app.jsonl").read_text(encoding="utf-8")
    last = [json.loads(ln) for ln in app.splitlines() if ln.startswith('{"op"')][-1]
    assert last["key"] == "boots" and last["value"] >= 2
