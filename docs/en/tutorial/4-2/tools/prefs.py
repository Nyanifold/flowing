"""Preference tool (4-2): the tool surface for state-bag reads/writes — values enter the state bag through tools and never enter the LLM context."""
from typing import Literal

from pydantic import BaseModel, Field

from flowing import Agent, ScriptTool


class PrefsArgs(BaseModel):
    action: Literal["remember", "list"] = Field(description="remember=store one preference; list=list all")
    key: str | None = Field(default=None, description="preference name (required for remember)")
    value: str | None = Field(default=None, description="preference value (required for remember)")


class PrefsTool(ScriptTool):
    """Remember / list user preferences (persisted in the Agent state bag, survive restarts)."""

    name = "prefs"
    args_model = PrefsArgs

    async def execute(self, *, action: str, key: str | None,
                      value: str | None, caller: Agent) -> dict:
        prefs = caller.state.get("user_prefs") or {}
        if action == "remember":
            if not key or value is None:
                raise ValueError("remember requires key and value")
            prefs[key] = value
            caller.state["user_prefs"] = prefs   # write-through persistence
            return {"remembered": {key: value}}
        return {"prefs": prefs}
