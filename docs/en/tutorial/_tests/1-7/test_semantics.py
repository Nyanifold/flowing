# 1-7 semantic assertions: against the recorded transcripts and project files;
# no real provider is called.
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def test_project_files_complete():
    for name in ("main.py", "root.fya", "providers.yaml", "models.yaml",
                 "model-tags.yaml",
                 "tools/run-tests/TOOL.fya", "tools/status-get/TOOL.fya",
                 "tools/order-create/TOOL.fya", "tools/http_demo_server.py"):
        assert (ROOT / name).is_file(), f"missing project file: {name}"


def test_cli_declaration_shape():
    decl = read("tools/run-tests/TOOL.fya")
    assert "type: cli" in decl
    assert "command:" in decl and "{{ working_dir }}" in decl, \
        "cli is a Jinja2 command template"
    assert "args:" in decl and "default: sample/" in decl, \
        "schema defaults drive optionality"


def test_request_declaration_shapes():
    status = read("tools/status-get/TOOL.fya")
    assert "type: request" in status
    assert "method: GET" in status
    order = read("tools/order-create/TOOL.fya")
    assert "method: POST" in order
    assert "output:" in order, "the output declaration extracts fields per schema"


def test_transcripts_present():
    for name in ("repl_input.txt", "repl_output.txt",
                 "repl_input_run2.txt", "repl_output_run2.txt"):
        p = ROOT / name
        assert p.is_file() and p.stat().st_size > 0, \
            f"transcript missing or empty: {name}"


def test_request_tools_called_and_real_data():
    out = read("repl_output.txt")
    assert "status-get" in out and "order-create" in out
    assert '"status": "ok"' in out, \
        "the status-get server response should be quoted faithfully"
    assert "ord-001" in out and "19.8" in out, \
        "the persisted order-create response (order_id/total) should return to the turn"


def test_nonzero_exit_is_not_error():
    out = read("repl_output_run2.txt")
    assert "run-tests" in out
    assert "[tool:completed]" in out, \
        "exit code 1 is still a completed result, not an error"
    assert "1 failed, 1 passed" in out, \
        "the failure details (1 failed, 1 passed) should appear in the answer"
    assert "[tool:error]" not in out, \
        "assertion: this run must not produce any error result"
