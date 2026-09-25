"""Mode-switching tool (3-2): toggling prompt_blocks by tag = the idiomatic way to switch modes."""
from typing import Literal

from pydantic import BaseModel, Field

from flowing import Agent, ScriptTool


class SwitchModeArgs(BaseModel):
    mode: Literal["explain", "poem"] = Field(description="Target mode: explain=bullet-point explanation; poem=modern poem")


class SwitchMode(ScriptTool):
    """Switch the answer mode (enable/disable the prompt blocks of the matching tag, and update current_mode)."""

    name = "switch-mode"
    args_model = SwitchModeArgs

    async def execute(self, *, mode: str, caller: Agent) -> dict:
        caller.prompt_blocks.disable_by_tag("mode")     # disable all mode blocks
        caller.prompt_blocks.enable_by_tag(mode)        # enable only the target mode block
        caller.current_mode = mode
        return {"mode": mode, "current_mode": caller.current_mode}
