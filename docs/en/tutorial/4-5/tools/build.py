"""Background build tool (4-5): the async generator form — first yield is the
receipt, subsequent yields are progress segments.

One of the three background forms: execute is written as an async generator.
The first yield is the pending receipt (the turn is not blocked); each later
yield is one production segment (delivered as an EVENT message); natural
exhaustion produces no final production (the closing marker is yielded by
the author).
"""
import asyncio

from pydantic import BaseModel

from flowing import ScriptTool


class BuildArgs(BaseModel):
    target: str = "app"


class BuildTool(ScriptTool):
    """Simulates one build: receipt + three progress segments + a completion
    summary (runs in the background without blocking the turn)."""

    name = "build"
    args_model = BuildArgs

    async def execute(self, *, target: str):
        yield {"receipt": f"Build task accepted: {target}"}   # first yield = pending receipt
        for step, label in enumerate(["compile", "package", "verify"], start=1):
            await asyncio.sleep(0.6)                    # long task: runs slowly in the background
            yield f"Progress {step}/3: {label} done"
        yield {"done": True, "artifact": f"dist/{target}.tar"}   # completion summary (author-defined closing segment)
