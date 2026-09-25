"""任务清单工具（2-3）：零散任务文本进、结构化清单出。

无状态纯函数式实现（状态袋用法是 4-2 的内容，届时再续）；
演示：args_model 声明即模型、普通异常 → LLM 可见 error 结果。
"""
from pydantic import BaseModel, Field

from flowing import ScriptTool


class TodoArgs(BaseModel):
    tasks_text: str = Field(
        description="任务清单文本：每行一个任务；以 [x] 开头表示已完成")


class TodoTool(ScriptTool):
    """把零散的任务文本解析成结构化清单：解析、校验、去空行，返回未结任务计数。"""

    name = "todo"
    args_model = TodoArgs

    async def execute(self, *, tasks_text: str) -> dict:
        items = []
        for raw in tasks_text.splitlines():
            line = raw.strip()
            if not line:
                continue                      # 去空行
            done = line[:3].lower() == "[x]"
            title = (line[3:] if done else line).strip().lstrip("-•").strip()
            if not title:
                raise ValueError(f"任务行为空（去掉标记与符号后无内容）：{raw!r}")
            items.append({"title": title, "done": done})
        if not items:
            raise ValueError("任务清单为空：至少提供一条任务")   # → error 结果（LLM 可见）
        open_count = sum(1 for it in items if not it["done"])
        return {"total": len(items), "open": open_count, "items": items}
