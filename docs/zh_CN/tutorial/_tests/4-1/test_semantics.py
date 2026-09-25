# 4-1 语义断言：针对留档与工程文件，不调用真实 provider。
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def test_project_files_complete():
    for name in ("main.py", "root.fya", "providers.yaml",
                 "models.yaml", "model-tags.yaml", "demo_crash.py"):
        assert (ROOT / name).is_file(), f"缺工程文件: {name}"


def test_transcripts_present():
    for name in ("repl_input.txt", "repl_output.txt", "dir_output.txt",
                 "crash_output.txt", "crash_wc.txt",
                 "repl_input_run2.txt", "repl_output_run2.txt"):
        p = ROOT / name
        assert p.is_file() and p.stat().st_size > 0, f"留档缺失或为空: {name}"


def test_tree_jsonl_record_stream_layout():
    out = read("dir_output.txt")
    assert '"type": "meta", "format_version": 1' in out, \
        "tree.jsonl 首行恒为元数据记录"
    assert "3 .flowing/agent-main/tree.jsonl" in out, \
        "一轮对话 = meta + user + provider 三行（记录流：一行一条记录）"


def test_meta_identity_keys():
    out = read("dir_output.txt")
    for key in ("agent_type", "parent_agent_id", "created_at", "args"):
        assert key in out, f"身份四键缺 {key}"


def test_crash_simulated():
    out = read("crash_output.txt")
    assert "回合完成 status=completed" in out
    assert "os._exit(9)" in out, "崩溃模拟须明示等价 kill -9"
    assert read("crash_wc.txt").strip().startswith("5 "), \
        "崩溃进程留下的记录已落盘（write-behind 在 ms 窗口内写完）"


def test_recovery_replays_history():
    out = read("repl_output_run2.txt")
    assert "731" in out and "紫" in out, \
        "恢复后应同时记起干净进程的数字与崩溃进程的颜色（重放重建树）"
