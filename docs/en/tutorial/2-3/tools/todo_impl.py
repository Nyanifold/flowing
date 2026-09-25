"""Implementation file for the callable-pointer channel: an independent subclass with the same logic as tools/todo.py.

The directory name todo-fn must equal the name declared by the class
(required by the identity-consistency assertion).
"""
from pydantic import BaseModel, Field

from flowing import ScriptTool


class TodoFnArgs(BaseModel):
    tasks_text: str = Field(description="Task-list text: one task per line; a leading [x] marks it completed")


class TodoToolFn(ScriptTool):
    """(Channel demo) Task-list parsing — the pointer-channel implementation with the same logic as the todo tool."""

    name = "todo-fn"
    args_model = TodoFnArgs

    async def execute(self, *, tasks_text: str) -> dict:
        items = []
        for raw in tasks_text.splitlines():
            line = raw.strip()
            if not line:
                continue
            done = line[:3].lower() == "[x]"
            title = (line[3:] if done else line).strip().lstrip("-•").strip()
            items.append({"title": title, "done": done})
        return {"total": len(items),
                "open": sum(1 for it in items if not it["done"]), "items": items}
