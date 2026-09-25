# 5-2 semantic assertions: against the recorded transcript and project files; no real provider is called.
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def test_project_files_complete():
    for name in ("main.py", "root.fya", "providers.yaml", "models.yaml",
                 "model-tags.yaml", "skills/daily-tip.md",
                 "demo_plugins.py", "notes-late-main.py"):
        assert (ROOT / name).is_file(), f"missing project file: {name}"


def test_two_stage_wiring():
    main_py = read("main.py")
    assert "runtime.install(SkillPlugin(), CronPlugin())" in main_py
    pos_install = main_py.index("runtime.install")
    pos_mount = main_py.index("runtime.mount")
    assert pos_install < pos_mount, "phase one (install) must precede the first mount"
    root_fya = read("root.fya")
    script = root_fya.split("$script:", 1)[1]
    assert "use_skill(self)" in script and "use_cron(self)" in script, \
        "phase two: instance-level enablement in setup()"


def test_transcripts_present():
    p = ROOT / "demo_output.txt"
    assert p.is_file() and p.stat().st_size > 0


def test_dependency_cycle_fails():
    out = read("demo_output.txt")
    seg = out.split("== ②")[0]
    assert "p1 ↔ p2 depend on each other -> DependencyError" in seg


def test_late_install_fails():
    out = read("demo_output.txt")
    seg = out.split("== ②")[1].split("== ③")[0]
    assert "late-install launch failed: ToolNotFoundError" in seg, \
        "install after mount: the tool body a plugin registers is invisible at creation time"


def test_cron_real_fire():
    out = read("demo_output.txt")
    seg = out.split("== ③")[1].split("== ④")[0]
    assert "on_cron_trigger fired 1 time(s)" in seg
    assert "Time to get up and move around" in seg
    assert "EVENT messages in tree: 1" in seg, \
        "cron trigger on schedule: the hook fires + the EVENT message is delivered into the tree"


def test_stage_one_artifacts():
    out = read("demo_output.txt")
    seg = out.split("== ④")[1]
    assert "skill-load in global registry: True" in seg
    assert "prompt block contains skills catalog: True" in seg
