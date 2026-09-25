"""等价通道演示：@flowing_tool 打标函数（不打标类、不手写子类）。"""
from flowing import flowing_tool


@flowing_tool
async def shout(text: str) -> str:
    """把文本转换为大写。"""
    return text.upper()
