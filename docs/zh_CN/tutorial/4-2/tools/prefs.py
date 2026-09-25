"""偏好工具（4-2）：状态袋读写的工具形态——值经工具进状态袋，不进 LLM 上下文。"""
from typing import Literal

from pydantic import BaseModel, Field

from flowing import Agent, ScriptTool


class PrefsArgs(BaseModel):
    action: Literal["remember", "list"] = Field(description="remember=记住一个偏好；list=列出全部")
    key: str | None = Field(default=None, description="偏好名（remember 时必填）")
    value: str | None = Field(default=None, description="偏好值（remember 时必填）")


class PrefsTool(ScriptTool):
    """记住 / 列出用户偏好（持久化在 Agent 状态袋，重启不丢）。"""

    name = "prefs"
    args_model = PrefsArgs

    async def execute(self, *, action: str, key: str | None,
                      value: str | None, caller: Agent) -> dict:
        prefs = caller.state.get("user_prefs") or {}
        if action == "remember":
            if not key or value is None:
                raise ValueError("remember 需要 key 和 value")
            prefs[key] = value
            caller.state["user_prefs"] = prefs   # 写透落盘
            return {"remembered": {key: value}}
        return {"prefs": prefs}
