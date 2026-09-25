# 1-6 语义断言：针对留档与工程文件，不调用真实 provider。
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def test_project_files_complete():
    for name in ("main.py", "root.fya", "providers.yaml", "models.yaml",
                 "model-tags.yaml", "tools/demo/TOOL.fya",
                 "tools/demo_server.py", "demo_env_failfast.py"):
        assert (ROOT / name).is_file(), f"缺工程文件: {name}"


def test_mcp_declaration_shape():
    decl = read("tools/demo/TOOL.fya")
    assert "type: mcp" in decl
    assert "command:" in decl, "本篇用 stdio（command）形态"
    assert "url:" not in decl.split("---")[0], "command 与 url 互斥"


def test_declarative_group_reference():
    root_fya = read("root.fya")
    assert "./tools/demo" in root_fya.split("---")[0], "tools: 声明式引用组声明"
    assert "$script" not in root_fya, "绑定期自动展开，无需手工 setup"


def test_transcripts_present():
    for name in ("repl_input.txt", "repl_output.txt", "demo_env_output.txt"):
        p = ROOT / name
        assert p.is_file() and p.stat().st_size > 0, f"留档缺失或为空: {name}"


def test_synthesized_tool_names_used():
    out = read("repl_output.txt")
    assert "demo--list-prs" in out and "demo--create-issue" in out, \
        "Agent 应按合成名调用两个服务端工具"


def test_mcp_results_returned():
    out = read("repl_output.txt")
    assert "issue_id" in out and "42" in out, "create-issue 的结构化结果应回到回合"
    assert "fix typo" in out, "list-prs 的服务端数据应出现在回答中"


def test_env_template_fail_fast():
    out = read("demo_env_output.txt")
    assert "FormatError" in out and "NO_SUCH_VAR" in out, \
        "缺失环境变量应在装配期 fail fast"
