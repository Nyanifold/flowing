"""任务清单工具·状态袋版（4-2）：给 2-3 的无状态 todo 接上持久化。

退出 repl 再启动，清单原样恢复——「为什么能恢复」见 4-2 正文。
"""
from typing import Literal

from pydantic import BaseModel, Field

from flowing import Agent, ScriptTool


class TodoArgs(BaseModel):
    action: Literal["add", "done", "list"] = Field(description="add=加任务；done=完成；list=列出")
    title: str | None = Field(default=None, description="任务标题（add 时必填）")
    index: int | None = Field(default=None, description="任务序号（done 时必填，从 1 起）")


class TodoStateTool(ScriptTool):
    """任务清单：增删查，持久化在 Agent 状态袋（跨会话保留）。"""

    name = "todo"
    args_model = TodoArgs

    async def execute(self, *, action: str, title: str | None,
                      index: int | None, caller: Agent) -> dict:
        items: list[dict] = caller.state.get("todo_items") or []
        if action == "add":
            if not title:
                raise ValueError("add 需要 title")
            items.append({"title": title, "done": False})
        elif action == "done":
            if index is None or not (1 <= index <= len(items)):
                raise ValueError(f"序号非法：{index!r}（共 {len(items)} 项）")
            items[index - 1]["done"] = True
        caller.state["todo_items"] = items   # 写透落盘
        return {"items": items,
                "open": sum(1 for it in items if not it["done"])}
