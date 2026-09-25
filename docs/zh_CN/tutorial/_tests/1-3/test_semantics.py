# 1-3 语义断言：针对留档与工程文件，不调用真实 provider。
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def test_project_files_complete():
    for name in ("main.py", "root.fya", "providers.yaml",
                 "models.yaml", "model-tags.yaml"):
        assert (ROOT / name).is_file(), f"缺工程文件: {name}"


def test_args_declared_two_forms():
    root_fya = read("root.fya")
    assert "user_name: str" in root_fya, "糖写法：裸类型字符串 → 必填参数"
    assert "locale:" in root_fya and "default: zh" in root_fya, \
        "完整写法：JSON Schema 展开 + 默认值"


def test_setup_does_typical_things():
    root_fya = read("root.fya")
    script = root_fya.split("$script:", 1)[1]
    assert "self.user_name = user_name" in script, "① 参数存 self"
    assert "self.ask_count = 0" in script, "③ 计数器初始化"
    assert 'self.provide("locale", locale)' in script, "④ 挂上 provide 链"
    assert 'self.inject("timezone")' in script, "⑤ 取上层 provide 的值"


def test_main_provides_before_mount():
    main_py = read("main.py")
    provide_pos = main_py.index('runtime.provide("timezone"')
    mount_pos = main_py.index("runtime.mount")
    assert provide_pos < mount_pos, "影响装配的值必须在 mount 前 provide"


def test_transcripts_present():
    for name in ("cli_error_output.txt", "repl_input.txt", "repl_output.txt"):
        p = ROOT / name
        assert p.is_file() and p.stat().st_size > 0, f"留档缺失或为空: {name}"


def test_missing_required_arg_fails_fast():
    out = read("cli_error_output.txt")
    assert "user_name" in out, "缺必填参数的错误信息应点名 user_name"


def test_args_reach_setup_and_template():
    out = read("repl_output.txt")
    assert "小红" in out, "user_name 参数应经 setup 存 self、进模板"
    assert "en" in out, "locale 参数（--locale en）应到达 setup"
    assert "Asia/Shanghai" in out, "timezone 应来自 main() 的 provide → inject"
