"""callable 指针通道的实现文件：与 tools/todo.py 同逻辑的独立子类。

目录名 todo-fn == 类声明的 name（identity 一致性断言的要求）。
"""
from pydantic import BaseModel, Field

from flowing import ScriptTool


class TodoFnArgs(BaseModel):
    tasks_text: str = Field(description="任务清单文本：每行一个任务；以 [x] 开头表示已完成")


class TodoToolFn(ScriptTool):
    """（通道演示）任务清单解析——与 todo 工具同逻辑的指针通道实现。"""

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
