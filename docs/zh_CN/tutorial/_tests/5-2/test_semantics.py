# 5-2 语义断言：针对留档与工程文件，不调用真实 provider。
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def test_project_files_complete():
    for name in ("main.py", "root.fya", "providers.yaml", "models.yaml",
                 "model-tags.yaml", "skills/daily-tip.md",
                 "demo_plugins.py", "notes-late-main.py"):
        assert (ROOT / name).is_file(), f"缺工程文件: {name}"


def test_two_stage_wiring():
    main_py = read("main.py")
    assert "runtime.install(SkillPlugin(), CronPlugin())" in main_py
    pos_install = main_py.index("runtime.install")
    pos_mount = main_py.index("runtime.mount")
    assert pos_install < pos_mount, "阶段一（install）必须在首个 mount 之前"
    root_fya = read("root.fya")
    script = root_fya.split("$script:", 1)[1]
    assert "use_skill(self)" in script and "use_cron(self)" in script, \
        "阶段二：setup() 里的实例级启用"


def test_transcripts_present():
    p = ROOT / "demo_output.txt"
    assert p.is_file() and p.stat().st_size > 0


def test_dependency_cycle_fails():
    out = read("demo_output.txt")
    seg = out.split("== ②")[0]
    assert "p1 ↔ p2 互相依赖 → DependencyError" in seg


def test_late_install_fails():
    out = read("demo_output.txt")
    seg = out.split("== ②")[1].split("== ③")[0]
    assert "迟装 launch 失败: ToolNotFoundError" in seg, \
        "install 迟于 mount：插件注册的工具本体在创建期即不可见"


def test_cron_real_fire():
    out = read("demo_output.txt")
    seg = out.split("== ③")[1].split("== ④")[0]
    assert "on_cron_trigger 触发 1 次" in seg
    assert "该起来活动一下了" in seg
    assert "EVENT 消息进树 1 条" in seg, \
        "cron 到点：钩子触发 + EVENT 消息投递进树"


def test_stage_one_artifacts():
    out = read("demo_output.txt")
    seg = out.split("== ④")[1]
    assert "skill-load 在全局注册表: True" in seg
    assert "prompt 块含 skills catalog: True" in seg
