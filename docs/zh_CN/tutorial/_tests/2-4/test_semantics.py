# 2-4 语义断言：针对留档与工程文件，不调用真实 provider。
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def test_project_files_complete():
    for name in ("main.py", "root.fya", "providers.yaml", "models.yaml",
                 "model-tags.yaml", "agents/coder/agent.fya",
                 "tools/todo.py", "demo_fanout.py",
                 "target/pkg/calc.py"):
        assert (ROOT / name).is_file(), f"缺工程文件: {name}"


def test_main_provides_cwd_before_mount():
    main_py = read("main.py")
    assert 'runtime.provide("cwd", cwd)' in main_py
    assert main_py.index('provide("cwd"') < main_py.index("runtime.mount")


def test_root_declares_full_stack():
    head = read("root.fya").split("---", 1)[0]
    assert "subagent-invoke" in head and "./tools/todo.py" in head
    assert "explore-agent" in head and "./agents/coder" in head


def test_coder_declares_builtin_six():
    coder = read("agents/coder/agent.fya")
    for bare in ("- read", "- write", "- edit", "- grep", "- glob", "- bash"):
        assert bare in coder, f"coder 应以裸名声明 {bare}"
    assert "{{ cwd }}" in coder and 'self.inject("cwd")' in coder


def test_transcripts_present():
    for name in ("repl_input.txt", "repl_output.txt",
                 "repl_input_run2.txt", "repl_output_run2.txt",
                 "demo_output.txt"):
        p = ROOT / name
        assert p.is_file() and p.stat().st_size > 0, f"留档缺失或为空: {name}"


def test_run_a_explore_then_todo():
    out = read("repl_output.txt")
    assert out.count("[tool_call] subagent-invoke") >= 1, "应派 explore-agent 摸结构"
    assert "[tool_call] todo" in out, "应用 todo 拆解任务"
    assert "共 6 项" in out, "todo 应产出 6 项任务清单"


def test_run_b_coder_wrote_readme():
    out = read("repl_output_run2.txt")
    assert out.count("[tool_call] subagent-invoke") >= 1, "应派 coder 写 README"
    assert "自检" in out or "复核" in out, "coder 应交卷并自检"
    readme = ROOT / "target" / "README.md"
    assert readme.is_file() and readme.stat().st_size > 500, \
        "README.md 应真实落盘且内容充实"
    content = readme.read_text(encoding="utf-8")
    assert "add" in content and "div" in content and "除数不能为 0" in content, \
        "README 应准确描述 calc.py 的 API"


def test_fanout_parallel_coders():
    out = read("demo_output.txt")
    assert "coder-a: status=completed" in out
    assert "coder-b: status=completed" in out
    assert (ROOT / "target" / "notes" / "adder.md").is_file()
    assert (ROOT / "target" / "notes" / "divider.md").is_file()
