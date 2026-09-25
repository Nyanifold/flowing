"""Task-list tool (2-3): scattered task text in, structured list out.

Stateless and purely functional (the state-bag usage is the topic of 4-2;
this chapter stays stateless);
demonstrates: args_model declaration is the model, and a plain exception
becomes an LLM-visible error result.
"""
from pydantic import BaseModel, Field

from flowing import ScriptTool


class TodoArgs(BaseModel):
    tasks_text: str = Field(
        description="Task-list text: one task per line; a leading [x] marks it completed")


class TodoTool(ScriptTool):
    """Parse scattered task text into a structured list: parse, validate, drop empty lines, and return the open-task count."""

    name = "todo"
    args_model = TodoArgs

    async def execute(self, *, tasks_text: str) -> dict:
        items = []
        for raw in tasks_text.splitlines():
            line = raw.strip()
            if not line:
                continue                      # drop empty lines
            done = line[:3].lower() == "[x]"
            title = (line[3:] if done else line).strip().lstrip("-•").strip()
            if not title:
                raise ValueError(f"task line is empty after removing markers and bullets: {raw!r}")
            items.append({"title": title, "done": done})
        if not items:
            raise ValueError("Task list is empty: provide at least one task")   # → error result (visible to the LLM)
        open_count = sum(1 for it in items if not it["done"])
        return {"total": len(items), "open": open_count, "items": items}
