# 4-9 语义断言：针对留档与工程文件，不调用真实 provider。
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def test_project_files_complete():
    for name in ("main.py", "root.fya", "providers.yaml",
                 "models.yaml", "model-tags.yaml",
                 "demo_parsable.py", "notes/refund.md"):
        assert (ROOT / name).is_file(), f"缺工程文件: {name}"


def test_transcripts_present():
    p = ROOT / "demo_output.txt"
    assert p.is_file() and p.stat().st_size > 0, "留档缺失或为空"


def test_five_forms():
    out = read("demo_output.txt")
    seg = out.split("== ②")[0]
    for t in ("RAW", "FILE_REF", "EXPRESSION", "TEMPLATE", "LITERAL"):
        assert f"type={t}" in seg, f"五形式判定缺 {t}"
    assert "→ 42" in seg, "EXPRESSION 返回原生类型（不字符串化）"
    assert "含 {{ 花括号 }} 的原文" in seg, "RAW 跳过全部渲染"


def test_render_context():
    out = read("demo_output.txt")
    seg = out.split("== ②")[1].split("== ③")[0]
    assert "来自环境变量" in seg, "env 入口"
    assert "→ 60" in seg, "config 入口（agent.timeout 默认 60）"
    assert "→ 'agent-main'" in seg, "self 入口"


def test_resolved_reference():
    out = read("demo_output.txt")
    seg = out.split("== ③")[1].split("== ④")[0]
    assert "政策：退款政策：七天内无理由退款" in seg, ".resolved 拿渲染结果"
    assert "政策：$./notes/refund.md" in seg, "不写 .resolved 只显示模板原文"


def test_reserved_and_missing():
    out = read("demo_output.txt")
    seg = out.split("== ④")[1].split("== ⑥")[0]
    assert "Reserved attribute name occupied: 'env'" in seg
    assert "MissingContextError" in seg
    assert "MissingProvideError" in seg


def test_pending_field_position():
    out = read("demo_output.txt")
    seg = out.split("== ⑥")[1]
    assert "MissingFieldError" in seg
    assert "still PENDING" in seg, "字段位 _ = 必须兑现的承诺（创建管线检查点）"
