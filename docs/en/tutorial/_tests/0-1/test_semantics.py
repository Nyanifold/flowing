# 0-1 semantic assertions: against the recorded transcripts and project files; no real provider calls.
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


# ── project file completeness ───────────────────────────────

def test_project_files_complete():
    for name in ("main.py", "root.fya", "providers.yaml",
                 "models.yaml", "model-tags.yaml"):
        assert (ROOT / name).is_file(), f"missing project file: {name}"


def test_main_uses_fixed_agent_id():
    main_py = read("main.py")
    assert 'agent_id="agent-main"' in main_py
    assert "Runtime(persist_dir=" in main_py


def test_model_three_files_wired():
    main_py = read("main.py")
    for stem in ("providers", "models", "model_tags"):
        assert f'set_{stem}("@/' in main_py, f"main.py does not wire in {stem}.yaml"


def test_credential_via_env_template():
    providers = read("providers.yaml")
    assert "{{env.DEEPSEEK_API_KEY}}" in providers, "credentials must be injected via the environment variable template"


def test_root_fya_has_model_tag_and_prompt():
    root_fya = read("root.fya")
    assert "model_tag: default" in root_fya
    assert "$system_prompt:" in root_fya
    assert "{{ user_name }}" in root_fya, "demo 2's name template must be in the system_prompt"


# ── transcript completeness ─────────────────────────────────

def test_transcripts_present():
    for name in ("cli_output.txt",
                 "repl_input.txt", "repl_output.txt",
                 "web_output.txt", "serve_output.txt",
                 "repl_input_run2.txt", "repl_output_run2.txt"):
        p = ROOT / name
        assert p.is_file() and p.stat().st_size > 0, f"transcript missing or empty: {name}"


# ── key semantic points ─────────────────────────────────────

def test_cli_answers_self_intro():
    out = read("cli_output.txt")
    assert "assistant" in out, "the cli one-shot conversation should print the assistant's self-introduction"


def test_repl_session_has_prompt_and_answer():
    out = read("repl_output.txt")
    # replay mode (stdin redirected) does not echo input lines: the prompt and the reply share one line
    assert "(agent-main)>>>" in out, "the REPL transcript should record the prompt"
    assert "assistant" in out, "the REPL reply should complete the self-introduction"


def test_web_endpoint_roundtrip():
    out = read("web_output.txt")
    assert "200" in out.splitlines()[1], "the web frontend page should return HTTP 200"
    assert '"final_text"' in out, "the web message endpoint should return the JSON turn result"
    assert "assistant" in out


def test_serve_endpoint_roundtrip():
    out = read("serve_output.txt")
    assert '"status": "ok"' in out, "the serve /healthz should return a healthy status"
    assert '"final_text"' in out and "assistant" in out


def test_user_name_injection_visible_in_reply():
    out = read("repl_output_run2.txt")
    assert "--user_name" not in out  # the transcript must not contain the command line itself
    # replay mode does not echo input lines: the injected name appears in the thinking summary and the formal reply
    assert "\nAlice\n" in out, "the agent should answer with the injected name Alice (reply on its own line)"
