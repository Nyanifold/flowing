"""Output normalization demo (4-5, offline): the five shapes / forbidden
blocks / media carriers.

Run: uv run python demo_shapes.py
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
    print("① The five normalized shapes (ordinary execute return values "
          "→ one unified exit):")
    for label, value in [("None", None), ("str", "done"),
                         ("scalar", 42), ("dict", {"ok": True}),
                         ("plain list", ["phase one", "phase two"]),
                         ("mixed list", ["phase one", Image(data=png_b64, mime_type="image/png")])]:
        out = await normalize_output(value)
        blocks = output_to_blocks(out)
        kinds = [b.type for b in blocks]
        print(f"   {label:<10} → blocks={kinds}")

    print("② Forbidden blocks (ToolCallBlock / ThinkingBlock on any path "
          "→ ValueError):")
    try:
        await normalize_output(ToolCallBlock(id="c1", name="x", args={}))
    except ValueError as exc:
        print(f"   {exc}")

    print("③ Media carriers (Image → ImageBlock, base64 inline):")
    png = base64.b64encode(bytes.fromhex(
        "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
        "1f15c4890000000d49444154789c626001000000ffff030000060005"
        "57bfabd40000000049454e44ae426082")).decode()
    out = await normalize_output(Image(data=png, mime_type="image/png"))
    blocks = output_to_blocks(out)
    print(f"   Image → {[b.type for b in blocks]} (mime={blocks[0].mime_type})")

    print("④ error appended (output_to_blocks appends an error text block "
          "at the end):")
    blocks = output_to_blocks({"ok": False}, error="build failed: missing dependencies")
    print(f"   {[b.type for b in blocks]} (last block={blocks[-1].text!r})")


asyncio.run(main())
