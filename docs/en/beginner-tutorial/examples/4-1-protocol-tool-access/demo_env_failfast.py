"""Demo: MCP credential templates fail fast at assembly time.

Run: uv run python demo_env_failfast.py
"""
import asyncio
import pathlib
import tempfile

from flowing.errors import FormatError
from flowing.tool.registry import ToolRegistry

BOGUS = """name: bogus
type: mcp
description: A declaration whose credential template is missing.
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
        reg.get("./bogus", source_dir=tmp)   # loading the declaration renders the env template
    except FormatError as exc:
        print(f"Missing credentials fail fast at assembly time → FormatError: {exc}")


asyncio.run(main())
