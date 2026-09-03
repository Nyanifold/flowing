"""阶段 3 builtins 文件/shell 工具测试（W29–W30）：测试清单 66–71。

六工具各至少一条正例 + 一条负例（error 结果而非异常）。直接
``await tool({...})`` 走 ``Tool.__call__`` 真实调度链（注意 ``__call__``
不填默认值——带 default 的参数显式传全，见批次 3 笔记）。
"""

from __future__ import annotations

import os
import shutil

import pytest

from flowing.builtins.tools import (
    BashTool,
    EditTool,
    GlobTool,
    GrepTool,
    ReadTool,
    WriteTool,
)


# ---------------------------------------------------------------------------
# 清单 66：read
# ---------------------------------------------------------------------------

async def test_t66_read_line_window(tmp_path):
    """66：三行文件 read(offset=1, limit=1) → 仅第二行，带行号前缀（0 基口径）。"""
    f = tmp_path / "a.txt"
    f.write_text("第一行\n第二行\n第三行\n", encoding="utf-8")
    result = await ReadTool()({"path": str(f), "cwd": None, "offset": 1, "limit": 1})
    assert result.status == "completed"
    assert result.output == "1\t第二行"


@pytest.mark.parametrize("case", ["dir", "binary", "missing"])
async def test_t66_read_error_forms(tmp_path, case):
    """66 负例：目录 / 二进制 / 不存在 → status="error"（不抛异常）。"""
    if case == "dir":
        target = tmp_path   # 目录
    elif case == "binary":
        target = tmp_path / "bin.dat"
        target.write_bytes(b"\xff\xfe\x00\x01")   # 非 UTF-8
    else:
        target = tmp_path / "ghost.txt"
    result = await ReadTool()({"path": str(target), "cwd": None, "offset": 0, "limit": None})
    assert result.status == "error"


async def test_t66_read_cwd_baseline(tmp_path):
    """66 负例（cwd 基准口径 N-01①）：相对路径无 cwd / cwd 非绝对 → error；
    相对路径 + 绝对 cwd → 正常解析。"""
    (tmp_path / "rel.txt").write_text("内容\n", encoding="utf-8")
    r1 = await ReadTool()({"path": "rel.txt", "cwd": None, "offset": 0, "limit": None})
    assert r1.status == "error"   # 相对路径且无 cwd
    r2 = await ReadTool()({"path": "rel.txt", "cwd": "relative/base", "offset": 0, "limit": None})
    assert r2.status == "error"   # cwd 非绝对
    r3 = await ReadTool()({"path": "rel.txt", "cwd": str(tmp_path), "offset": 0, "limit": None})
    assert r3.status == "completed" and "内容" in r3.output


# ---------------------------------------------------------------------------
# 清单 67：write
# ---------------------------------------------------------------------------

async def test_t67_write_creates_parents_and_overwrites(tmp_path):
    """67：目标与上级目录均不存在 → 上级目录创建、内容精确写入；再写 → 整体覆盖。"""
    target = tmp_path / "deep" / "nested" / "out.txt"
    result = await WriteTool()({"path": str(target), "cwd": None, "content": "第一版\n"})
    assert result.status == "completed"
    assert target.read_text(encoding="utf-8") == "第一版\n"
    result2 = await WriteTool()({"path": str(target), "cwd": None, "content": "第二版"})
    assert result2.status == "completed"
    assert target.read_text(encoding="utf-8") == "第二版"   # 无残留


async def test_t67_write_dir_is_error(tmp_path):
    """67 负例：目标是目录 → error 结果。"""
    result = await WriteTool()({"path": str(tmp_path), "cwd": None, "content": "x"})
    assert result.status == "error"


# ---------------------------------------------------------------------------
# 清单 68：bash
# ---------------------------------------------------------------------------

async def test_t68_bash_echo_and_nonzero(tmp_path):
    """68：`echo hi` → 输出含 hi、exit_code=0；非零退出码照常返回不是 error。"""
    result = await BashTool()({"command": "echo hi", "timeout": 120, "cwd": None})
    assert result.status == "completed"
    assert "hi" in result.output and "exit_code: 0" in result.output
    nonzero = await BashTool()({"command": "exit 3", "timeout": 120, "cwd": None})
    assert nonzero.status == "completed" and "exit_code: 3" in nonzero.output


async def test_t68_bash_timeout_kills_process_group(tmp_path):
    """68：`sleep 999` timeout=1 → 超时 error；进程组被杀——后台孙进程
    预定的延迟写标记文件不应出现（杀不干净则会落盘）。"""
    import asyncio

    marker = tmp_path / "survivor.txt"
    result = await BashTool()({
        # 孙进程（后台子 shell）预定 2 秒后写标记；主进程睡死
        "command": f"(sleep 2; touch {marker}) & sleep 999",
        "timeout": 1, "cwd": None})
    assert result.status == "error" and "timed out" in result.error
    await asyncio.sleep(1.5)   # 越过孙进程预定的写时点（共 2.5s > 2s）
    assert not marker.exists()   # 进程组被整组杀掉，孙进程未能落盘


async def test_t68_bash_relative_cwd_is_error(tmp_path):
    """68 负例：cwd 相对 → error 结果。"""
    result = await BashTool()({"command": "true", "timeout": 5, "cwd": "relative/dir"})
    assert result.status == "error"


# ---------------------------------------------------------------------------
# 清单 69：edit
# ---------------------------------------------------------------------------

async def test_t69_edit_ambiguity_and_replace_all(tmp_path):
    """69：两处 foo：edit 不 replace_all → 歧义 error 且文件不变；
    replace_all=True → 两处全换。"""
    f = tmp_path / "e.txt"
    f.write_text("foo 一\nfoo 二\n", encoding="utf-8")
    tool = EditTool()
    ambiguous = await tool({
        "path": str(f), "cwd": None,
        "old_string": "foo", "new_string": "bar", "replace_all": False})
    assert ambiguous.status == "error" and "ambiguous" in ambiguous.error
    assert f.read_text(encoding="utf-8") == "foo 一\nfoo 二\n"   # 文件不变
    replaced = await tool({
        "path": str(f), "cwd": None,
        "old_string": "foo", "new_string": "bar", "replace_all": True})
    assert replaced.status == "completed"
    assert f.read_text(encoding="utf-8") == "bar 一\nbar 二\n"


async def test_t69_edit_zero_hit_is_error(tmp_path):
    """69 负例：0 次命中 → error 结果。"""
    f = tmp_path / "e.txt"
    f.write_text("内容\n", encoding="utf-8")
    result = await EditTool()({
        "path": str(f), "cwd": None,
        "old_string": "不存在", "new_string": "x", "replace_all": False})
    assert result.status == "error"


# ---------------------------------------------------------------------------
# 清单 70：grep
# ---------------------------------------------------------------------------

async def test_t70_grep_matches_with_line_numbers(tmp_path):
    """70：两处匹配 → 两行带路径与行号。"""
    (tmp_path / "a.txt").write_text("foo 在此\n无关\n", encoding="utf-8")
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "b.txt").write_text("另一处 foo\n", encoding="utf-8")
    result = await GrepTool()({
        "pattern": "foo", "path": str(tmp_path), "cwd": None, "glob": None})
    assert result.status == "completed"
    lines = result.output.splitlines()
    assert len(lines) == 2
    # 每行均为 rg 的 <路径>:<行号>:<内容> 形态
    assert any(line == f"{tmp_path / 'a.txt'}:1:foo 在此" for line in lines)
    assert any(line == f"{sub / 'b.txt'}:1:另一处 foo" for line in lines)


async def test_t70_grep_rg_missing_is_error(tmp_path, monkeypatch):
    """70 负例：rg 不存在（模拟）→ error 结果（提示安装，不做 Python 兜底）。"""
    monkeypatch.setattr(shutil, "which", lambda _cmd: None)
    result = await GrepTool()({
        "pattern": "foo", "path": str(tmp_path), "cwd": None, "glob": None})
    assert result.status == "error" and "ripgrep" in result.error


# ---------------------------------------------------------------------------
# 清单 71：glob
# ---------------------------------------------------------------------------

async def test_t71_glob_recursive_mtime_order(tmp_path):
    """71：a.py 与 sub/b.py 皆列出（绝对路径），mtime 新的在前；只列文件。"""
    (tmp_path / "a.py").write_text("a\n", encoding="utf-8")
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "b.py").write_text("b\n", encoding="utf-8")
    (sub / "c.py").mkdir()   # 同名目录：只列文件不列目录
    # 显式设定 mtime（本环境文件系统时间戳粒度粗，不能依赖真实写入序）
    os.utime(tmp_path / "a.py", ns=(1_000_000_000, 1_000_000_000))
    os.utime(sub / "b.py", ns=(2_000_000_000, 2_000_000_000))   # b.py 更新
    result = await GlobTool()({"pattern": "**/*.py", "path": str(tmp_path), "cwd": None})
    assert result.status == "completed"
    lines = result.output.splitlines()
    assert len(lines) == 2   # c.py 是目录，不列出
    assert lines[0] == str(sub / "b.py")   # mtime 新的在前
    assert lines[1] == str(tmp_path / "a.py")
    assert all(os.path.isabs(line) for line in lines)


async def test_t71_glob_missing_dir_is_error(tmp_path):
    """71 负例：基准目录不存在 → error 结果。"""
    result = await GlobTool()({
        "pattern": "**/*.py", "path": str(tmp_path / "ghost"), "cwd": None})
    assert result.status == "error"
