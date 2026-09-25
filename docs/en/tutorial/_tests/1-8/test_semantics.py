# 1-8 semantic assertions: target the recorded transcripts and project files; no real provider is called.
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def test_project_files_complete():
    for name in ("main.py", "root.fya", "providers.yaml",
                 "models.yaml", "model-tags.yaml"):
        assert (ROOT / name).is_file(), f"missing project file: {name}"


def test_main_uses_project_local_persist_dir():
    main_py = read("main.py")
    assert 'Runtime(persist_dir="@/.flowing")' in main_py, \
        "this chapter demonstrates that the persistence directory lives inside the project"


def test_transcripts_present():
    for name in ("repl_input.txt", "repl_output.txt", "dir_output.txt",
                 "repl_input_run2.txt", "repl_output_run2.txt"):
        p = ROOT / name
        assert p.is_file() and p.stat().st_size > 0, f"recorded transcript missing or empty: {name}"


def test_dir_layout_documented():
    out = read("dir_output.txt")
    for f in (".flowing/core.jsonl",
              ".flowing/agent-main/tree.jsonl",
              ".flowing/agent-main/core.jsonl",
              ".flowing/agent-main/state.jsonl",
              ".flowing/agent-main/meta.json"):
        assert f in out, f"directory observation should list {f}"


def test_meta_identity_keys():
    out = read("dir_output.txt")
    block = out.split("$ cat .flowing/agent-main/meta.json")[1]
    block = block.split("$ rm -rf", 1)[0]
    meta = json.loads(block)
    for key in ("agent_type", "parent_agent_id", "created_at", "args"):
        assert key in meta, f"identity four keys missing {key}"
    assert meta["agent_type"] == "@/root.fya"


def test_delete_means_fresh_start():
    out = read("dir_output.txt")
    assert "No such file or directory" in out, "after deletion the directory should not exist"


def test_run2_has_short_chain_only():
    import re
    out = read("repl_output_run2.txt")
    # /messages list items: index + two spaces + kind (first item shares the prompt line)
    items = re.findall(r"(?:^|>>>)\s*([0-9]+)\s{2}(user|provider|tool|system)\b",
                       out, flags=re.M)
    assert [kind for _, kind in items] == ["user", "provider"], \
        "after deleting the directory and restarting, the message chain should contain only the new session's one question and one answer (fresh start)"
