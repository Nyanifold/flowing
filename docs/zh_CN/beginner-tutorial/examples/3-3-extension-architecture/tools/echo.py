"""回声工具（策略注入演示的调用素材）。"""
from pydantic import BaseModel

from flowing import ScriptTool


class EchoArgs(BaseModel):
    text: str


class EchoTool(ScriptTool):
    """原样返回输入文本。"""

    name = "echo"
    args_model = EchoArgs

    async def execute(self, *, text: str) -> str:
        return text
