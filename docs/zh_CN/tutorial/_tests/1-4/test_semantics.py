# 1-4 语义断言：针对留档与工程文件，不调用真实 provider。
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def test_project_files_complete():
    for name in ("main.py", "root.fya", "providers.yaml",
                 "models.yaml", "model-tags.yaml"):
        assert (ROOT / name).is_file(), f"缺工程文件: {name}"


def test_root_fya_has_guard_and_observer():
    root_fya = read("root.fya")
    script = root_fya.split("$script:", 1)[1]
    # 两种挂法：@on 类上声明 + setup() 内注册
    assert '@on("after_turn")' in script, "观察 handler 用 @on 声明"
    assert "self.hooks.before_tool_call(_guard)" in script, \
        "拦截 handler 在 setup() 内注册"
    assert "raise Intercepted" in script, "拦截出口是硬阻断信号"


def test_setup_signature_matches_args():
    root_fya = read("root.fya")
    head = root_fya.split("---", 1)[0]
    script = root_fya.split("$script:", 1)[1]
    assert "user_name: str" in head
    assert "async def setup(self, user_name: str)" in script, \
        "setup 形参必须与 args 声明一一对应（编译期校验）"


def test_transcripts_present():
    for name in ("repl_input.txt", "repl_output.txt"):
        p = ROOT / name
        assert p.is_file() and p.stat().st_size > 0, f"留档缺失或为空: {name}"


def test_rm_was_blocked():
    out = read("repl_output.txt")
    assert "[tool:blocked]" in out, "rm 命令应被 Intercepted 硬阻断为 blocked"
    assert "不允许删除" in out, "blocked 结果应携带拦截原因（LLM 可见）"


def test_ls_was_allowed():
    out = read("repl_output.txt")
    assert "[tool:completed] bash" in out, "ls 命令应正常放行执行"
    assert "使用说明.md" in out, "ls 的实查输出应进入回答"


def test_after_turn_observed_both_turns():
    out = read("repl_output.txt")
    assert "ask_count=1" in out and "ask_count=2" in out, \
        "after_turn 应在每个回合收尾触发（含被拦截的回合）"


def test_no_file_was_deleted():
    assert (ROOT / "notes" / "路线图.md").exists(), "被拦截后文件必须完好"
