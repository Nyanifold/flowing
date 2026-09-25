# 2-2 语义断言：针对留档与工程文件，不调用真实 provider。
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def test_project_files_complete():
    for name in ("main.py", "root.fya", "providers.yaml", "models.yaml",
                 "model-tags.yaml", "agents/assistant/agent.fya"):
        assert (ROOT / name).is_file(), f"缺工程文件: {name}"


def test_orchestrator_prompt_declares_name_resume():
    root_fya = read("root.fya")
    assert 'name 用 "memo"' in root_fya or 'name="memo"' in root_fya
    assert 'resume="memo"' in root_fya


def test_transcripts_present():
    for name in ("repl_input.txt", "repl_output.txt"):
        p = ROOT / name
        assert p.is_file() and p.stat().st_size > 0, f"留档缺失或为空: {name}"


def test_named_creation_then_resume():
    out = read("repl_output.txt")
    assert out.count("[tool_call] subagent-invoke") >= 2, \
        "应先命名唤起、再续接同一实例（两次唤起）"


def test_memory_survives_resume():
    out = read("repl_output.txt")
    # 用户第二次没有重述数字；memo 的实例上下文里才有 42
    assert "42" in out.split("问 memo")[-1], \
        "续接同一实例后，assistant 应复述出此前记住的数字 42"


def test_confirmation_relayed():
    out = read("repl_output.txt")
    assert "记住了" in out, "唤起后编排者应转达 assistant 的确认复述"
