"""Todo-list tool, state-bag edition (4-2): gives the stateless todo from 2-3 persistence.

Exit the repl and start it again — the list comes back exactly as it was.
Why recovery works is explained in the 4-2 chapter text.
"""
from typing import Literal

from pydantic import BaseModel, Field

from flowing import Agent, ScriptTool


class TodoArgs(BaseModel):
    action: Literal["add", "done", "list"] = Field(description="add=add a task; done=complete; list=list")
    title: str | None = Field(default=None, description="task title (required for add)")
    index: int | None = Field(default=None, description="task index (required for done, 1-based)")


class TodoStateTool(ScriptTool):
    """Todo list: add / complete / list, persisted in the Agent state bag (kept across sessions)."""

    name = "todo"
    args_model = TodoArgs

    async def execute(self, *, action: str, title: str | None,
                      index: int | None, caller: Agent) -> dict:
        items: list[dict] = caller.state.get("todo_items") or []
        if action == "add":
            if not title:
                raise ValueError("add requires title")
            items.append({"title": title, "done": False})
        elif action == "done":
            if index is None or not (1 <= index <= len(items)):
                raise ValueError(f"index out of range: {index!r} ({len(items)} items)")
            items[index - 1]["done"] = True
        caller.state["todo_items"] = items   # write-through persistence
        return {"items": items,
                "open": sum(1 for it in items if not it["done"])}
