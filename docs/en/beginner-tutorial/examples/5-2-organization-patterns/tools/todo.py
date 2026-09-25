"""Task-list tool (2-3): free-form task text in, structured list out.

Stateless pure-function implementation (the state-bag usage is the subject of
4-2; to be continued there); demonstrates: the args_model declaration is the
model contract, and plain exceptions become LLM-visible error results.
"""
from pydantic import BaseModel, Field

from flowing import ScriptTool


class TodoArgs(BaseModel):
    tasks_text: str = Field(
        description="Task list text: one task per line; a line starting with [x] is marked done")


class TodoTool(ScriptTool):
    """Parses free-form task text into a structured list: parses, validates, drops blank lines, and returns the count of open tasks."""

    name = "todo"
    args_model = TodoArgs

    async def execute(self, *, tasks_text: str) -> dict:
        items = []
        for raw in tasks_text.splitlines():
            line = raw.strip()
            if not line:
                continue                      # drop blank lines
            done = line[:3].lower() == "[x]"
            title = (line[3:] if done else line).strip().lstrip("-•").strip()
            if not title:
                raise ValueError(f"task line is empty (nothing left after removing markers and symbols): {raw!r}")
            items.append({"title": title, "done": done})
        if not items:
            raise ValueError("task list is empty: provide at least one task")   # → error result (visible to the LLM)
        open_count = sum(1 for it in items if not it["done"])
        return {"total": len(items), "open": open_count, "items": items}
