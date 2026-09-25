# 4-4 语义断言：针对留档与工程文件，不调用真实 provider。
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def test_project_files_complete():
    for name in ("main.py", "root.fya", "providers.yaml", "models.yaml",
                 "model-tags.yaml", "tools/make_payment.py", "tools/probe.py",
                 "agents/cashier/agent.fya", "agents/scorer/agent.fya",
                 "demo_entry.py"):
        assert (ROOT / name).is_file(), f"缺工程文件: {name}"


def test_root_entry_overrides_declared():
    head = read("root.fya").split("---", 1)[0]
    assert "as pay" in head
    assert "currency: USD" in head, "specified 固定值"
    assert "self.inject('user_id')" in head, "注入表达式 → specified"
    assert "as oid" in head, "参数改名"


def test_transcripts_present():
    for name in ("demo_output.txt", "repl_input.txt", "repl_output.txt"):
        p = ROOT / name
        assert p.is_file() and p.stat().st_size > 0, f"留档缺失或为空: {name}"


def test_two_llm_views():
    out = read("demo_output.txt")
    assert "name='pay' params=['amount', 'oid']" in out, \
        "根视图：别名 + 改名 + specified 参数移出 LLM 视图"
    assert "name='make-payment' params=['amount', 'currency', 'order_id', 'user_id']" in out, \
        "cashier 裸视图：规范名 + 全参数"


def test_resolve_priority():
    out = read("demo_output.txt")
    assert ("{'order_id': 'A-1', 'amount': 99.0, 'currency': 'USD', "
            "'user_id': 'u-10086'}") in out, \
        "specified（currency=USD / user_id 注入）覆盖 LLM 传入，别名映射回规范名"


def test_strict_gate():
    out = read("demo_output.txt")
    assert "received undefined parameters: ['hack']" in out, \
        "strict=True：幻觉参数 → LLM 可见 error（不是异常）"


def test_strict_false_gate():
    out = read("demo_output.txt")
    assert "status=completed output={'known': 'k', 'extra_seen': ['extra']}" in out, \
        "strict=False：未定义参数放行并由 execute 自行处置"


def test_finish_structured_payload():
    out = read("demo_output.txt")
    assert "last_result(dict)=" in out and "'score':" in out, "结构化交卷：载荷进 last_result"
    assert "'score'" in out and "'verdict'" in out, \
        "output 覆写的动态字段进 last_result dict"


def test_repl_alias_and_injection():
    out = read("repl_output.txt")
    assert "[tool_call] pay" in out, "LLM 按别名调用"
    assert "currency=USD user_id='u-10086'" in out, \
        "execute 实参：specified 固定值 + 注入值对 LLM 不可见但可达执行层"
