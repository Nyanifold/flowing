"""MCP 凭证模板装配期 fail-fast 演示（1-6）。

运行：uv run python demo_env_failfast.py
"""
import asyncio
import pathlib
import tempfile

from flowing.errors import FormatError
from flowing.tool.registry import ToolRegistry

BOGUS = """name: bogus
type: mcp
description: 凭证模板缺失的声明。
command: python
args: ["server.py"]
env:
  API_KEY: "{{ env.NO_SUCH_VAR }}"
"""


async def main() -> None:
    tmp = pathlib.Path(tempfile.mkdtemp())
    (tmp / "bogus").mkdir()
    (tmp / "bogus" / "TOOL.fya").write_text(BOGUS, encoding="utf-8")
    reg = ToolRegistry(project_root=pathlib.Path.cwd())
    try:
        reg.get("./bogus", source_dir=tmp)   # 加载声明即渲染 env 模板
    except FormatError as exc:
        print(f"凭证缺失在装配期 fail fast → FormatError: {exc}")


asyncio.run(main())
