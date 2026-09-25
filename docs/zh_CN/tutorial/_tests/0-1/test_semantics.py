# 0-1 语义断言：针对留档与工程文件，不调用真实 provider。
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


# ── 工程文件齐全性 ──────────────────────────────────────────

def test_project_files_complete():
    for name in ("main.py", "root.fya", "providers.yaml",
                 "models.yaml", "model-tags.yaml"):
        assert (ROOT / name).is_file(), f"缺工程文件: {name}"


def test_main_uses_fixed_agent_id():
    main_py = read("main.py")
    assert 'agent_id="agent-main"' in main_py
    assert "Runtime(persist_dir=" in main_py


def test_model_three_files_wired():
    main_py = read("main.py")
    for stem in ("providers", "models", "model_tags"):
        assert f'set_{stem}("@/' in main_py, f"main.py 未接入 {stem}.yaml"


def test_credential_via_env_template():
    providers = read("providers.yaml")
    assert "{{env.DEEPSEEK_API_KEY}}" in providers, "凭证必须经环境变量模板注入"


def test_root_fya_has_model_tag_and_prompt():
    root_fya = read("root.fya")
    assert "model_tag: default" in root_fya
    assert "$system_prompt:" in root_fya
    assert "{{ user_name }}" in root_fya, "演示 2 的名字模板必须在 system_prompt 中"


# ── 留档齐全性 ──────────────────────────────────────────────

def test_transcripts_present():
    for name in ("cli_output.txt",
                 "repl_input.txt", "repl_output.txt",
                 "web_output.txt", "serve_output.txt",
                 "repl_input_run2.txt", "repl_output_run2.txt"):
        p = ROOT / name
        assert p.is_file() and p.stat().st_size > 0, f"留档缺失或为空: {name}"


# ── 语义关键点 ──────────────────────────────────────────────

def test_cli_answers_self_intro():
    out = read("cli_output.txt")
    assert "中文助手" in out, "cli 一次性对话应打印助手的自我介绍"


def test_repl_session_has_prompt_and_answer():
    out = read("repl_output.txt")
    # 回放模式（stdin 重定向）不回显输入行：提示符与回复同行照录
    assert "(agent-main)>>>" in out, "REPL 留档应照录提示符"
    assert "中文助手" in out, "REPL 回答应完成自我介绍"


def test_web_endpoint_roundtrip():
    out = read("web_output.txt")
    assert "200" in out.splitlines()[1], "web 前端页面应返回 HTTP 200"
    assert '"final_text"' in out, "web 的 message 端点应返回 JSON 回合结果"
    assert "中文助手" in out


def test_serve_endpoint_roundtrip():
    out = read("serve_output.txt")
    assert '"status": "ok"' in out, "serve 的 /healthz 应返回健康状态"
    assert '"final_text"' in out and "中文助手" in out


def test_user_name_injection_visible_in_reply():
    out = read("repl_output_run2.txt")
    assert "--user_name" not in out  # 留档不含命令行本身
    # 回放模式不回显输入行：注入的名字出现在思考摘要与正式回复中
    assert "\n小明\n" in out, "Agent 应以注入的名字 小明 作答（独立成行的回复）"
