# 1-7 语义断言：针对留档与工程文件，不调用真实 provider。
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def test_project_files_complete():
    for name in ("main.py", "root.fya", "providers.yaml", "models.yaml",
                 "model-tags.yaml",
                 "tools/run-tests/TOOL.fya", "tools/status-get/TOOL.fya",
                 "tools/order-create/TOOL.fya", "tools/http_demo_server.py"):
        assert (ROOT / name).is_file(), f"缺工程文件: {name}"


def test_cli_declaration_shape():
    decl = read("tools/run-tests/TOOL.fya")
    assert "type: cli" in decl
    assert "command:" in decl and "{{ working_dir }}" in decl, \
        "cli 是 Jinja2 命令模板"
    assert "args:" in decl and "default: sample/" in decl, \
        "schema 默认值派生可选性"


def test_request_declaration_shapes():
    status = read("tools/status-get/TOOL.fya")
    assert "type: request" in status
    assert "method: GET" in status
    order = read("tools/order-create/TOOL.fya")
    assert "method: POST" in order
    assert "output:" in order, "output 声明按 schema 提取字段"


def test_transcripts_present():
    for name in ("repl_input.txt", "repl_output.txt",
                 "repl_input_run2.txt", "repl_output_run2.txt"):
        p = ROOT / name
        assert p.is_file() and p.stat().st_size > 0, f"留档缺失或为空: {name}"


def test_request_tools_called_and_real_data():
    out = read("repl_output.txt")
    assert "status-get" in out and "order-create" in out
    assert '"status": "ok"' in out, "status-get 的服务端响应应被如实引用"
    assert "ord-001" in out and "19.8" in out, \
        "order-create 的落库响应（order_id/total）应回到回合"


def test_nonzero_exit_is_not_error():
    out = read("repl_output_run2.txt")
    assert "run-tests" in out
    assert "[tool:completed]" in out, "退出码 1 仍是 completed，不是 error"
    assert "1 failed, 1 passed" in out, \
        "失败明细（1 failed, 1 passed）应进入回答"
    assert "[tool:error]" not in out, "断言：本次调用不得出现 error 结果"
