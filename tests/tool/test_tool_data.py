"""Tool 数据与归一化族测试：测试清单 16–24。

覆盖 ToolCall.from_block / ToolResult（blocked / as_message / 经
``Tool.__call__`` 占位调度层包装）/ normalize_output（五形态、幂等、
违禁块、媒体载体转换）/ output_to_blocks（D22 塑形表）/
register_media_converter（D10）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pytest
from pydantic import BaseModel

from flowing.message import (
    FileBlock,
    ImageBlock,
    MediaBlock,
    MessageKind,
    StructBlock,
    TextBlock,
    ThinkingBlock,
    ToolCallBlock,
)
from flowing.tool import (
    _MEDIA_CONVERTERS,
    File,
    Image,
    Tool,
    ToolCall,
    ToolDefinition,
    ToolResult,
    normalize_output,
    output_to_blocks,
    register_media_converter,
)

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32  # PNG 魔数 + 填充


@pytest.fixture
def png_path(tmp_path) -> Path:
    p = tmp_path / "plot.png"
    p.write_bytes(PNG_BYTES)
    return p


class _DictTool(Tool):
    """清单 17/19/20 的最小执行体：execute 行为由参数化注入。"""

    def __init__(self, behavior) -> None:
        self.definition = ToolDefinition(name="demo", description="demo")
        self._behavior = behavior

    async def execute(self, **kwargs):
        return self._behavior()


# ---------------------------------------------------------------------------
# ToolCall / ToolResult（清单 16、17、18、19、20）
# ---------------------------------------------------------------------------


class TestToolCallFromBlock:
    def test_t16_from_block(self):
        """清单 16：from_block 剥离 type，shortcut 恒 None，入参不被修改。"""
        block = ToolCallBlock(
            type="tool_call", id="c1", name="read_file", args={"path": "a.py"})
        call = ToolCall.from_block(block)
        assert call.id == "c1" and call.name == "read_file"
        assert call.args == {"path": "a.py"}
        assert call.shortcut is None
        # 纯函数：入参 block 不被修改
        assert block.type == "tool_call" and block.args == {"path": "a.py"}


class TestToolResult:
    async def test_t17_execute_return_wrapped_completed(self):
        """清单 17：execute 返回 dict 经 __call__ 包装 → completed。"""
        tool = _DictTool(lambda: {"tx": "abc"})
        result = await tool({})
        assert result.status == "completed"
        assert result.output == {"tx": "abc"}
        assert result.error is None

    def test_t18_blocked_factory(self):
        """清单 18：blocked() 唯一来源；as_message 塑形为 TextBlock(reason)。"""
        result = ToolResult.blocked("拒绝")
        assert result.status == "blocked" and result.error is None
        msg = result.as_message("c1")
        assert msg.kind is MessageKind.TOOL
        assert msg.tool_call_id == "c1" and msg.tool_status == "blocked"
        assert msg.content == [TextBlock(text="拒绝")]
        # 无 reason → output None → content 为空
        assert ToolResult.blocked().as_message("c2").content == []

    async def test_t19_execute_exception_wrapped_error(self):
        """清单 19：execute 抛异常 → error 正常产物（本层无钩子参与，
        「不触发 on_provider_error」由 __call__ 无任何钩子调用保证）。"""
        def _raise():
            raise ValueError("bad")

        result = await _DictTool(_raise)({})
        assert result.status == "error"
        assert "bad" in (result.error or "")
        assert result.output is None

    async def test_t20_image_carrier_to_block(self, png_path):
        """清单 20：execute 返回 Image 载体 → 归一化为 ImageBlock 单块形态。"""
        result = await _DictTool(lambda: Image(path=png_path))({})
        assert result.status == "completed"
        assert isinstance(result.output, ImageBlock)
        assert result.output.mime_type == "image/png"
        msg = result.as_message("c1")
        assert msg.content == [result.output]


# ---------------------------------------------------------------------------
# normalize_output（清单 21、22）
# ---------------------------------------------------------------------------


class TestNormalizeOutput:
    async def test_t21_idempotent(self, png_path):
        """清单 21：已归一值（块 / 基础值 / 混合 list）再跑一遍结果不变。"""
        block = await normalize_output(Image(path=png_path))
        assert await normalize_output(block) is block

        basic = {"tx": "abc", "n": 1}
        assert await normalize_output(basic) == basic

        mixed = await normalize_output(["文本", {"k": 1}, Image(path=png_path)])
        assert await normalize_output(mixed) == mixed

    async def test_t22_forbidden_blocks(self):
        """清单 22：ToolCallBlock / ThinkingBlock 混入结果 → ValueError。"""
        with pytest.raises(ValueError):
            await normalize_output(
                ToolCallBlock(type="tool_call", id="c", name="n", args={}))
        with pytest.raises(ValueError):
            await normalize_output(ThinkingBlock(type="thinking", thinking="t"))
        # 混合 list 成员同样拒绝
        with pytest.raises(ValueError):
            await normalize_output(
                ["ok", ThinkingBlock(type="thinking", thinking="t")])

    async def test_media_routing(self, png_path, tmp_path):
        """D7/D8 路由补充：File 强制文件块；魔数兜底；未知类型 → FileBlock.bin。"""
        # File 显式推翻推断：png 路径强制 FileBlock
        out = await normalize_output(File(path=png_path))
        assert isinstance(out, FileBlock) and out.mime_type == "image/png"
        # 裸 bytes 魔数 → ImageBlock，hash.ext 合成名
        out = await normalize_output(PNG_BYTES)
        assert isinstance(out, ImageBlock)
        assert out.mime_type == "image/png" and out.name.endswith(".png")
        # 推不出 MIME → FileBlock（宁文件勿图），.bin 兜底
        out = await normalize_output(b"\x01\x02 mystery")
        assert isinstance(out, FileBlock) and out.name.endswith(".bin")
        # 裸 Path（绝对）→ 按后缀路由
        out = await normalize_output(png_path)
        assert isinstance(out, ImageBlock) and out.name == "plot.png"
        # 裸 Path 相对 → ValueError
        with pytest.raises(ValueError):
            await normalize_output(Path("rel/plot.png"))
        # 不可转换对象 → ValueError（D11 作者 bug 通道）
        with pytest.raises(ValueError):
            await normalize_output(object())

    def test_carrier_validation(self):
        """W8 载体构造校验：data/path 至少其一；path 须绝对。"""
        with pytest.raises(ValueError):
            Image()
        with pytest.raises(ValueError):
            Image(path="rel/x.png")


# ---------------------------------------------------------------------------
# register_media_converter（清单 23）
# ---------------------------------------------------------------------------


class TestMediaConverter:
    @pytest.fixture
    def fake_lib(self):
        """注册伪库转换器并在用例结束后还原注册表。"""
        base = type("FakeImage", (), {"__module__": "fakelib.media"})
        register_media_converter(
            "fakelib.media", "FakeImage",
            convert=lambda o: Image(data=PNG_BYTES, mime_type="image/png"))
        try:
            yield base
        finally:
            _MEDIA_CONVERTERS.pop()

    def test_t23_duplicate_registration(self, fake_lib):
        """清单 23a：同 (module, qualname) 重复注册 → ValueError。"""
        with pytest.raises(ValueError):
            register_media_converter(
                "fakelib.media", "FakeImage", convert=lambda o: None)

    async def test_t23_mro_subclass_hit(self, fake_lib):
        """清单 23b：子类实例经 MRO 全名命中已注册转换器。"""

        class Sub(fake_lib):
            """子类自身 module/qualname 均不命中，靠 MRO 上溯。"""

        out = await normalize_output(Sub())
        assert isinstance(out, ImageBlock)
        assert out.mime_type == "image/png"


# ---------------------------------------------------------------------------
# output_to_blocks（清单 24）
# ---------------------------------------------------------------------------


class _Payload(BaseModel):
    tx: str


@dataclass
class _DcPayload:
    tx: str


class TestOutputToBlocks:
    def test_t24_five_forms(self, png_path):
        """清单 24：五形态逐态 + error 末尾追加（D22 表）。"""
        # None → []
        assert output_to_blocks(None) == []
        # str → [TextBlock(v)]（原样，不 dumps）
        assert output_to_blocks("你好") == [TextBlock(text="你好")]
        # 标量 → [TextBlock(json.dumps(v))]（可解析回）
        assert output_to_blocks(42) == [TextBlock(text="42")]
        assert output_to_blocks(True) == [TextBlock(text="true")]
        # dict / dataclass / BaseModel → [StructBlock(data)]
        assert output_to_blocks({"tx": "abc"}) == [StructBlock(data={"tx": "abc"})]
        assert output_to_blocks(_DcPayload(tx="abc")) == [
            StructBlock(data={"tx": "abc"})]
        assert output_to_blocks(_Payload(tx="abc")) == [
            StructBlock(data={"tx": "abc"})]
        # 纯基础 list → [StructBlock(list)]
        assert output_to_blocks([1, "a", {"k": 1}]) == [
            StructBlock(data=[1, "a", {"k": 1}])]
        # 单块 → [block]
        img = ImageBlock(data="eA==", name="x.png", mime_type="image/png")
        assert output_to_blocks(img) == [img]
        # 混合 list → 逐成员保序：str 原样 / 标量 dumps / dict·list →
        # StructBlock / 块透传
        blocks = output_to_blocks(["注意", 7, {"k": 1}, img])
        assert blocks == [
            TextBlock(text="注意"),
            TextBlock(text=json.dumps(7)),
            StructBlock(data={"k": 1}),
            img,
        ]
        # error 末尾追加 TextBlock
        assert output_to_blocks({"tx": "abc"}, error="x") == [
            StructBlock(data={"tx": "abc"}), TextBlock(text="x")]
        assert output_to_blocks(None, error="x") == [TextBlock(text="x")]

    def test_t24b_deep_buried_non_json_honest_failure(self):
        """深层埋藏的非 JSON 对象：归一期放行，塑形期 StructBlock 构造
        校验诚实失败（ValueError，框架错误通道）。"""
        with pytest.raises(ValueError):
            output_to_blocks({"a": object()})
