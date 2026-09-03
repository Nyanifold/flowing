"""阶段 4 clipboard 测试（W36–W37）：测试清单 T93–T99。

工具经 ``tool({...}, caller=agent)``（``Tool.__call__`` 调度层）直接驱动
（cron 批次同惯例）；错误路径经 ``Tool.__call__`` 包装为
``status="error"`` 的 LLM 可见结果，不抛异常。剪贴板测试统一用
``tmp_path`` 动态生成文件（cut/paste 改写源文件，fixtures 保持只读）。
"""

from __future__ import annotations

import pytest

from flowing.plugins.clipboard import ClipboardPlugin, use_clipboard

from clipboard_support import (
    ClipboardAgent,
    SimpleAgent,
    add_fake_provider,
    make_clipboard_harness,
    make_runtime,
    write_lines,
)


async def _make_agent(runtime, cls=ClipboardAgent):
    return await runtime.create_agent(cls, start_loop=False)


async def test_t93_cut_line_and_char_modes(tmp_path):
    """T93：10 行文件 cut(path, 3, 6) → 缓冲含第 3-5 行、源文件剩 7 行
    （标准剪切语义）；cut(path, "2,0", "4,5") → 按字符区间剪切
    （行 1 起 offset 0 起，end 排除）。"""
    runtime = make_clipboard_harness(tmp_path)
    agent = await _make_agent(runtime)
    try:
        cut = runtime.tool_registry.get("clipboard-cut")
        src = write_lines(tmp_path / "a.txt", 10)
        r = await cut({"path": str(src), "start": 3, "end": 6}, caller=agent)
        assert r.status == "completed", r
        buffer = agent.state.clipboard_buffer
        assert buffer["content"] == "line3\nline4\nline5\n"
        assert buffer["lines"] == 3 and buffer["chars"] == len("line3\nline4\nline5\n")
        assert buffer["origin_path"] == str(src)
        assert src.read_text(encoding="utf-8") == (
            "line1\nline2\n" + "line6\nline7\nline8\nline9\nline10\n")

        # 字符区间形态：line2 offset0 起、line4 offset5 排除（"lineN" 5 字符）
        src2 = write_lines(tmp_path / "b.txt", 10)
        r2 = await cut({"path": str(src2), "start": "2,0", "end": "4,5"},
                       caller=agent)
        assert r2.status == "completed", r2
        assert agent.state.clipboard_buffer["content"] == "line2\nline3\nline4"
        # 源文件删除该字符区间：line4 的换行残留为一个空行
        assert src2.read_text(encoding="utf-8") == (
            "line1\n" + "\n" + "line5\nline6\nline7\nline8\nline9\nline10\n")
    finally:
        await agent.destroy()


async def test_t94_threshold_and_file_output(tmp_path):
    """T94：600 行区段 cut 进剪贴板 → 阈值 error 且源文件不变（先校验后
    落盘）；同内容 output=<文件路径> → 成功（文件输出无阈值），上级目录
    自动创建。"""
    runtime = make_clipboard_harness(tmp_path)
    agent = await _make_agent(runtime)
    try:
        cut = runtime.tool_registry.get("clipboard-cut")
        src = write_lines(tmp_path / "big.txt", 600)
        before = src.read_text(encoding="utf-8")
        r = await cut({"path": str(src), "start": 1, "end": 601}, caller=agent)
        assert r.status == "error" and "clipboard limit" in r.error
        assert src.read_text(encoding="utf-8") == before   # 源文件不变
        assert agent.state.clipboard_buffer is None        # 缓冲未写

        out = tmp_path / "out" / "seg.txt"   # 上级目录不存在 → 自动创建
        r2 = await cut({"path": str(src), "start": 1, "end": 601,
                        "output": str(out)}, caller=agent)
        assert r2.status == "completed", r2
        assert out.read_text(encoding="utf-8") == before   # 文件输出无阈值
        assert src.read_text(encoding="utf-8") == ""       # cut 删段照常

        # 字符阈值（双限的另一限）：小文件 + max_chars=10
        ClipboardAgent.clipboard_kwargs = {"max_chars": 10}
        agent2 = await _make_agent(runtime)
        try:
            small = write_lines(tmp_path / "small.txt", 3)
            r3 = await cut({"path": str(small), "start": 1, "end": 3},
                           caller=agent2)
            assert r3.status == "error" and "clipboard limit" in r3.error
            assert small.read_text(encoding="utf-8") == "line1\nline2\nline3\n"
        finally:
            ClipboardAgent.clipboard_kwargs = {}
            await agent2.destroy()
    finally:
        await agent.destroy()


async def test_t95_copy_keeps_source(tmp_path):
    """T95：10 行文件 copy(path, 3, 6) → 缓冲含第 3-5 行、源文件不变。"""
    runtime = make_clipboard_harness(tmp_path)
    agent = await _make_agent(runtime)
    try:
        copy = runtime.tool_registry.get("clipboard-copy")
        src = write_lines(tmp_path / "a.txt", 10)
        before = src.read_text(encoding="utf-8")
        r = await copy({"path": str(src), "start": 3, "end": 6}, caller=agent)
        assert r.status == "completed", r
        assert agent.state.clipboard_buffer["content"] == "line3\nline4\nline5\n"
        assert src.read_text(encoding="utf-8") == before   # 源文件不变
    finally:
        await agent.destroy()


async def test_t96_paste_once_semantics(tmp_path):
    """T96：缓冲含 3 行、目标 5 行 → paste(pos=3) 内容插入为新第 3-5 行、
    缓冲清空；再次 paste → 「剪贴板为空」error；paste(pos="2,4") →
    插入第 2 行 offset 4 处、行数不变。"""
    runtime = make_clipboard_harness(tmp_path)
    agent = await _make_agent(runtime)
    try:
        copy = runtime.tool_registry.get("clipboard-copy")
        paste = runtime.tool_registry.get("clipboard-paste")
        src = write_lines(tmp_path / "src.txt", 3, prefix="seg")
        await copy({"path": str(src), "start": 1, "end": 4}, caller=agent)
        target = write_lines(tmp_path / "dst.txt", 5)

        r = await paste({"path": str(target), "pos": 3}, caller=agent)
        assert r.status == "completed", r
        assert target.read_text(encoding="utf-8") == (
            "line1\nline2\n" + "seg1\nseg2\nseg3\n" + "line3\nline4\nline5\n")
        assert agent.state.clipboard_buffer is None        # 一次性语义：成功清空

        r2 = await paste({"path": str(target), "pos": 1}, caller=agent)
        assert r2.status == "error" and "clipboard is empty" in r2.error

        # 行内 offset 插入：不改动行结构
        agent.state.clipboard_buffer = {
            "content": "XX", "origin_path": None, "lines": 1, "chars": 2}
        r3 = await paste({"path": str(target), "pos": "2,4"}, caller=agent)
        assert r3.status == "completed", r3
        lines = target.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 8                             # 行数不变
        assert lines[1] == "lineXX2"                       # 第 2 行 offset 4 处
        assert agent.state.clipboard_buffer is None
    finally:
        await agent.destroy()


async def test_t97_error_paths_no_raise(tmp_path):
    """T97：pos 越界粘贴失败 → error 且缓冲保留；相对路径无 cwd /
    cwd 非绝对 / start·end 两形态混用 / 行号或 offset 越界 / 路径是目录
    → 均为 error ToolResult，不抛异常。"""
    runtime = make_clipboard_harness(tmp_path)
    agent = await _make_agent(runtime)
    try:
        cut = runtime.tool_registry.get("clipboard-cut")
        paste = runtime.tool_registry.get("clipboard-paste")
        src = write_lines(tmp_path / "a.txt", 5)
        agent.state.clipboard_buffer = {
            "content": "seg\n", "origin_path": None, "lines": 1, "chars": 4}

        # 粘贴 pos 越界 → error，缓冲保留（LLM 可修正重试）
        r = await paste({"path": str(src), "pos": 99}, caller=agent)
        assert r.status == "error" and "out of range" in r.error
        assert agent.state.clipboard_buffer is not None
        assert src.read_text(encoding="utf-8") == "line1\nline2\nline3\nline4\nline5\n"

        cases = [
            (cut, {"path": "rel.txt", "start": 1, "end": 2}),          # 相对路径无 cwd
            (cut, {"path": str(src), "start": 1, "end": 2,
                   "cwd": "relative/dir"}),                            # cwd 非绝对
            (cut, {"path": str(src), "start": 3, "end": "4,5"}),       # 两形态混用
            (cut, {"path": str(src), "start": 50, "end": 51}),         # 行号越界
            (cut, {"path": str(src), "start": "2,0", "end": "2,99"}),  # offset 超行尾
            (cut, {"path": str(tmp_path), "start": 1, "end": 2}),      # 路径是目录
            (paste, {"path": str(src), "pos": "9,0"}),                 # pos 行号越界
        ]
        for tool, args in cases:
            result = await tool(args, caller=agent)   # 不抛异常
            assert result.status == "error", (args, result)
            assert result.error
        assert agent.state.clipboard_buffer is not None   # 全程未动缓冲
    finally:
        await agent.destroy()


async def test_t98_use_clipboard_state_and_recover(tmp_path):
    """T98：use_clipboard(self) → state.clipboard_buffer is None、
    clipboard_max_lines == 500；写入缓冲后模拟崩溃重放 → 缓冲内容恢复
    （写透落盘）。真 Runtime + 真 recover 管线。"""
    rt1 = make_runtime(tmp_path)
    rt1.use(ClipboardPlugin())
    rt1.register_agent_type("clipboard-agent", ClipboardAgent)
    add_fake_provider(rt1)
    agent = await rt1.create_agent("clipboard-agent")
    assert agent.state.clipboard_buffer is None
    assert agent.clipboard_max_lines == 500
    assert agent.clipboard_max_chars == 10_000
    buffer = {"content": "line3\nline4\n", "origin_path": "/tmp/x.txt",
              "lines": 2, "chars": 12}
    agent.state.clipboard_buffer = buffer
    agent_id = agent.node_id
    await rt1.shutdown()

    rt2 = make_runtime(tmp_path)   # 新 Runtime 同目录 = 进程重启
    rt2.use(ClipboardPlugin())
    rt2.register_agent_type("clipboard-agent", ClipboardAgent)
    add_fake_provider(rt2)
    try:
        recovered = await rt2.recover_agent(agent_id)
        assert recovered.state.clipboard_buffer == buffer   # 写透重放恢复
        assert recovered.clipboard_max_lines == 500         # setup 重跑阈值就位
    finally:
        await rt2.shutdown()


async def test_t99_validation_and_disabled_agent(tmp_path):
    """T99：use_clipboard(agent, max_lines=0) → ValueError 且无注册副作用
    （先校验后注册）；未启用 Agent 调用三件工具 → 「剪贴板未启用」
    error 结果。"""
    runtime = make_clipboard_harness(tmp_path)
    plain = await _make_agent(runtime, cls=SimpleAgent)   # 未 use_clipboard
    try:
        with pytest.raises(ValueError):
            use_clipboard(plain, max_lines=0)
        with pytest.raises(ValueError):
            use_clipboard(plain, max_chars=0)
        assert "clipboard_buffer" not in plain.state      # 无注册副作用
        assert not hasattr(plain, "clipboard_max_lines")
        assert not hasattr(plain, "clipboard_max_chars")

        src = write_lines(tmp_path / "a.txt", 5)
        calls = [
            ("clipboard-cut", {"path": str(src), "start": 1, "end": 2}),
            ("clipboard-copy", {"path": str(src), "start": 1, "end": 2}),
            ("clipboard-paste", {"path": str(src), "pos": 1}),
        ]
        for name, args in calls:
            result = await runtime.tool_registry.get(name)(args, caller=plain)
            assert result.status == "error", (name, result)
            assert "clipboard is not enabled" in result.error
        # 文件形态 output 同样被启用门拦截（三件工具整体要求启用）
        result = await runtime.tool_registry.get("clipboard-cut")(
            {"path": str(src), "start": 1, "end": 2,
             "output": str(tmp_path / "o.txt")}, caller=plain)
        assert result.status == "error" and "clipboard is not enabled" in result.error
    finally:
        await plain.destroy()
