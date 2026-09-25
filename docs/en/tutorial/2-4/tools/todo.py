"""Task list tool (2-3): unstructured task text in, structured list out.

Stateless pure-function implementation (the state-bag usage is the subject of
4-2; to be continued there); demo: args_model declares the model as-is, and a
plain exception becomes an LLM-visible error result.
"""
from pydantic import BaseModel, Field

from flowing import ScriptTool


class TodoArgs(BaseModel):
    tasks_text: str = Field(
        description="Task list text: one task per line; a line starting with [x] marks a completed task")


class TodoTool(ScriptTool):
    """Parse unstructured task text into a structured list: parse, validate, drop blank lines, and return the open-task count."""

    name = "todo"
    args_model = TodoArgs

    async def execute(self, *, tasks_text: str) -> dict:
        items = []
        for raw in tasks_text.splitlines():
            line = raw.strip()
            if not line:
                continue                      # skip blank lines
            done = line[:3].lower() == "[x]"
            title = (line[3:] if done else line).strip().lstrip("-•").strip()
            if not title:
                raise ValueError(f"task line is empty (no content after removing the marker and symbols): {raw!r}")
            items.append({"title": title, "done": done})
        if not items:
            raise ValueError("task list is empty: provide at least one task")   # → error result (visible to the LLM)
        open_count = sum(1 for it in items if not it["done"])
        return {"total": len(items), "open": open_count, "items": items}
