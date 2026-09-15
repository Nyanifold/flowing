"""``flowing.media`` —— 结果归一与媒体通道：媒体载体、转换器注册与 ``normalize_output`` / ``output_to_blocks``。

.. rubric:: 功能介绍

媒体载体 :class:`Image` / :class:`File` / :class:`Audio` / :class:`Video`
是工具返回值中富媒体内容的统一表示；:class:`MediaConverter` 与
:func:`register_media_converter` 让 ``normalize_output`` 认识第三方库
返回的对象；:func:`normalize_output`（载体 → 块的唯一转换点，async、
幂等）与 :func:`output_to_blocks`（归一值 → ``ContentBlock`` 列表的塑形
统一出口）构成工具结果的归一管线。公开符号同时经 ``flowing.tool``
re-export（工具子系统的包级导览与全局约定见其模块 docstring）。

.. seealso::

    - :mod:`flowing.tool` —— 工具子系统包（公开符号的 re-export 面）。
    - :class:`flowing.tool.ToolResult` —— 归一值的承载字段 ``output``。
    - :mod:`flowing.message` —— 落树块类型。
"""

from __future__ import annotations   # 注解延迟求值，配合 TYPE_CHECKING 破注解级循环边

import asyncio
import base64
import binascii
import hashlib
import json
import mimetypes
import os

from collections.abc import Callable
from dataclasses import asdict, dataclass, is_dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from flowing.message import (
    AudioBlock,
    ContentBlock,
    FileBlock,
    ImageBlock,
    MediaBlock,
    StructBlock,
    TextBlock,
    ThinkingBlock,
    ToolCallBlock,
    VideoBlock,
)

# ──────────────────────────────────────────────────────────────────
# 结果归一化与媒体通道
# ──────────────────────────────────────────────────────────────────


def _validate_carrier(carrier: "Image | File | Audio | Video") -> None:
    """载体四类共用的构造校验（作者笔误 → ``ValueError``）。

    - ``data`` / ``path`` 至少其一；
    - ``path`` 须为绝对路径。
    """
    if carrier.data is None and carrier.path is None:
        raise ValueError(f"at least one of data or path is required for {type(carrier).__name__}")
    if carrier.path is not None and not Path(carrier.path).is_absolute():
        raise ValueError(
            f"{type(carrier).__name__}.path must be an absolute path: {carrier.path!r}")


@dataclass
class Image:
    """工具返回图片时使用的媒体载体（消息层 Block 对工具作者透明）。

    .. rubric:: 功能介绍

    工具作者通常不需要本载体：``execute`` 直接返回裸 ``bytes`` 或
    ``pathlib.Path`` 即可，框架自动推断 MIME 与文件名并转为消息层的
    ``ImageBlock``。需要显式指定 MIME、文件名，或推翻推断结果（如
    ``File(path="x.png")`` 强制文件块而非图片块）时才构造本载体。
    第三方库对象（如 ``PIL.Image.Image``）也直接返回，经
    :func:`register_media_converter` 注册的转换器处理。工具作者接口面
    不出现消息层 Block——载体 → 块的转换只发生在
    :func:`flowing.tool.normalize_output` （async，可能读盘）。

    .. rubric:: 行为要点

    - ``data`` 与 ``path`` 至少给其一，都没有 → ``ValueError``；
      ``path`` 必须是绝对路径，相对路径 → ``ValueError`` （作者笔误，
      直接上抛，不包成工具结果）。
    - MIME 推断链：显式 ``mime_type`` > path 后缀（``mimetypes``）>
      bytes 魔数；``image/*`` → ``ImageBlock``、``audio/*`` →
      ``AudioBlock``、``video/*`` → ``VideoBlock``、其余或推不出 →
      ``FileBlock`` （宁文件勿图）。显式载体声明永远压过推断。
    - 文件名填充链：显式 ``name`` > ``path`` 文件名 > 合成
      ``<sha256(data)[:12]>.<ext>`` （ext 由 MIME 反推，MIME 未知 →
      ``.bin``）。

    .. seealso::

        - :func:`flowing.tool.normalize_output` —— 载体 → 块的唯一转换点。
        - :class:`flowing.message.ImageBlock` —— 转换产物（消息层）。
    """

    data: bytes | str | None = None
    """bytes 原始数据或 str base64；与 ``path`` 至少其一。"""
    path: str | os.PathLike | None = None
    """绝对路径（接受 ``pathlib.Path``）；相对路径 → ``ValueError``。"""
    mime_type: str | None = None
    """显式 MIME；缺省走推断链（见类 docstring 行为要点）。"""
    name: str | None = None
    """文件名；缺省 = path 文件名 > hash.ext 合成（见类 docstring）。"""

    def __post_init__(self) -> None:
        _validate_carrier(self)


@dataclass
class File:
    """工具返回文件时使用的媒体载体——与 `Image` 同构。

    显式推翻推断的通道：``File(path="x.png")`` 强制产 ``FileBlock``
    而非 ``ImageBlock``。字段语义与校验规则见 `Image`。
    """

    data: bytes | str | None = None
    path: str | os.PathLike | None = None
    mime_type: str | None = None
    name: str | None = None

    def __post_init__(self) -> None:
        _validate_carrier(self)


@dataclass
class Audio:
    """工具返回音频时使用的媒体载体——与 `Image` 同构，产物
    ``AudioBlock``。字段语义与校验规则见 `Image`。
    """

    data: bytes | str | None = None
    path: str | os.PathLike | None = None
    mime_type: str | None = None
    name: str | None = None

    def __post_init__(self) -> None:
        _validate_carrier(self)


@dataclass
class Video:
    """工具返回视频时使用的媒体载体——与 `Image` 同构，产物
    ``VideoBlock``。字段语义与校验规则见 `Image`。
    """

    data: bytes | str | None = None
    path: str | os.PathLike | None = None
    mime_type: str | None = None
    name: str | None = None

    def __post_init__(self) -> None:
        _validate_carrier(self)


@dataclass
class MediaConverter:
    """第三方库对象 → 媒体载体的转换器注册条目（由
    :func:`register_media_converter` 写入模块级注册表）。

    .. rubric:: 功能介绍

    条目固定为 ``(module, qualname, convert)`` 三元组：前两者是第三方
    库对象的类全名（``模块名.类限定名``），``convert`` 是把该对象转为
    `Image` / `File` / `Audio` / `Video` 四类载体之一的函数。匹配时沿
    对象的类 MRO 逐级比对全名——子类实例同样命中（注册的是基类、返回
    的是子类实例也能转换）。注册表天然惰性：库在任何时候被 import，
    下一次判别即生效，没有初始化快照问题。

    .. seealso::

        - :func:`flowing.tool.register_media_converter` —— 注册入口。
        - :func:`flowing.tool.normalize_output` —— 匹配与转换的消费点。
    """

    module: str
    """库模块全名，如 ``"PIL.Image"``。"""
    qualname: str
    """类限定名，如 ``"Image"``。"""
    convert: Callable[[Any], "Image | File | Audio | Video"]
    """第三方对象 → 执行层载体四类之一的转换函数。"""


def _pil_image_convert(img: Any) -> "Image":
    """内置 Pillow 转换器：``PIL.Image.Image`` → PNG bytes 的 `Image` 载体。

    PIL 未安装不影响本定义的存在——匹配走 MRO 全名字符串，不 import PIL；
    convert 被调用时 ``PIL.Image.Image`` 实例已存在，PIL 必然已装入。
    内部 API。
    """
    import io

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return Image(data=buf.getvalue(), mime_type="image/png")


_MEDIA_CONVERTERS: list[MediaConverter] = [
    # 内置条目：Pillow——PIL.Image.Image 实例（含子类）经 MRO 全名
    # 匹配命中，convert 取 PNG bytes 包 Image 载体（mime_type="image/png"）
    MediaConverter("PIL.Image", "Image", convert=_pil_image_convert),
]
"""媒体转换注册表（模块级，进程内共享）。匹配算法与语义见
`MediaConverter`；写入入口为 :func:`register_media_converter`。"""


def register_media_converter(
    tp_or_module: type | str,
    qualname: str | None = None,
    convert: Callable[[Any], "Image | File | Audio | Video"] | None = None,
) -> None:
    """注册第三方库对象 → 媒体载体的转换器（进程内全局生效）。

    .. rubric:: 功能介绍

    让 `normalize_output` 认识第三方库返回的对象：注册后，工具返回该库
    的实例（或它的子类实例）时，框架会调用 ``convert`` 把它转为
    `Image` / `File` / `Audio` / `Video` 载体之一，再走载体通道转块。
    直传 ``type`` 对象时降级存名字（``tp.__module__`` /
    ``tp.__qualname__``）——全库只有 MRO 全名一种匹配机制。

    .. rubric:: 行为要点

    - 同 ``(module, qualname)`` 重复注册 → ``ValueError``。
    - ``tp_or_module`` 为 ``type`` 时 ``qualname`` 必须省略；
      module / qualname / convert 三要素缺一 → ``ValueError``。
    - 注册表是进程内模块级状态，跨测试或跨 Runtime 共享；测试如需
      隔离应自行清理。

    .. rubric:: 使用示例

    .. code-block:: python

        from flowing.tool import Image, register_media_converter

        class MyPlot:
            \"\"\"第三方绘图库返回的对象类型。\"\"\"

            def render_png(self) -> bytes:
                ...

        def my_plot_to_carrier(plot: MyPlot) -> Image:
            return Image(data=plot.render_png(), mime_type="image/png")

        # 直传 type 对象（降级存 module.qualname 全名）；库全名 + 类
        # 限定名的字符串形态等价
        register_media_converter(MyPlot, convert=my_plot_to_carrier)

    :param tp_or_module: ``type`` 对象（降级存名字）或模块全名字符串。
    :param qualname: 类限定名；``tp_or_module`` 为 ``type`` 时省略。
    :param convert: 转换函数，产物为执行层载体四类之一。
    """
    # 归一为 (module, qualname, convert) 三元组存 _MEDIA_CONVERTERS；
    # 同 (module, qualname) 重复注册 → ValueError
    if isinstance(tp_or_module, type):
        # 直传 type 降级存名字（全库只有 MRO 全名一种匹配机制）
        if qualname is not None:
            raise ValueError("qualname should be omitted when tp_or_module is a type")
        module, qname = tp_or_module.__module__, tp_or_module.__qualname__
    else:
        module, qname = tp_or_module, qualname
    # 三要素缺失属调用方笔误（spec 未具名异常类型，按编程错误通道 ValueError）
    if not module or not qname or convert is None:
        raise ValueError(
            "register_media_converter requires all three of module / qualname / convert")
    if any(c.module == module and c.qualname == qname for c in _MEDIA_CONVERTERS):
        raise ValueError(f"media converter already registered: {module}.{qname}")
    _MEDIA_CONVERTERS.append(
        MediaConverter(module=module, qualname=qname, convert=convert))


def _sniff_mime(data: bytes) -> str | None:
    """bytes 魔数嗅探：只内置常见魔数，推不出返回 ``None``
    （调用方按「宁文件勿图」落 ``FileBlock``）。内部 API。"""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[:4] == b"RIFF" and data[8:12] == b"WAVE":
        return "audio/wav"
    if data.startswith(b"ID3") or data[:2] == b"\xff\xfb":
        return "audio/mpeg"
    if data[4:8] == b"ftyp":
        return "video/mp4"
    if data.startswith(b"%PDF-"):
        return "application/pdf"
    return None


def _route_block(mime: str | None) -> "type[MediaBlock]":
    """MIME 路由：``image/*`` / ``audio/*`` / ``video/*`` → 对应块；
    其余 / 推不出 → ``FileBlock`` （宁文件勿图）。内部 API。"""
    if mime is not None:
        category = mime.split("/", 1)[0]
        if category == "image":
            return ImageBlock
        if category == "audio":
            return AudioBlock
        if category == "video":
            return VideoBlock
    return FileBlock


def _synth_name(data: bytes, mime: str | None) -> str:
    """文件名合成：``<sha256(data)[:12]>.<ext>`` （ext 由 MIME 反推，
    未知 → ``.bin``）。内部 API。"""
    ext = mimetypes.guess_extension(mime) if mime else None
    return f"{hashlib.sha256(data).hexdigest()[:12]}{ext or '.bin'}"


async def _media_to_block(
    *,
    data: bytes | str | None,
    path: str | os.PathLike | None,
    mime_type: str | None,
    name: str | None,
    forced: "type[MediaBlock] | None",
) -> MediaBlock:
    """载体 / 裸 bytes / 裸 Path → 媒体块（I/O 唯一发生点，async）。

    - MIME 推断链：显式 ``mime_type`` > path 后缀（``mimetypes``）>
      bytes 魔数；``forced`` 非 ``None`` （显式载体声明）时块类别不再
      经路由。
    - 文件名填充链：显式 ``name`` > path 文件名 > hash.ext 合成。

    内部 API。
    """
    if isinstance(data, str):
        # data str = base64 形态（见 Image.data 字段注释）；解码为 raw 统一处理
        try:
            raw = base64.b64decode(data, validate=True)
        except binascii.Error as exc:
            raise ValueError(f"carrier data string must be valid base64: {exc}") from exc
    elif data is None:
        raw = await asyncio.to_thread(Path(path).read_bytes)  # type: ignore[arg-type]
    else:
        raw = data
    p = Path(path) if path is not None else None
    mime = mime_type
    if mime is None and p is not None:
        mime = mimetypes.guess_type(str(p))[0]
    if mime is None:
        mime = _sniff_mime(raw)
    block_cls = forced if forced is not None else _route_block(mime)
    block_name = name or (p.name if p is not None else None) or _synth_name(raw, mime)
    return block_cls(
        data=base64.b64encode(raw).decode("ascii"), name=block_name, mime_type=mime)


_CARRIER_BLOCK: "dict[type, type[MediaBlock]]" = {
    Image: ImageBlock,
    File: FileBlock,
    Audio: AudioBlock,
    Video: VideoBlock,
}
"""显式载体四类 → 强制块类别（显式声明永远压过推断）。内部 API。"""


def _find_media_converter(obj: Any) -> MediaConverter | None:
    """注册表匹配：沿 ``type(obj).__mro__`` 查 ``module.qualname`` 全名，
    子类命中。内部 API。"""
    mro_names = {f"{c.__module__}.{c.__qualname__}" for c in type(obj).__mro__}
    for converter in _MEDIA_CONVERTERS:
        if f"{converter.module}.{converter.qualname}" in mro_names:
            return converter
    return None


async def normalize_output(value: Any) -> Any:
    """把 ``execute`` 返回值 / ``ToolResult.output`` 原料归一化为五形态之一。

    .. rubric:: 功能介绍

    工具结果的统一归一入口：`Tool.__call__` 与 ``Agent.tool_call`` 收尾
    各调一次（两次都安全，见行为要点）。产物为五形态之一：``None`` /
    基础值原样 / 单块 / 纯基础 list / 混合 list（基础成员原样保留 +
    非基础成员已转块）。「基础类型」指 ``None`` / ``bool`` / ``int`` /
    ``float`` / ``str`` / ``dict`` / ``list`` / ``tuple`` / dataclass
    实例 / pydantic ``BaseModel`` 实例（JSON 兼容及其常见载体）。

    .. rubric:: 行为要点

    - ``value`` 是 ``list`` / ``tuple`` （顶层；tuple 归一为 list）：浅层
      检查每个成员，不递归。全为基础 → 原样返回（JSON 形态）；含非基础
      成员 → 逐成员转换，基础成员原样保留、非基础成员单独转块（混排
      不报错）。
    - ``value`` 非 list / tuple：基础类型 → 原样返回；合法块
      （``TextBlock`` / ``StructBlock`` / ``MediaBlock``）→ 原样放行；
      载体四类 / 裸 ``bytes`` / 绝对路径 ``Path`` / 注册表命中对象 →
      转对应媒体块（async，可读盘）；违禁块（``ToolCallBlock`` /
      ``ThinkingBlock``，任何层级、任何路径）→ ``ValueError``；不可
      转换对象 → ``ValueError``。裸 ``Path`` 相对路径 → ``ValueError``。
    - 幂等：已归一的值再跑一遍结果不变（块原样放行、基础保留）——这是
      `Tool.__call__` 与 ``Agent.tool_call`` 收尾双调用点安全的前提。
    - 媒体 → 块的 I/O 转换只发生在本函数（async）；塑形（五形态 →
      content 块列表）是纯同步，见 `output_to_blocks`。
    - 边缘情况：深层埋藏的非 JSON 对象（如 ``[{"a": pil_image}]``）
      归一化期放行，`as_message` 塑形时 ``StructBlock`` 构造校验失败 →
      ``ValueError`` （诚实失败点，不追求早发现）。

    .. seealso::

        - :func:`flowing.tool.output_to_blocks` —— 下游塑形统一出口。
        - :class:`flowing.tool.ToolResult` —— 五形态的承载字段 ``output``。
    """
    def _is_basic(v: Any) -> bool:
        # 「基础类型」：None/bool/int/float/str/dict/list/tuple/dataclass
        # 实例/pydantic BaseModel 实例（JSON 兼容及其常见载体）。
        # 注意：媒体载体四类与全部 ContentBlock 都是 dataclass 实例，必须
        # 显式排除（否则永远走不到载体转换与违禁块检查）。
        return (
            v is None
            or isinstance(v, (bool, int, float, str, dict, list, tuple))
            or (is_dataclass(v)
                and not isinstance(v, (type, ContentBlock, Image, File, Audio, Video)))
            or isinstance(v, BaseModel)
        )

    async def _convert_one(v: Any) -> Any:
        """单成员归一：基础原样 / 合法块原样 / 违禁块 ValueError /
        载体·bytes·Path·注册表命中转块 / 其余 ValueError（框架错误通道）。"""
        if _is_basic(v):
            return v
        if isinstance(v, (ToolCallBlock, ThinkingBlock)):
            # 违禁块：浅层出现 -> 框架错误通道（作者 bug）；深层埋藏
            # 的块由塑形期 StructBlock 构造校验同通道兜住
            raise ValueError(f"forbidden block type in tool result: {type(v).__name__}")
        if isinstance(v, ContentBlock):
            # 合法块（TextBlock/StructBlock/MediaBlock）原样放行；其余块类型
            # 按违禁同通道处理
            if isinstance(v, (TextBlock, StructBlock, MediaBlock)):
                return v
            raise ValueError(f"forbidden block type in tool result: {type(v).__name__}")
        if isinstance(v, (Image, File, Audio, Video)):
            # 载体四类：显式声明压过推断，块类别由载体类型强制
            return await _media_to_block(
                data=v.data, path=v.path, mime_type=v.mime_type, name=v.name,
                forced=_CARRIER_BLOCK[type(v)])
        if isinstance(v, bytes):
            return await _media_to_block(
                data=v, path=None, mime_type=None, name=None, forced=None)
        if isinstance(v, Path):
            # 裸 Path 与载体同口径：相对路径 → ValueError（spec 未单列裸
            # Path 的相对路径处置，按载体规则同口径落实）
            if not v.is_absolute():
                raise ValueError(f"bare Path results must be absolute paths: {v!r}")
            return await _media_to_block(
                data=None, path=v, mime_type=None, name=None, forced=None)
        converter = _find_media_converter(v)
        if converter is not None:
            # 注册表命中：先转载体四类之一，再走载体通道
            carrier = converter.convert(v)
            return await _media_to_block(
                data=carrier.data, path=carrier.path, mime_type=carrier.mime_type,
                name=carrier.name, forced=_CARRIER_BLOCK[type(carrier)])
        raise ValueError(
            f"tool result type cannot be converted: {type(v).__name__}")   # 作者 bug

    if isinstance(value, (list, tuple)):
        # 顶层序列：浅层判别（不递归）——全基础原样（tuple 归一为 list）；
        # 有非基础成员则基础保留、非基础逐成员转块（混排不报错）
        items = list(value)
        if all(_is_basic(v) for v in items):
            return items
        return [await _convert_one(v) for v in items]
    return await _convert_one(value)


def output_to_blocks(output: Any, *, error: str | None = None) -> list[ContentBlock]:
    """把五形态 ``output`` 塑形为消息 ``content`` 块列表（同步统一出口）。

    .. rubric:: 功能介绍

    塑形只有这一处实现，全部消费方走同一条代码路径：
    :meth:`flowing.tool.ToolResult.as_message` （同步工具结果 → TOOL
    消息，内部调本函数）、Agent 侧后台投递驱动（异步工具分段 / 终值 →
    EVENT 消息）、``Agent.invoke_subagent`` 的 SUBAGENT 交付段——后两处
    自行加标注块后产 EVENT / SUBAGENT 消息。

    .. rubric:: 行为要点

    - 纯同步、无 I/O——原料 → 块的转换已在 `normalize_output` 完成。
    - 形态映射：``None`` → 空列表；``str`` → ``[TextBlock(v)]``；标量
      （``bool`` / ``int`` / ``float``）→ ``[TextBlock(json.dumps(v))]``
      （可解析回）；``dict`` / dataclass / ``BaseModel`` / 纯基础
      ``list`` / ``tuple`` → ``[StructBlock(data)]``；单块 → ``[block]``；
      混合 list → 逐成员保序（``str`` 原样、标量 dumps、``dict`` /
      ``list`` 转 ``StructBlock``、块透传）。
    - ``error`` 非 ``None`` 时在末尾追加 ``TextBlock(error)``。
    - 前置：``output`` 已是五形态之一（出 ``Agent.tool_call`` 恒成立）；
      深层埋藏的非 JSON 对象在本函数内 ``StructBlock`` 构造校验失败 →
      ``ValueError`` （框架错误通道，直接上抛）。

    .. seealso::

        - :func:`flowing.tool.normalize_output` —— 上游归一化。
        - :meth:`flowing.tool.ToolResult.as_message` —— 消费方一。
    """
    # output 五形态 → content 块列表（纯同步，无 I/O）：
    #   None            → []
    #   str             → [TextBlock(v)]
    #   标量            → [TextBlock(json.dumps(v))]   # "true"/42，可解析回
    #   dict / dataclass / BaseModel / 纯基础 list / tuple
    #                   → [StructBlock(data)]           # dataclass 此刻序列化
    #   单块            → [block]
    #   混合 list       → 逐成员：标量→TextBlock(dumps)、dict/list→StructBlock、
    #                     块→透传（保序）
    # error 非 None 时末尾追加 TextBlock(error)
    def _member_to_block(v: Any) -> ContentBlock:
        if isinstance(v, ContentBlock):
            return v   # 块透传（保序）
        if isinstance(v, str):
            return TextBlock(text=v)   # str 原样
        if isinstance(v, (bool, int, float)) or v is None:
            return TextBlock(text=json.dumps(v, ensure_ascii=False))   # 标量 dumps（可解析回）
        if isinstance(v, BaseModel):
            return StructBlock(data=v.model_dump())
        if is_dataclass(v) and not isinstance(v, type):
            return StructBlock(data=asdict(v))   # dataclass 此刻序列化
        # dict / 纯基础 list / tuple → StructBlock（JSON 校验在 StructBlock 构造点，
        # 深层埋藏非 JSON 对象在此诚实失败）
        return StructBlock(data=v)

    blocks: list[ContentBlock] = []
    if output is None:
        blocks = []
    elif isinstance(output, (list, tuple)):
        if any(isinstance(v, ContentBlock) for v in output):
            # 混合 list：逐成员（标量→TextBlock(dumps)、dict/list→StructBlock、
            # 块→透传，保序）
            blocks = [_member_to_block(v) for v in output]
        else:
            # 纯基础 list / tuple → 整体一个 StructBlock（JSON 校验在构造点）
            blocks = [StructBlock(data=list(output))]
    else:
        blocks = [_member_to_block(output)]
    if error is not None:
        blocks.append(TextBlock(text=error))   # error 仅 error 态非 None，末尾追加
    return blocks


