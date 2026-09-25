"""结果归一演示（4-5，离线）：五形态 / 违禁块 / 媒体载体。

运行：uv run python demo_shapes.py
"""
import asyncio
import base64

from flowing.media import Image
from flowing.message import StructBlock, TextBlock, ToolCallBlock
from flowing.tool import normalize_output, output_to_blocks


async def main() -> None:
    png_b64 = base64.b64encode(bytes.fromhex(
        "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
        "1f15c4890000000d49444154789c626001000000ffff030000060005"
        "57bfabd40000000049454e44ae426082")).decode()
    print("① 归一五形态（execute 普通值出 → 统一出口）：")
    for label, value in [("None", None), ("str", "完成"),
                         ("标量", 42), ("dict", {"ok": True}),
                         ("纯基础 list", ["阶段一", "阶段二"]),
                         ("混合 list", ["阶段一", Image(data=png_b64, mime_type="image/png")])]:
        out = await normalize_output(value)
        blocks = output_to_blocks(out)
        kinds = [b.type for b in blocks]
        print(f"   {label:<10} → blocks={kinds}")

    print("② 违禁块（ToolCallBlock / ThinkingBlock 任何路径 → ValueError）：")
    try:
        await normalize_output(ToolCallBlock(id="c1", name="x", args={}))
    except ValueError as exc:
        print(f"   {exc}")

    print("③ 媒体载体（Image → ImageBlock，base64 内联）：")
    png = base64.b64encode(bytes.fromhex(
        "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
        "1f15c4890000000d49444154789c626001000000ffff030000060005"
        "57bfabd40000000049454e44ae426082")).decode()
    out = await normalize_output(Image(data=png, mime_type="image/png"))
    blocks = output_to_blocks(out)
    print(f"   Image → {[b.type for b in blocks]}（mime={blocks[0].mime_type}）")

    print("④ error 追加（output_to_blocks 末尾补错误文本块）：")
    blocks = output_to_blocks({"ok": False}, error="构建失败：缺依赖")
    print(f"   {[b.type for b in blocks]}（末块={blocks[-1].text!r}）")


asyncio.run(main())
