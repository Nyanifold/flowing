"""模式切换工具（3-2）：prompt_blocks 按 tag 启停 = 模式切换的正规手法。"""
from typing import Literal

from pydantic import BaseModel, Field

from flowing import Agent, ScriptTool


class SwitchModeArgs(BaseModel):
    mode: Literal["explain", "poem"] = Field(description="目标模式：explain=要点式讲解；poem=现代诗")


class SwitchMode(ScriptTool):
    """切换回答模式（启停对应 tag 的 prompt 块，并更新 current_mode）。"""

    name = "switch-mode"
    args_model = SwitchModeArgs

    async def execute(self, *, mode: str, caller: Agent) -> dict:
        caller.prompt_blocks.disable_by_tag("mode")     # 停用全部模式块
        caller.prompt_blocks.enable_by_tag(mode)        # 只启用目标模式块
        caller.current_mode = {"explain": "讲解", "poem": "诗歌"}[mode]
        return {"mode": mode, "current_mode": caller.current_mode}
