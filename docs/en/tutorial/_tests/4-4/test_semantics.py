# 4-4 semantic assertions: against the transcripts and project files; no real provider calls.
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def test_project_files_complete():
    for name in ("main.py", "root.fya", "providers.yaml", "models.yaml",
                 "model-tags.yaml", "tools/make_payment.py", "tools/probe.py",
                 "agents/cashier/agent.fya", "agents/scorer/agent.fya",
                 "demo_entry.py"):
        assert (ROOT / name).is_file(), f"missing project file: {name}"


def test_root_entry_overrides_declared():
    head = read("root.fya").split("---", 1)[0]
    assert "as pay" in head
    assert "currency: USD" in head, "specified fixed value"
    assert "self.inject('user_id')" in head, "injection expression → specified"
    assert "as oid" in head, "parameter rename"


def test_transcripts_present():
    for name in ("demo_output.txt", "repl_input.txt", "repl_output.txt"):
        p = ROOT / name
        assert p.is_file() and p.stat().st_size > 0, f"transcript missing or empty: {name}"


def test_two_llm_views():
    out = read("demo_output.txt")
    assert "name='pay' params=['amount', 'oid']" in out, \
        "root view: alias + rename + specified params moved out of the LLM view"
    assert "name='make-payment' params=['amount', 'currency', 'order_id', 'user_id']" in out, \
        "cashier bare view: canonical name + all params"


def test_resolve_priority():
    out = read("demo_output.txt")
    assert ("{'order_id': 'A-1', 'amount': 99.0, 'currency': 'USD', "
            "'user_id': 'u-10086'}") in out, \
        "specified (currency=USD / injected user_id) overrides LLM input; alias mapped back to the canonical name"


def test_strict_gate():
    out = read("demo_output.txt")
    assert "received undefined parameters: ['hack']" in out, \
        "strict=True: hallucinated param → LLM-visible error (not an exception)"


def test_strict_false_gate():
    out = read("demo_output.txt")
    assert "status=completed output={'known': 'k', 'extra_seen': ['extra']}" in out, \
        "strict=False: undefined params pass through and execute handles them"


def test_finish_structured_payload():
    out = read("demo_output.txt")
    assert "last_result(dict)=" in out and "'score':" in out, "structured submission: payload lands in last_result"
    assert "'score'" in out and "'verdict'" in out, \
        "dynamic fields from the output override land in the last_result dict"


def test_repl_alias_and_injection():
    out = read("repl_output.txt")
    assert "[tool_call] pay" in out, "LLM calls by alias"
    assert "currency=USD user_id='u-10086'" in out, \
        "execute args: specified fixed value + injected value are invisible to the LLM but reachable at the execution layer"
