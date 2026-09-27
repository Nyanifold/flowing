# 1-8 语义断言：针对留档与工程文件，不调用真实 provider。
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def test_project_files_complete():
    for name in ("main.py", "root.fya", "providers.yaml",
                 "models.yaml", "model-tags.yaml"):
        assert (ROOT / name).is_file(), f"缺工程文件: {name}"


def test_main_uses_project_local_persist_dir():
    main_py = read("main.py")
    assert 'Runtime(persist_dir="@/.flowing")' in main_py, \
        "本篇演示“持久化目录落在工程内”"


def test_transcripts_present():
    for name in ("repl_input.txt", "repl_output.txt", "dir_output.txt",
                 "repl_input_run2.txt", "repl_output_run2.txt"):
        p = ROOT / name
        assert p.is_file() and p.stat().st_size > 0, f"留档缺失或为空: {name}"


def test_dir_layout_documented():
    out = read("dir_output.txt")
    for f in (".flowing/core.jsonl",
              ".flowing/agent-main/tree.jsonl",
              ".flowing/agent-main/core.jsonl",
              ".flowing/agent-main/state.jsonl",
              ".flowing/agent-main/meta.json"):
        assert f in out, f"目录观察应列出 {f}"


def test_meta_identity_keys():
    out = read("dir_output.txt")
    block = out.split("$ cat .flowing/agent-main/meta.json")[1]
    block = block.split("$ rm -rf", 1)[0]
    meta = json.loads(block)
    for key in ("agent_type", "parent_agent_id", "created_at", "args"):
        assert key in meta, f"身份四键缺 {key}"
    assert meta["agent_type"] == "@/root.fya"


def test_delete_means_fresh_start():
    out = read("dir_output.txt")
    assert "No such file or directory" in out, "删除后目录应不存在"


def test_run2_has_short_chain_only():
    import re
    out = read("repl_output_run2.txt")
    # /messages 列表项：序号 + 两个空格 + kind（首行与提示符同行）
    items = re.findall(r"(?:^|>>>)\s*([0-9]+)\s{2}(user|provider|tool|system)\b",
                       out, flags=re.M)
    assert [kind for _, kind in items] == ["user", "provider"], \
        "删掉目录重启后，消息链只应含新会话的一问一答（全新开始）"
