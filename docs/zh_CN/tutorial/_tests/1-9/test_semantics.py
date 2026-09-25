# 1-9 语义断言：针对留档与工程文件，不调用真实 provider。
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def test_project_files_complete():
    for name in ("main.py", "root.fya", "providers.yaml", "models.yaml",
                 "model-tags.yaml", "skills/polite.md"):
        assert (ROOT / name).is_file(), f"缺工程文件: {name}"


def test_plugin_installed_before_mount():
    main_py = read("main.py")
    assert "runtime.install(SkillPlugin())" in main_py
    assert main_py.index("install(SkillPlugin") < main_py.index("runtime.mount")


def test_skill_load_declared():
    root_fya = read("root.fya")
    head = root_fya.split("---", 1)[0]
    assert "skill-load" in head, "插件 install 只注册本体；Agent 须显式声明才对 LLM 可见"
    assert "./skills/polite.md" in head


def test_transcripts_present():
    for name in ("repl_input.txt", "repl_output.txt",
                 "repl_input_run2.txt", "repl_output_run2.txt"):
        p = ROOT / name
        assert p.is_file() and p.stat().st_size > 0, f"留档缺失或为空: {name}"


def test_skill_load_called_and_content_delivered():
    out = read("repl_output.txt")
    assert "[tool_call] skill-load" in out, "LLM 应调用 skill-load 加载技能"
    assert "4  event" in out, "技能正文应作为 EVENT 消息进树"
    assert "愿阁下今日顺遂" in out, "技能正文（固定句式）应出现在消息链中"


def test_fixed_phrase_used_in_answer():
    out = read("repl_output.txt")
    # EVENT 送达后的回答必须使用正文规定的固定句式
    assert "愿阁下今日顺遂，凡事称心。" in out


def test_system_reminder_every_turn():
    for name in ("repl_output.txt", "repl_output_run2.txt"):
        out = read(name)
        # /messages 摘要截断尾部，断言提醒文本头部（每回合一条）
        assert out.count("[提醒] 当前工作目录") >= 2, \
            f"{name}: 每个回合开头都应出现提醒消息"


def test_prompt_until_steer_rerun():
    out = read("repl_output_run2.txt")
    assert "[steer] 验收条件未满足" in out, \
        "最终回复不含 DONE 时应 steer 续跑（导向消息当轮可见）"
