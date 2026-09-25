"""Equivalent-channel demo: a function marked with @flowing_tool (no marking of classes, no handwritten subclass)."""
from flowing import flowing_tool


@flowing_tool
async def shout(text: str) -> str:
    """Convert text to uppercase."""
    return text.upper()
