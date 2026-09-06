"""``flowing.tool`` —— 工具子系统：能力三正交中的 Tool 维度。

.. rubric:: 功能介绍

本模块定义工具子系统的全部公开类型：可执行对象（:class:`Tool` 基类、
:class:`ScriptTool` 作者基类，以及三种按声明驱动的具体实现
:class:`McpTool` / :class:`CliTool` / :class:`RequestTool`）、LLM 可见
声明（:class:`ToolDefinition`）、Agent 级绑定（:class:`ToolEntry`）、
调用与结果（:class:`ToolCall` / :class:`ToolResult`，状态四值见
:data:`ToolStatus`）、全局注册表（:class:`ToolRegistry`）、工具类型
判别值（:data:`ToolType`）、打标装饰器（:func:`flowing_tool`），以及
结果归一与媒体族（:func:`normalize_output` / :func:`output_to_blocks`、
媒体载体 :class:`Image` / :class:`File` / :class:`Audio` /
:class:`Video`、转换器 :class:`MediaConverter` /
:func:`register_media_converter`）。

框架自带的出厂内置工具（核心 ``SubagentInvokeTool`` 与标准件
``FinishTool`` 等）定义在 :mod:`flowing.builtins`，不在本模块。

能力三正交（Tool / 子 Agent / Skill 共享同一模式，三个维度各自独立
演化、互不侵入）：

.. list-table::
   :header-rows: 1

   * - 维度
     - Tool
     - 子 Agent
     - Skill
   * - 可执行对象
     - :class:`Tool` （``execute()``）
     - Agent 类
     - Skill 内容
   * - LLM 可见声明
     - :class:`ToolDefinition`
     - catalog XML 条目
     - catalog XML 条目
   * - Agent 级绑定
     - :class:`ToolEntry`
     - ``SubagentEntry``
     - ``SkillEntry`` （扩展）

绑定层独立存在的理由：同一个工具在不同 Agent 上可以呈现不同的 LLM
视图（如 ``FinishTool`` 在 ReviewerAgent 上暴露 ``score`` / ``pass`` /
``issues``，在 PaymentAgent 上暴露 ``transaction_id`` / ``status`` /
``charged_amount``）——通过 `ToolEntry` 覆写实现，不改全局注册表、
不改工具本身。

本模块全部公开签名属跨版本稳定契约；``_`` 前缀符号为内部 API，不属
稳定契约。

.. rubric:: 全局约定（跨符号、影响使用的约定）

工具调用时序：LLM 在一次逻辑 Turn 中发出工具调用后，框架按固定顺序
处理。``Agent.tool_call`` 先 dispatch ``before_tool_call`` 钩子
（handler 可改写 `ToolCall` 的参数；把 `ToolResult` 放进 ``shortcut``
字段可跳过工具执行；``raise Intercepted`` 硬阻断，工具不执行、结果
为 ``blocked``），随后仅按别名查找 Agent 的工具绑定表（找不到抛
``UnknownToolError``）、聚合参数，经 ``Tool.__call__`` 执行，最后
dispatch ``after_tool_call`` （可改写结果）并在返回前对结果做一次
幂等归一（已归一的值重复归一结果不变）。完整时序见 :mod:`flowing.agent`，钩子语义见 :mod:`flowing.hooks`。

``builtin::`` 命名空间：出厂内置工具注册在 ``builtin::`` 命名空间下。
裸名查找先查 ``default::`` 再查 ``builtin::``——插件 / 应用可以在
``default::`` 注册同名工具覆盖内置行为，被覆盖的条目仍可用
``builtin::xxx`` 全限定名显式引用。注册不等于可见：Agent 的工具目录
只包含它在 ``.fya`` 或 ``add_tool`` 中显式声明的条目；可写文件、
执行命令等危险工具必须由使用者显式声明，框架不会因为注册就把它们
暴露给 LLM。

参数优先级：指定值（``specified``，含注入表达式）最高，其次 LLM
传入参数，最后 schema 默认值。指定值对 LLM 不可见、不可被 LLM 覆盖
——固定值或宿主上下文注入的参数（``user_id`` / ``trace_id`` 等）用
指定值声明，这是防篡改的安全边界。注入表达式
``{{ self.inject('key') }}`` 在调用时以调用方 Agent 为上下文求值，
沿 provide-inject 链向根查找；链上找不到提供者抛
``MissingProvideError``。

工具业务错误是正常产物：``execute`` 抛普通异常 →
``ToolResult(status="error")``，LLM 可见、不触发任何错误钩子；``execute`` 内
``raise Intercepted`` → ``ToolResult.blocked``。参数 / 返回值中的
编程错误（参数缺类型标注、返回值含违禁块、返回不可转换对象等）
走框架错误通道直接上抛（``ValueError`` / ``FormatError`` 等），不包
成 LLM 可见结果。

审批等策略在 ``before_tool_call`` handler 内实现：``requires_approval``
之类的字段只是 ``.fya`` 的非保留字段，原样成为工具对象的普通属性，
框架不解析、不据此做任何自动行为。handler 内 ``await`` 审批——通过
返回原值、改参数返回改写后的 `ToolCall`、拒绝 ``raise Intercepted``。

路径约定：工具定义文件（``.fya`` / ``.py``）的定位、``@/`` 项目根
锚定与命名空间派生规则见 :meth:`ToolRegistry.get`；``@/`` 上下文按
asyncio Task 隔离（见 :mod:`flowing.runtime`）。

.. rubric:: 使用示例

.. code-block:: python

    from flowing import ScriptTool
    from pydantic import BaseModel

    class PayArgs(BaseModel):
        order_id: str
        amount: float

    class MakePayment(ScriptTool):
        \"\"\"对指定订单发起支付。仅在用户明确确认支付意图后调用。\"\"\"

        name = "make-payment"      # 必填：不再由类名推断
        args_model = PayArgs

        async def execute(self, *, order_id: str, amount: float) -> dict:
            return {"tx": "fake", "order_id": order_id, "amount": amount}

    tool = MakePayment()
    result = await tool({"order_id": "o1", "amount": 9.9})
    assert result.status == "completed"

.. seealso::

    - :mod:`flowing.agent` —— ``Agent.tool_call`` 的完整工具调用时序。
    - :mod:`flowing.hooks` —— ``before_tool_call`` / ``after_tool_call``
      钩子语义。
    - :mod:`flowing.builtins` —— 出厂内置工具。
    - :mod:`flowing.params` —— 参数声明与 schema 桥接。
    - :mod:`flowing.message` —— 工具调用与结果的落树形态。
"""

from __future__ import annotations   # 注解延迟求值，配合 TYPE_CHECKING 破注解级循环边

import asyncio
import base64
import binascii
import hashlib
import inspect
import json
import logging
import mimetypes
import os
import shlex
import uuid
import warnings
from collections.abc import Callable, Iterator
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass, field, is_dataclass
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Literal

import jinja2
import jinja2.meta

from flowing.errors import (
    AmbiguousMcpSourceError,
    AmbiguousToolError,
    FormatError,
    Intercepted,
    MissingFieldError,
    MissingMcpSourceError,
    MissingSchemaError,
    NameMismatchError,
    ToolNameConflictError,
    ToolNotFoundError,
)
from flowing.message import (
    AudioBlock,
    ContentBlock,
    FileBlock,
    ImageBlock,
    MediaBlock,
    Message,
    MessageKind,
    MessagePriority,
    StructBlock,
    TextBlock,
    ThinkingBlock,
    ToolCallBlock,
    VideoBlock,
)
from pydantic import BaseModel, ValidationError

from flowing.params import (
    _coerce,
    apply_param_overrides,
    bridge_properties,
    expand_args_schema,
    schema_to_model,
)
from flowing.parsable import Parsable
from flowing.paths import (
    NamingRules,
    classify_ref,
    infer_name,
    kebab_to_pascal,
    kebab_to_snake,
    pascal_to_kebab,
    probe_candidates,
    resolve_path,
    to_project_path,
)

if TYPE_CHECKING:
    from flowing.agent import Agent, Execution
    from flowing.runtime import Runtime

_logger = logging.getLogger(__name__)

__all__ = [
    "Tool",
    "ScriptTool",
    "McpTool",
    "CliTool",
    "RequestTool",
    "ToolDefinition",
    "ToolEntry",
    "ToolCall",
    "ToolResult",
    "ToolRegistry",
    "ToolStatus",
    "ToolType",
    "Image",
    "File",
    "Audio",
    "Video",
    "MediaConverter",
    "register_media_converter",
    "normalize_output",
    "output_to_blocks",
    "flowing_tool",
]

ToolStatus = Literal["completed", "pending", "blocked", "error"]
"""工具执行结果的状态四值。

- ``"completed"``：正常完成，返回值承载在 ``output`` 字段。
- ``"pending"``：异步收据。三种形态产生：``execute`` 返回
  ``asyncio.Task``；``execute`` 是 async generator（首个 ``yield``
  即收据内容）；``background = True`` 标记的普通 async ``execute``。
  框架只回收据，真正结果稍后以独立消息到达（不阻塞逻辑 Turn）。
- ``"blocked"``：被钩子硬阻断的产物——``before_tool_call`` 拦截
  （工具未执行）／ ``after_tool_call`` 拦截（工具已执行完、结果被丢弃）
  ／ ``execute`` 内 ``raise Intercepted`` （执行被中断于中途）；只能经
  ``ToolResult.blocked`` 工厂产生。
- ``"error"``：执行抛普通异常的正常产物——LLM 可见、不触发任何错误
  钩子（核心错误钩子仅 ``on_provider_error``，见 :mod:`flowing.hooks`）。
"""

ToolType = Literal["script", "mcp", "cli", "request"]
"""四种工具类型判别值（``.fya`` 的 ``type:`` 字段取值）。

- ``"script"``：Python callable（`ScriptTool` 子类 / 裸函数 /
  ``callable:`` 指针指向），全局单例注册——所有 Agent 共享同一实例。
- ``"mcp"``：MCP 服务器（本地 stdio 进程或远程端点），每个声明
  独立实例。
- ``"cli"``：命令行工具（Jinja2 命令模板），每个声明独立实例。
- ``"request"``：HTTP/HTTPS 请求工具，每个声明独立实例。

script 是单例是因为其业务逻辑是用户代码，不应实例化多次；其余三类
只是参数化配置，没有用户代码。
"""


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

    塑形只有这一处实现，全部消费方走同一条代码路径：`ToolResult.as_message`
    （内部调本函数）、异步工具完成回调（``add_done_callback`` 固定
    watcher）、cron 的 ``default_tool_executor``——后两处自行加标注块后
    产 EVENT 消息。

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


@dataclass
class ToolCall:
    """LLM 发出的单次工具调用——``before_tool_call`` 钩子的 value 类型。

    .. rubric:: 功能介绍

    `ToolCall` 是「LLM 想调什么」的纯数据载体：从 PROVIDER 消息的工具调用
    块（``ToolCallBlock``）解析而来，经 ``before_tool_call`` 钩子链传递，
    最终被解包为零散参数喂给 ``Tool.execute()``。`ToolCall` 只是调用意图，
    不是可执行对象；``execute()`` 不接收 `ToolCall` 整体入参。

    .. rubric:: 使用示例

    ``before_tool_call`` handler 统一签名 ``(agent, value)``——方法形态
    ``(self, tool_call)`` 的 ``self`` 即承载 Agent：

    .. code-block:: python

        from flowing import Intercepted, ToolResult, on

        @on("before_tool_call")
        async def _approve(self, tool_call):
            tool_call.args["request_id"] = self.node_id   # 改写参数
            if tool_call.name == "delete-file":
                raise Intercepted("删除文件需要人工审批")  # 硬阻断 → blocked
            if (hit := self._cache.get(tool_call.name)):
                tool_call.shortcut = ToolResult(           # 短路：跳过执行
                    status="completed", output=hit)
            return tool_call

    .. rubric:: 行为要点

    - handler 三种合法出口：返回（可能改写的）`ToolCall` / 把
      `ToolResult` 放进 ``shortcut`` 字段短路 / ``raise Intercepted``
      硬阻断；普通异常直接上抛，没有会捕获它的兜底钩子。
    - `ToolCall` 不做参数校验、不认识 specified——参数聚合全部发生在
      其后的 ``_normalize()`` （见 :mod:`flowing.agent`）。
    - ``args`` 只包含 LLM 原始传入值（键可能是别名）；hook 改写后出现的
      同名键会在 ``resolve()`` 中被 specified 覆盖（优先级见模块
      docstring 的参数优先级约定）。

    .. seealso::

        - :class:`flowing.tool.ToolResult` —— 调用的结果载体。
        - :class:`flowing.tool.ToolEntry` —— ``resolve()`` 的参数聚合。
        - :mod:`flowing.hooks` —— dispatch 算法与 ``shortcut`` 契约。
    """

    id: str
    """Provider 侧的调用 ID（如 OpenAI ``tool_call_id``）。它是与结果侧
    ``kind=TOOL`` 消息 ``tool_call_id`` 字段严格配对的依据，也是恢复时
    扫描孤立 tool_call 的匹配键。编程路径（cron / workflow / 手动构造）
    由调用方生成合成字符串——推荐形如 ``<来源类型名>-<uuid4>``
    （workflow → ``workflow-…``、cron → ``cron-…``）的有语义前缀，亦
    接受无语义的 uuid；仅作追踪，不参与配对。
    """
    name: str
    """LLM 看到的工具名——即 `ToolEntry.name_alias` （别名），不是规范名。
    ``Agent.tool_call()`` 仅按此名查找工具绑定表，不回退规范名。
    """
    args: dict[str, Any]
    """LLM 原始传入的参数字典（键为 LLM 可见名，可能是 ``param_aliases``
    中的别名）。``before_tool_call`` handler 可直接改写本字典；别名 → 规范名
    的映射、specified 聚合在 ``_normalize()`` 中发生，不在本对象上。
    """
    shortcut: "ToolResult | None" = None
    """钩子短路字段：初值 ``None``，钩子链以「非 ``None`` 即短路」门控。
    handler 把一个 `ToolResult` 放进本字段后返回——钩子链停止、工具默认
    执行被替代（该 `ToolResult` 直接作为本次调用的结果，典型场景：缓存
    命中跳过工具执行），``after_tool_call`` 照常触发。与 ``raise
    Intercepted`` 的区别：shortcut 是正常收场、结果由 handler 提供，
    Intercepted 是硬阻断、结果为 ``blocked``、``after_tool_call`` 不触发。
    shortcut 产物不经 ``Tool.__call__``，其 ``output`` 可为原料形态——
    由 ``Agent.tool_call`` 收尾的 `normalize_output` 幂等归一。
    """

    @classmethod
    def from_block(cls, block: ToolCallBlock) -> "ToolCall":
        """从消息层 `ToolCallBlock` 解析为 `ToolCall` （唯一官方转换点）。

        .. rubric:: 功能介绍

        剥离 ContentBlock 的通用字段（``type``），产出 ``ToolCall(id,
        name, args, shortcut=None)``。逻辑 Turn 循环遍历响应 content 的
        tool_call block 时，经本方法提取后传给 ``Agent.tool_call()``。

        .. rubric:: 行为要点

        - 纯函数：不修改入参 block，不产生副作用。
        - 返回值的 ``shortcut`` 恒为 ``None`` （短路是钩子链运行期状态，
          不来自消息）。

        .. seealso::

            - :class:`flowing.message.ToolCallBlock` —— 上游（消息层）。
            - :meth:`flowing.tool.ToolResult.as_message` —— 反向转换：
              结果 → 消息。
        """
        # shortcut 恒为 None 初值（短路是钩子链运行期状态，不来自消息）
        return cls(id=block.id, name=block.name, args=block.args)


@dataclass
class ToolResult:
    """工具执行结果——LLM 可见的调用回执。

    .. rubric:: 功能介绍

    `ToolResult` 是工具调用链的最终产物：无论成功、失败、异步还是被
    阻断，对 LLM 与消息树都呈现为同一个结构。结果字段为 ``status`` /
    ``output`` / ``error`` 三者；``output`` 是唯一结果字段，构造期收
    原料（基础值 / 载体四类 / 裸 ``bytes`` / ``Path`` / 第三方库对象
    均可，经 `normalize_output` 归一）。

    .. rubric:: 使用示例

    工具作者通常不直接构造 `ToolResult`——框架自动包装：

    .. code-block:: python

        async def execute(self, *, order_id: str, caller: Agent) -> dict:
            return {"tx": "abc"}      # → ToolResult(status="completed",
                                      #              output={"tx": "abc"})

        async def execute(self, *, url: str):
            return [f"已截取 {url}", Image(path=png_path)]   # 媒体返回

    .. rubric:: 行为要点

    - ``status="error"`` 是正常产物而非异常：LLM 应当看到工具失败并自行
      决策（重表述、换工具、向用户报告），因此 error 结果不触发任何错误
      钩子——核心错误钩子仅 ``on_provider_error`` （机制 vs 策略）。
    - ``status="blocked"`` 与 ``"error"`` 区分：blocked 表示工具根本没
      执行（被审批 / 守卫阻断），error 表示执行了但失败；二者对 LLM 的
      语义与审计含义不同，不可合并。``status == "blocked"`` 的实例只能
      经 `ToolResult.blocked` 工厂产生——来源：``before_tool_call`` /
      ``after_tool_call`` 拦截（工具未执行或结果被丢弃），或 ``execute``
      内主动抛出 ``Intercepted`` （执行被硬阻断于中途）。
    - ``pending`` 承载异步边界（三种形态：返回 ``asyncio.Task`` /
      async generator 首 yield / ``background`` 标记）：框架只回收据，
      不阻塞逻辑 Turn 等待异步任务。
    - 出 ``Agent.tool_call`` 的 ``output`` 恒为五形态之一——``None`` /
      基础值 / 单块 / 纯基础 list / 混合 list（其中块只有 ``TextBlock`` /
      ``StructBlock`` / ``MediaBlock``）；归一点 = `Tool.__call__` +
      ``Agent.tool_call`` 收尾（幂等再归一，封 shortcut / 钩子改写两缝）。
    - 配对元数据（``tool_call_id`` / ``tool_status``）在消息层，不在本
      对象上（见 `as_message`）。
    - `ToolResult` 不携带 trace 等观测数据——观测走钩子与快照层，不进
      LLM 可见结构。例外：``duration`` 是预留字段（当前实现不写入，恒为
      ``None``），即使有值也不进入 ``as_message`` 产物（LLM 不可见）。
    - 边缘情况：深层埋藏的非 JSON 对象归一化期放行，`as_message` 塑形时
      ``StructBlock`` 构造校验失败 → ``ValueError`` （诚实失败点）。

    .. seealso::

        - :class:`flowing.tool.ToolCall` —— 调用意图载体。
        - :func:`flowing.tool.normalize_output` —— 归一化（幂等）。
        - :func:`flowing.tool.output_to_blocks` —— 塑形统一出口。
        - :meth:`flowing.tool.Tool.__call__` —— 自动包装的调度层。
        - :mod:`flowing.errors` —— ``Intercepted`` 与普通异常的边界。
    """

    status: ToolStatus
    """执行状态四值之一，见 `ToolStatus`。
    """
    output: Any = None
    """唯一结果字段。构造期收原料；经 `normalize_output` 归一后、出
    ``Agent.tool_call`` 恒为五形态之一。``completed`` 时承载返回值；
    ``pending`` 时为 ``None`` （收据）；``error`` 时可为 ``None``；
    ``blocked`` 时为 ``None`` 或阻断原因 str。
    """
    error: str | None = None
    """错误描述，仅 ``status == "error"`` 时有值。对 LLM 可见（LLM 应能
    据此自我纠正）；不触发错误钩子。
    """
    duration: float | None = None
    """执行时长（秒）——预留字段，当前实现不写入（恒为 ``None``）。即使
    有值也不进入 ``as_message`` 产物（LLM 不可见）。
    """
    background_task_id: str | None = None
    """后台任务注册键：``status="pending"`` 且经
    `Agent.track_background_task` 注册时非 ``None``；其余状态恒
    ``None``。调用方 / LLM 可据此按 id 取消或查询后台任务。
    """

    @classmethod
    def blocked(cls, reason: str | None = None) -> "ToolResult":
        """构造「被钩子硬阻断」的结果（``status="blocked"`` 的唯一来源）。

        .. rubric:: 功能介绍

        当 ``before_tool_call`` / ``after_tool_call`` 链中任一 handler
        ``raise Intercepted``，或 ``execute()`` 内部主动抛出
        ``Intercepted`` 时，框架捕获 ``Intercepted`` 并调用本工厂生成
        阻断结果：``before_tool_call`` 拦截时工具未执行；``after_tool_call``
        拦截时工具已执行完、结果被丢弃；``execute`` 内拦截时执行被中断
        于中途。统一由本工厂生成，保证所有阻断路径的产物结构一致（LLM
        可据此向用户说明「该操作被拦截」而不是「执行失败」）。

        :param reason: 阻断原因（通常取 ``Intercepted`` 的消息），LLM 可见。
        :return: ``status="blocked"``、``error`` 为 ``None`` 的 `ToolResult`。

        .. rubric:: 行为要点

        - 后置条件：返回实例满足 ``status == "blocked"`` 且 ``error is
          None``；``reason`` 非空时作为 ``output`` （str 基础值），
          ``as_message`` 塑形为 ``[TextBlock(reason)]``——LLM 可见形态即
          文本块。
        - 本工厂不记录审计日志——审计由 handler 或快照层负责。

        .. seealso::

            - :class:`flowing.errors.Intercepted` —— 触发本工厂的阻断信号异常。
        """
        return cls(status="blocked", output=reason if reason else None, error=None)

    def as_message(self, tool_call_id: str, *, source: str | None = None) -> Message:
        """把本结果塑形为 ``kind=TOOL`` 的消息，供落消息级树。

        .. rubric:: 功能介绍

        逻辑 Turn 收尾时，框架将每次工具调用的 `ToolResult` 经本方法转为
        一条 ``Message(kind=TOOL, ...)``，以 ``parent_id`` 链入消息级树
        并在消息完整后 append 落盘。配对元数据上移到消息字段
        （``tool_call_id`` / ``tool_status``），``content`` 只含纯内容块
        ——塑形（五形态 → content 块列表）委托模块级统一出口
        `output_to_blocks`，与异步完成回调、cron 消费同一实现。

        ``tool_call_id`` 在本方法接线：`ToolResult` 本体不携带调用 id——
        工具执行层（``Tool.__call__``）从未见过 ``ToolCall`` 对象，全库
        唯一同时持有两者的点是 ``Agent.tool_call`` 管线，由它在收尾转换
        时传入 ``tool_call.id``，完成与 ``ToolCallBlock.id`` 的严格配对。

        :param tool_call_id: 配对的目标调用 id（``ToolCall.id`` /
          ``ToolCallBlock.id``）；编程路径（cron / workflow 构造的
          ToolCall）同样经此接线，配对不断链。
        :param source: 消息 ``source`` 字段；缺省填 ``"tool_result"``
          （与异步工具结果 EVENT 消息的既有约定同值；工具身份经
          ``tool_call_id`` 配对反查，不由 source 携带）。
        :return: ``kind=TOOL``、``tool_call_id`` / ``tool_status`` 接线、
          ``content`` 为 `output_to_blocks` 产物（纯内容块，无协议块）的
          新 `Message`；``synthetic=False``、``turn_end=False``。

        .. rubric:: 行为要点

        - 前置：``self.output`` 已是五形态之一（出 ``Agent.tool_call`` 恒
          成立）；深层埋藏的非 JSON 对象在塑形时 ``StructBlock`` 构造
          校验失败 → ``ValueError`` （框架错误通道）。
        - ``ToolResult.error`` 仅 ``status="error"`` 时非 ``None``，本方法
          无需自行判断，直接透传给 `output_to_blocks` （末尾追加
          ``TextBlock(error)``）。
        - 本方法不负责 append 落盘与 ``parent_id`` 接线——那是
          ``Agent._append_message`` 的职责；本方法只产出未接线的 `Message`。
        - 边缘情况：``status="pending"`` 也产生消息（收据消息）——返回
          Task 路径 ``output=None`` → ``content=[]`` （空 tool_result 的
          API 层兜底属 adapter 职责）；async gen 路径 ``output=首 yield``
          → content 带内容，并（注册键非 ``None`` 时）末尾附加「后台任务
          ID」文本块（不动作者 yield 的内容）；异步任务真正完成时的结果
          由框架另行产生多块 EVENT 消息，与本收据互不覆盖。

        .. seealso::

            - :func:`flowing.tool.output_to_blocks` —— 塑形统一出口。
            - :class:`flowing.message.Message` —— 消息级树的节点结构。
            - :meth:`flowing.agent.Agent._append_message` —— 落树时序。
        """
        # 塑形统一出口 output_to_blocks；配对元数据（tool_call_id /
        # tool_status）在消息字段，content 只含纯内容块
        blocks = output_to_blocks(self.output, error=self.error)  # error 仅 error 态非 None，直接透传
        if self.status == "pending" and self.background_task_id:
            # pending 收据附加「后台任务 ID」块（不动作者 yield 的内容——
            # 附加块而非并入，避免污染作者数据；LLM/调用方可据此按 id 引用）
            blocks = [*blocks,
                      TextBlock(text=f"background task ID: {self.background_task_id}")]
        return Message(
            kind=MessageKind.TOOL,
            tool_call_id=tool_call_id,   # 由 Agent.tool_call 管线接线（ToolCall.id），见 docstring
            tool_status=self.status,
            content=blocks,
            # source 缺省 "tool_result"（与异步 EVENT 结果同值）
            source=source if source is not None else "tool_result",
            synthetic=False,
            turn_end=False,
        )


@dataclass
class ToolDefinition:
    """LLM 可见的工具声明——可序列化的纯数据，不含任何执行逻辑。

    .. rubric:: 功能介绍

    三正交中的「LLM 可见声明」层：字段为 ``name`` / ``description`` /
    ``params_schema`` / ``output_schema`` / ``strict``。它出现在
    ``Context.tools`` 中，是 Provider adapter 组装各家 function-calling
    schema 的唯一来源（adapter 为白名单语义——只取 ``name`` /
    ``description`` / ``params`` 等已知字段构造，多带的字段天然不会被
    映射）。

    工具作者通常不直接构造本类（`ScriptTool` 类属性声明 + 框架自动生成
    是主路径）；本类独立存在的理由是：同一工具在不同 Agent 上需要不同
    LLM 视图（见 `ToolEntry`），视图必须是可自由复制、覆写的纯数据，
    不能与执行对象纠缠。

    .. rubric:: 使用示例

    .. code-block:: python

        definition = ToolDefinition(
            name="finish",
            description="结束当前任务并返回结构化结果。",
            params_schema={                       # JSON Schema properties（展开式）
                "summary": {"type": "string", "default": "",
                            "description": "任务执行的简短摘要"},
            },
        )

    .. rubric:: 行为要点

    - 不变量：可 JSON 序列化（``params_schema`` 即 JSON Schema
      properties dict）；不得持有 callable、连接等运行时对象。
    - 本类不做参数校验——LLM 视角校验在 ``Agent._normalize``、内部校验
      在 `Tool.__call__` （校验模型于工具创建时编译）。
    - ``strict`` 语义：``True`` （默认）时工具层按 ``params_schema``
      严格约束 LLM 入参——LLM 传出未定义参数即校验失败；``False`` 时
      工具层不限制参数（未知键原样放行，用于「参数由下游自行校验」的
      工具）。

    .. seealso::

        - :meth:`flowing.tool.ToolDefinition.clone_with_overrides` —— 覆写
          生成新声明的唯一机制。
        - :class:`flowing.tool.ToolEntry` —— 覆写数据的持有者。
        - :mod:`flowing.params` —— 声明层规则与桥接子集。
    """

    name: str
    """规范工具名（kebab-case）。LLM 看到的名字以 `ToolEntry.name_alias`
    为准——本字段经 ``clone_with_overrides`` 覆写后才对 LLM 生效。
    """
    description: str
    """给 LLM 的工具说明（已是 Parsable 渲染后的文本）。
    """
    params_schema: dict[str, dict[str, Any]] = field(default_factory=dict)
    """参数声明表，键为规范参数名、值为 JSON Schema property dict；
    参数是否必填由有无 ``default`` 派生（有 ``default`` → 可选）。
    来源两条：Python 层 ``BaseModel`` 子类经 ``model_json_schema()``
    派生，``.fya`` 层展开式声明经 :func:`flowing.params.expand_args_schema`
    归一化；执行层校验模型经 :func:`flowing.params.schema_to_model`
    桥接。``default`` 只接受 JSON 兼容类型——复杂对象（DB 连接、HTTP
    客户端）走标识符引用 + ``caller`` 获取。
    """
    output_schema: dict[str, Any] | None = None
    """返回值结构声明（JSON Schema 片段）；``None`` 表示不约束。

    来源：工具 ``.fya`` 的 ``output:`` 字段；MCP 服务器的
    ``outputSchema`` 自动填入本字段。用途：随 ``llm_definition()`` 产物
    携带（adapter 白名单取用，不映射进各家 function-calling schema）；
    `RequestTool` 执行时按 ``properties`` 从 JSON 响应提取字段。当前
    实现不据此做结果校验（MCP 的 ``outputSchema`` 同样只存储不校验）。
    Agent 侧 ``output:`` 覆写并入 `ToolEntry.override_params` （以字段名为
    键进 LLM 视图的参数表），不落本字段。
    """
    strict: bool = True
    """是否在工具层严格约束 LLM 入参；``False`` 用于「参数由下游自行校验」
    的工具。
    """

    def clone_with_overrides(
        self,
        name: str | None = None,
        override_params: dict[str, dict[str, Any]] | None = None,
        override_description: str | None = None,
        *,
        specified_params: set[str] | None = None,
    ) -> "ToolDefinition":
        """生成覆写后的新 `ToolDefinition`，原始对象不变。

        .. rubric:: 功能介绍

        `ToolEntry.llm_definition()` 的唯一覆写机制：别名、参数局部覆写、
        描述覆写、隐藏参数移除，全部经本方法一次性完成。「返回新对象，
        原始不变」是绑定层不污染全局注册表的结构性保证——`ToolRegistry`
        中的定义永不被 Agent 级覆写修改。

        :param name: 新名字（通常传 `ToolEntry.name_alias`）；``None`` 保持
          原名。
        :param override_params: ``{规范参数名: {JSON Schema 关键字: 新值}}``
          稀疏补丁——只覆写出现的关键字，未出现的参数与关键字保持原值
          （深合并回填）。补丁应用委托
          :func:`flowing.params.apply_param_overrides`——关键字超出桥接
          子集时抛 :class:`flowing.errors.FormatError` （声明笔误
          fail-fast，即尽早报错、不静默容忍）。requiredness 不主动
          推断：没写 ``default`` 沿用
          基底；显式给 ``default`` 变可选。
        :param override_description: 描述覆写；``None`` 保持原描述。
        :param specified_params: 要从 LLM 视图中移除的参数名集合（调用方
          传入 ``set(specified.keys())``——注入表达式也是 specified 的
          一种值形态）。
        :return: 新的 `ToolDefinition`；``self`` 不被修改。

        .. rubric:: 行为要点

        - 后置条件：返回值与 ``self`` 是不同对象；``self.params_schema``
          内容不变。
        - 边缘情况：``override_params`` 中出现 ``params_schema`` 不存在
          的键 → 视为新增参数（`FinishTool` 动态 schema 即依赖此语义）；
          ``specified_params`` 中出现不存在的键 → 静默忽略。
        - 本方法不做参数别名应用（``param_aliases`` 的改名由
          `ToolEntry.llm_definition()` 第 4 步在返回值上完成）。

        .. seealso::

            - :meth:`flowing.tool.ToolEntry.llm_definition` —— 本方法的
              唯一框架调用点。
        """
        params: dict[str, dict[str, Any]] = {k: dict(v) for k, v in self.params_schema.items()}
        if specified_params:
            for key in specified_params:
                params.pop(key, None)  # 不存在的键静默忽略（行为要点）；
            # specified 参数由 specified 值兜底，requiredness 无需维护
        if override_params:
            params = apply_param_overrides(params, override_params)
            # 非法关键字 fail-fast / 未知键新增参数 / 稀疏回填，语义见该函数
        return ToolDefinition(
            name=name or self.name,
            description=override_description or self.description,
            params_schema=params,
            output_schema=self.output_schema,  # 透传：覆写不触及
            strict=self.strict,
        )


def _whole_doc(doc: str | None) -> str | None:
    """docstring 整体提取（0904 description 回退口径）：``inspect.cleandoc``
    全文、去首尾空白；无内容 → ``None``。内部 API。"""
    if not doc:
        return None
    return inspect.cleandoc(doc).strip() or None


def _first_paragraph(doc: str | None) -> str | None:
    """docstring 首段提取（其它内部用途）：cleandoc 后按空行切首段，
    段内换行折叠为空格；无内容 → ``None``。内部 API。"""
    if not doc:
        return None
    paragraph = inspect.cleandoc(doc).split("\n\n", 1)[0].strip()
    return " ".join(paragraph.splitlines()) or None


def _apply_param_aliases(
    definition: ToolDefinition,
    param_aliases: dict[str, str],
) -> ToolDefinition:
    """把 LLM 可见 schema 的参数名从规范名改为别名（`ToolEntry.llm_definition`
    第 4 步的唯一可调用物）。内部 API，不属稳定契约。

    .. rubric:: 行为要点

    - ``param_aliases`` 方向为 ``LLM 别名 → 规范名``；本函数对
      ``params_schema`` 键做反向改名——仅改名，property 内容原样，
      其余字段（name/description/output_schema/strict）透传。
    - 撞名（改名结果与既有键撞车，含两个规范名经别名映射到同一名称）→
      :class:`flowing.errors.FormatError` （绑定声明笔误，fail-fast）；
      不反向查重（``param_aliases`` 自身的别名重复不在此校验）。
    - 映射到 schema 中不存在的规范名 → 静默跳过；``param_aliases`` 为空
      时原样返回入参（不复制）。
    """
    if not param_aliases:
        return definition
    reverse = {canonical: alias for alias, canonical in param_aliases.items()}
    renamed: dict[str, dict[str, Any]] = {}
    for key, prop in definition.params_schema.items():
        new_key = reverse.get(key, key)
        if new_key in renamed:
            raise FormatError(
                f"parameter alias collision after mapping: {new_key!r} (param_aliases conflict with existing parameters)")
        renamed[new_key] = prop
    return ToolDefinition(
        name=definition.name,
        description=definition.description,
        params_schema=renamed,
        output_schema=definition.output_schema,
        strict=definition.strict,
    )


@dataclass
class ToolEntry:
    """Agent 对工具的一次「用法声明」——三正交中的 Agent 级绑定层。

    .. rubric:: 功能介绍

    `ToolEntry` 回答「这个 Agent 如何使用这个 Tool」：LLM 看到的别名、
    参数覆写、指定值、参数别名。每个 Agent 实例持有自己的 entry 集合，
    互不共享——同一工具在不同 Agent 上可以呈现不同的 LLM 视图（如
    ``FinishTool`` 在不同 Agent 上的不同 schema），覆写发生在 Agent 级
    绑定层，而不是全局注册表。

    .. rubric:: 使用示例

    .. code-block:: python

        from flowing import ToolEntry
        from flowing.parsable import Parsable

        entry = ToolEntry(
            name_alias="pay",
            name_ori="make-payment",
            override_params={            # JSON Schema 稀疏补丁（只写要改的字段）
                "amount": {"description": "支付金额（元），上限 50000"},
                "currency": {"default": "USD"},
            },
            specified={
                # 固定值与注入表达式都是 specified
                "currency": Parsable("USD"),
                "user_id": Parsable("{{ self.inject('user_id') }}"),
            },
        )

    ``.fya`` 等价声明（``agent.fya`` 的 ``tools:`` 条目）：

    .. code-block:: yaml

        tools:
          - make-payment as pay:
              description: "发起支付"
              args:
                amount: {description: "支付金额（元），上限 50000"}  # 稀疏补丁
                currency: USD                  # 裸值 → specified（固定值）
                user_id: "{{ self.inject('user_id') }}"   # 注入表达式 → specified
                working_dir as cwd: _          # as 改名 + 空补丁（_ 语义见下）
        ---
        $tools.pay.args.cwd.description:      # 深层块：向空补丁逐字段写入
          本订单的工作目录

    .. rubric:: 行为要点

    - 装配判别（``.fya`` ``tools:`` 条目与 ``Agent.add_tool`` 的程序化
      body 走同一判别代码，执行点 = :meth:`flowing.agent.Agent.add_tool`）。
      ``args:`` 下每个参数：值是 dict → ``override_params`` 稀疏补丁
      （JSON Schema 关键字，只写要改的字段，未出现的字段从基底回填）；
      键含 ``<name> as <alias>`` → ``param_aliases`` （改名可与补丁 /
      指定值叠加）；其它值（含 ``{{ self.inject('key') }}`` 注入表达式）
      → ``specified`` （LLM 不可见，调用时以调用方 Agent 为上下文现场
      求值，注入表达式在此沿 provide 链上溯）；值是 ``_`` （``PENDING``）
      → 空补丁：装配时解析为空，深层块（如示例 ``$tools.pay.args.cwd.description:``）可逐字段填充，未被填充则合成时从基底定义全量回填，
      不报错。
    - ``enabled=False`` 时条目不进 ``Context.tools`` （LLM 不可见），但
      编程式路径仍可经注册表访问——可见性与可执行性分离。
    - entry 不持有 Tool 实例引用——执行时按 ``name_ori`` 现场查
      `ToolRegistry`。
    - 不变量：``specified`` 中的参数（固定值与注入表达式）对 LLM 不可见；
      其值优先级最高（防 LLM 篡改通道）。

    .. seealso::

        - :class:`flowing.tool.ToolRegistry` —— 规范名 → Tool 的全局表。
        - :class:`flowing.tool.ToolDefinition` —— 覆写产物类型。
        - :meth:`flowing.agent.Agent.add_tool` —— 条目装配入口。
    """


    name_alias: str
    """LLM 看到的工具名（别名）。工具调用仅按别名查找，不回退规范名——
    不同 Agent 对同一工具注册了不同别名 / 覆写，回退会绕开 Agent 级绑定。
    """
    name_ori: str
    """规范名——`ToolRegistry` 中的 key，查找可执行对象的唯一依据。
    """
    override_description: Parsable | None = None
    """覆写 LLM 看到的描述；``None`` 使用注册表原描述。是 `Parsable`：
    ``llm_definition()`` 时以调用方 Agent 为上下文自动求值（声明期可写
    模板 / 表达式，组装时拿到渲染后字符串；与 SubagentEntry 的
    description 覆写同律）。
    """
    override_params: dict[str, dict[str, Any]] | None = None
    """参数局部覆写：``{规范参数名: {子属性: 新值}}``，只写与默认不同的
    字段。Agent 侧 ``output:`` 覆写也并入本字典（以字段名为键），对
    ``llm_definition()`` 与 ``resolve()`` 完全透明。
    """
    specified: dict[str, Parsable] = field(default_factory=dict)
    """指定值参数（LLM 不可见）。默认空 dict。值为 `Parsable`，在
    ``resolve()`` 时以调用方 Agent 局部变量为上下文惰性求值。两种值形态：
    固定值（``Parsable("USD")``）与注入表达式
    （``Parsable("{{ self.inject('user_id') }}")``——求值时沿 provide
    链上溯，链断裂抛 ``MissingProvideError``）。
    """
    param_aliases: dict[str, str] = field(default_factory=dict)
    """LLM 参数名 → 规范参数名。默认空 dict。LLM 看到别名，``resolve()``
    第一步映射回规范名。
    """
    enabled: bool = True
    """是否对 LLM 可见；``False`` 时不进 ``Context.tools``，但仍可编程式调用。
    """

    def llm_definition(self, runtime: Runtime, agent: Agent) -> ToolDefinition:
        """生成本 Agent 视角下 LLM 可见的 `ToolDefinition` （四步，顺序为不变量）。

        .. rubric:: 功能介绍

        上下文组装（``Agent._assemble_context()``）时对每个 ``enabled``
        entry 调用本方法，产物进入 ``Context.tools``。每次调用都重新
        求值，不缓存结果。

        .. rubric:: 行为要点

        1. 从 ``runtime.tool_registry`` 按 ``name_ori`` 取规范 Tool 的
           默认 ``definition``；
        2. 计算 ``hidden = set(self.specified.keys())``——specified 参数
           （固定值与注入表达式）对 LLM 不可见；
        3. ``override_description`` 非 ``None`` 时以 ``agent`` 为上下文
           当场求值（Parsable 自动求值），随后
           ``definition.clone_with_overrides(self.name_alias,
           self.override_params, <渲染后描述>, specified_params=hidden)``
           生成新定义（原始定义不变）；
        4. 应用参数别名：把 LLM 可见 schema 中的参数名从规范名改为
           ``param_aliases`` 中的别名。

        :param runtime: 当前 Runtime（取其 ``tool_registry``）。
        :param agent: 调用方 Agent（``override_description`` 的 Parsable
          渲染上下文）。
        :return: 覆写后的新 `ToolDefinition`；注册表中的原始定义不变。
        :raises flowing.errors.ToolNotFoundError: ``name_ori`` 不在注册
          表中（创建管线应已保证不触发；运行时出现即注册表被外部改动的
          信号）。

        .. seealso::

            - :meth:`flowing.tool.ToolDefinition.clone_with_overrides` ——
              第 3 步的覆写语义。
        """
        tool = runtime.tool_registry.get(self.name_ori)
        hidden = set(self.specified.keys())   # specified（固定值/注入表达式）对 LLM 不可见
        # override_description 是 Parsable：以调用方 agent 为上下文现场 resolve
        # （求值面内）；None 时保持注册表原描述
        description = (str(self.override_description.resolve(agent))
                       if self.override_description is not None else None)
        definition = tool.definition.clone_with_overrides(
            self.name_alias, self.override_params, description,
            specified_params=hidden)
        # 第 4 步参数别名应用（规范名 → param_aliases 别名）：唯一可调用物
        # _apply_param_aliases（仅改名不改内容；撞名 → FormatError）
        return _apply_param_aliases(definition, self.param_aliases)

    def resolve(
        self,
        agent: Agent,
        args: dict[str, Any],
        params_schema: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        """把 LLM args 与 specified（含注入表达式）聚合为 ``execute()`` 的完整参数。

        .. rubric:: 功能介绍

        本方法在 ``Agent._normalize()`` 内部被调用（``before_tool_call``
        钩子之后），产出按规范名组织的最终参数字典。只收参数声明表
        ``params_schema`` （``tool.definition.params_schema``），不持有
        `Tool` 引用——`ToolEntry` 保持在「绑定 / 声明」层，不依赖「执行」
        层；调用方（``Agent._normalize``）已持有 Tool 实例，顺手传入
        声明表即可。

        .. rubric:: 行为要点

        1. LLM args：逐键经 ``param_aliases`` 映射回规范名；
        2. specified：`Parsable` 以 ``agent`` 局部变量为上下文惰性求值
           （固定值直给；注入表达式 ``{{ self.inject('key') }}`` 在此沿
           provide 链上溯），并按 ``params_schema`` 中对应 property 做
           `_coerce` 兼容转换后覆盖同名字段；声明表无此键时跳过转换、
           保留原值（最终由 `Tool.__call__` 的内部校验兜底报错）。

        schema 默认值不在本方法填充——由 ``_normalize()`` 第 3 步填充。

        :param agent: 调用方 Agent（provide 链上溯与 Parsable 渲染上下文）。
        :param args: LLM 原始参数（键可能是别名）。
        :param params_schema: 规范参数名 → JSON Schema property 的声明表，
          取 ``tool.definition.params_schema`` （覆写后的 LLM 视图不适用
          ——本方法一律按注册表规范定义）。
        :return: 规范名 → 值的完整参数字典，供 `Tool.__call__` 按
          ``execute()`` 签名匹配分发。
        :raises flowing.errors.MissingProvideError: 注入表达式中的 key
          沿 provide 链上溯不到任何提供者（调用时求值抛出）。

        .. seealso::

            - :meth:`flowing.agent.Agent.inject` —— 注入表达式的
              provide 链上溯执行点。
            - :meth:`flowing.parsable.Parsable.resolve` —— 惰性求值。
            - :func:`flowing.params._coerce` —— 兼容类型转换。
        """
        resolved: dict[str, Any] = {}
        for key, value in args.items():  # 第 1 步：LLM args 逐键别名 → 规范名
            resolved[self.param_aliases.get(key, key)] = value
        for key, parsable in self.specified.items():  # 第 2 步：specified 惰性求值后覆盖（固定值/注入表达式同路）
            value = parsable.resolve(agent)  # -> Any（注入表达式 {{ self.inject(...) }} 在求值中沿 provide 链上溯）
            prop = params_schema.get(key)  # -> property dict | None（声明表由调用方传入）
            if prop is not None:
                value = _coerce(value, prop)  # 声明表无此键时跳过转换，内部校验兜底
            resolved[key] = value
        return resolved


class Tool:
    """可执行对象基类——三正交中的「执行」层。

    .. rubric:: 功能介绍

    `Tool` 只对执行负责：持有一份默认 `ToolDefinition`，暴露
    ``execute()``；框架调度层经 ``__call__`` 统一调用。四种工具类型
    （script / mcp / cli / request）均以本类（或其子类）为最终产物。
    调度职责（awaitable / async generator 检测、Task 包装、caller 注入、
    返回值包装）集中在 ``__call__``，让 ``execute()`` 保持「零散参数进、
    普通值出」的最简单签名——工具作者不需要知道 `ToolResult` 的存在。

    .. rubric:: 使用示例

    直接子类化通常只用于内置工具；应用代码应使用 `ScriptTool`：

    .. code-block:: python

        class FinishTool(Tool):
            definition = ToolDefinition(name="finish", ...)

            async def execute(self, *, summary: str = "", caller: Agent) -> dict:
                ...

    .. rubric:: 行为要点

    - `Tool` 不认识钩子、不查注册表、不做参数聚合——这些都在
      ``Agent.tool_call()`` / ``_normalize()`` 一侧。
    - 实例属性开放：``.fya`` 中的非保留字段（如 ``requires_approval``）
      直接成为 tool 对象的普通属性，框架不解析、不据此做任何自动行为。
    - 不变量：``self._execution`` 仅在 ``__call__`` 调度期间非 ``None``；
      工具内部可检查 ``self._execution.cancel.is_set()`` 以响应 cancel
      （协作式取消，见 :mod:`flowing.agent` 的 `Execution` 契约）。

    .. seealso::

        - :class:`flowing.tool.ScriptTool` —— script 工具的作者基类。
        - :class:`flowing.tool.ToolRegistry` —— 注册与查找。
    """

    definition: ToolDefinition
    """默认 LLM 声明。类属性或实例属性（`ScriptTool.__init__` 自动生成）。
    Agent 级覆写不修改本对象（见 `ToolEntry`）。
    """
    registry_key: str | None = None
    """注册表全键（``ns::name``），``ToolRegistry.register`` 时回写；未注册
    实例为 ``None``。Entry 装配对文件派生工具落账本字段为 ``name_ori``
    （含目录派生命名空间的限定键，热路径精确命中）；注册表命中
    （``default::`` / ``builtin::``）的条目仍记裸名。内部 API。
    """
    _has_caller: bool
    """注册 / 实例化时经 ``inspect`` 检测 ``execute()`` 签名是否声明
    ``caller`` 参数的缓存标记。内部 API，不属稳定契约。
    """
    _execution: Execution | None
    """当前调度的执行追踪对象（cancel 信号载体），仅 ``__call__`` 期间有效。
    内部 API，不属稳定契约。
    """
    _args_model: type[BaseModel]
    """内部校验用的 Pydantic 模型——工具创建时编译一次、终身复用。来源：
    Python 层 ``args_model`` 声明直接用（声明即模型）；未声明时从
    ``execute()`` 签名构建（``_infer_from_execute``）；``.fya`` 声明经
    :func:`flowing.params.schema_to_model` 桥接。由各子类 ``__init__``
    在 ``definition`` 落定后赋值；覆写 ``__init__`` 的子类必须调
    ``super().__init__()`` 或自行赋值，否则 ``__call__`` 的内部校验无
    模型可用。与 LLM 可见 JSON Schema 同源（见 :mod:`flowing.params`），
    永不漂移。不做与 ``execute()`` 签名的一致性检查——签名差异可能是
    合法写法（``**kwargs`` 透传、装饰器包装），真写岔由调用时内部校验
    或直接测试暴露。内部 API，不属稳定契约。
    """


    async def execute(self, **kwargs: Any) -> Any:
        """工具业务逻辑入口——零散参数 + 可选 ``caller``，不接收 `ToolCall`。

        .. rubric:: 功能介绍

        子类覆写本方法。参数来自 `ToolEntry.resolve()` 聚合后的完整字典，
        由 ``__call__`` 按签名匹配分发；声明 ``caller: Agent`` 参数时框架
        自动传入调用方 Agent（用于注入表达式求值 / ``get_resource`` /
        访问调用方状态）。同步函数同样合法（``__call__`` 做 awaitable
        检测）。

        .. rubric:: 使用示例

        .. code-block:: python

            async def execute(self, *, order_id: str, amount: float,
                              caller: Agent) -> dict:
                return await payment_service.charge(
                    order_id=order_id, amount=amount)

        .. rubric:: 行为要点

        - 返回值由 ``__call__`` 自动包装：普通值 → ``completed``；抛异常
          → ``error``；返回 ``asyncio.Task`` → ``pending`` （收据，框架
          不等待）。
        - 不自行构造 `ToolResult`；不处理 specified（固定值 / 注入表达式
          已由调度层聚合进参数）；复杂对象（连接池、客户端）不进参数
          ——以标识符字符串传入，经 ``caller`` 获取真实对象。
        - 长时间运行的工具应周期性检查 ``self._execution.cancel.is_set()``
          以支持协作式取消。

        .. seealso::

            - :meth:`flowing.tool.Tool.__call__` —— 调度层职责。
            - :class:`flowing.tool.ToolResult` —— 自动包装产物。
        """
        ...

    async def __call__(
        self,
        resolved_args: dict[str, Any],
        *,
        caller: Agent | None = None,
        execution: Execution | None = None,
    ) -> ToolResult:
        """框架调度层统一入口——用户永不覆写。

        .. rubric:: 功能介绍

        ``Agent.tool_call()`` 在 ``_normalize()`` 之后经本方法执行工具。
        职责固定五项：

        1. 形态检测：``inspect.isasyncgen`` （async generator 后台形态
           ——首 yield 收据 + 后台驱动）→ ``inspect.isawaitable``——同步
           ``execute`` 直接调用，异步 ``execute`` await；
        2. Task 包装：异步执行包装为 ``asyncio.Task`` 并关联
           ``execution`` （cancel 注入的落点——置 abort 信号而非强杀
           协程）；
        3. caller 自动传入：依 ``_has_caller`` （注册时 inspect 检测）
           决定是否传 ``caller=``；
        4. 内部校验：caller 注入之后、``execute`` 之前，聚合终值按
           ``self._args_model`` （创建时编译的 Pydantic 产物）校验。
           specified（固定值 / 注入表达式求值结果）与默认值属可信来源，
           本步失败是框架 / 宿主配置错误——异常在 ``try`` 之外上抛框架
           错误通道并记日志，不被 ``except Exception`` 吞成
           ``ToolResult(error)``、不进入 LLM 可见文本（不泄漏隐藏参数
           的存在）；
        5. 结果归一与包装：返回值（含直接构造 `ToolResult` 返回的
           ``output``）经 `normalize_output` 归一——浅层判别、幂等；
           归一化中的违禁块（``ToolCallBlock`` / ``ThinkingBlock``）
           ``ValueError`` 属作者 bug，与职责 4 同走框架错误通道在
           ``try`` 之外上抛；``execute`` 内普通异常 →
           ``ToolResult(error)`` （不触发错误钩子）；``execute`` 内抛出的
           :class:`flowing.errors.Intercepted` → ``ToolResult.blocked``
           （硬阻断信号语义即 blocked，与 ``before_tool_call`` 拦截同一
           出口、同一 reason 塑形——「有意拒绝」与「意外故障」不进同一
           LLM 可见通道）；三种后台形态 → ``ToolResult(pending)``：
           ① ``execute`` 是 async generator（首 yield = 收据内容，剩余
           部分后台驱动逐段投递 EVENT）；② 普通 async ``execute`` +
           ``background = True`` （仅 script 型，不 await，直接落 Task
           分支）；③ 返回 ``asyncio.Task`` （挂 ``add_done_callback``
           固定 watcher——完成回调取终值 → `normalize_output` →
           `output_to_blocks` → 标注块 + 结果块的多块 EVENT 入队；任务
           异常 → 标注块 + 错误文本块，与同步 error 同语义）。三条后台
           路径统一经 ``Agent.track_background_task`` 注册（强引用 + 按
           id 取消 / 查询 + destroy 覆盖）。

        :param resolved_args: `ToolEntry.resolve()` 产出并经默认值填充的
          规范名参数字典；已经过 LLM 视角校验，但尚未过内部校验（本方法
          职责 4）。
        :param caller: 调用方 Agent；仅当 ``execute`` 声明了 ``caller``
          参数时实际传入。
        :param execution: 本次调用的执行追踪对象；挂到 ``self._execution``
          供工具内部检查 abort 信号。
        :return: 包装后的 `ToolResult`。
        :raises pydantic.ValidationError: 内部校验失败（specified /
          默认值的配置错误）——框架错误通道，非 LLM 可见产物。

        .. rubric:: 行为要点

        - 前置条件：``resolved_args`` 已经过 LLM 视角校验（``_normalize``
          第 1 步）与默认值填充（第 3 步）。
        - enqueue 契约：普通 ``async def execute`` 被 await 到底，结果只
          作为返回值交给 ``Agent.tool_call``，不 enqueue。三种后台形态
          （async generator / ``background`` 标记 / 返回 ``asyncio.Task``）
          返回 ``pending`` 收据：async gen 的后续 yield 与 Task 完成由
          驱动方逐段 enqueue EVENT（``source="tool_result"``）；
          ``background`` 标记复用 Task 分支。因此工具结果是否入队，只由
          ``execute`` 的返回形态决定，与调用上下文无关。
        - 不重试、不超时兜底、不审批——重试由可选的 ``use_retry()``
          提供，审批在 ``before_tool_call``。
        - 边缘情况：``execution.cancel`` 在 ``execute`` 运行期间被置位
          → 本方法不强制中断 Task，工具自行协作退出；工具不响应时由
          cancel / stop 族的上层策略处理。

        .. seealso::

            - :meth:`flowing.agent.Agent.tool_call` —— 上游调用点（其收尾
              对 shortcut / 钩子改写产物幂等再归一）。
            - :class:`flowing.agent.Execution` —— cancel / pause 信号契约。
        """
        self._execution = execution  # cancel 注入落点；仅调度期间非 None
        if not hasattr(self, "_has_caller"):
            # 逃生舱写法（直接子类化 Tool + 类属性 definition、无 __init__，
            # 见类 docstring 的 FinishTool 示例）没有创建期检测点——就地检测
            # 并实例级缓存；正常路径（ScriptTool 等四个子类）在 __init__ 已落定
            self._has_caller = "caller" in inspect.signature(self.execute).parameters
        if self._has_caller:
            resolved_args["caller"] = caller  # caller 自动传入（execute 声明了该参数时）
        if not hasattr(self, "_args_model"):
            # 同上逃生舱：创建期未定模型的直接子类，就地按 execute 签名构建
            # 并缓存（与 ScriptTool.__init__ 同一建模入口，编译一次终身复用）
            self._args_model = _infer_from_execute(self.execute)
        # 职责 4：内部校验——caller 注入之后、try 之外。
        # specified/inject/默认值的配置错误 -> 上抛框架错误通道 + 日志，
        # 不被下方 except Exception 吞成 ToolResult(error)、不进 LLM 可见文本
        # （不泄漏隐藏参数的存在）；日志只记工具名，不记参数值（防敏感值泄露）
        try:
            self._args_model.model_validate(resolved_args)
        except ValidationError:
            _logger.exception(
                "tool %s failed internal validation (specified/inject/default-value misconfiguration)",
                getattr(self, "definition", None) and self.definition.name
                or type(self).__name__)
            raise
        try:
            result = self.execute(**resolved_args)  # -> Any（同步）/ awaitable（异步）/ async gen（后台形态）
            if inspect.isasyncgen(result):
                # async generator 形态——等待首 yield（准备完成哨兵，
                # 可空）作 pending 收据；剩余部分 ensure_future 后台驱动（B3/
                # 每个后续 yield 逐段投递 EVENT）。首 yield 前异常
                # 在此上抛，按 except 顺序分派：Intercepted → blocked（1833）；
                # 其余 → error（工具调用错误，LLM 立即可见，不挂 pending）
                try:
                    receipt = await anext(result)
                except StopAsyncIteration:
                    receipt = None                # 条件性空 yield：无收据（纯副作用）
                if caller is not None:
                    drive_task = asyncio.ensure_future(
                        self._drive_asyncgen(result, caller))
                    task_id = caller.track_background_task(drive_task)   # 注册（强引用 + 按 id 取消/查询 + destroy 覆盖）
                    return ToolResult(status="pending", output=receipt,
                                      background_task_id=task_id)
                # 编程路径（无 caller）：不注册、不驱动——后台主体不执行；
                # 弃置的 generator 须 aclose() 关闭（否则 GC 触发
                # "async generator ignored GeneratorExit" 警告）
                await result.aclose()
                return ToolResult(status="pending", output=receipt,
                                  background_task_id=None)
            if inspect.isawaitable(result):
                if getattr(self, "background", False) and isinstance(self, ScriptTool):
                    # background 标记——普通 async execute 的后台化入口，
                    # 不 await，落下方 Task/pending 分支（仅 script 型生效）
                    value = asyncio.ensure_future(result)
                else:
                    task = asyncio.ensure_future(result)  # Task 包装（abort 为协作式，不强杀）
                    value = await task
            else:
                value = result
        except Intercepted as exc:
            # execute 内抛出的硬阻断信号（如工具内部下游扩展钩子的拦截）：语义
            # 即 blocked——与 before_tool_call 拦截同一出口、同一 reason 塑形，
            # 不当业务异常吞成 error（「有意拒绝」与「意外故障」不进同一通道）
            return ToolResult.blocked(reason=str(exc))
        except Exception as exc:
            # 异常路径：包装为 error 结果，不触发错误钩子（LLM 可见的正常产物）
            return ToolResult(status="error", error=str(exc))
        finally:
            self._execution = None  # 不变量：_execution 仅 __call__ 期间有效
        if inspect.isasyncgen(value):
            # 嵌套形态：async def execute 返回 async gen 对象
            # ——与 execute 自身即 async gen 同一条后台管线。
            # 本分支在 try 之外——首 yield 前异常在此自行按 except 顺序分派
            try:
                receipt = await anext(value)
            except StopAsyncIteration:
                receipt = None
            except Intercepted as exc:
                return ToolResult.blocked(reason=str(exc))
            except Exception as exc:
                return ToolResult(status="error", error=str(exc))
            if caller is not None:
                drive_task = asyncio.ensure_future(
                    self._drive_asyncgen(value, caller))
                task_id = caller.track_background_task(drive_task)
                return ToolResult(status="pending", output=receipt,
                                  background_task_id=task_id)
            await value.aclose()
            return ToolResult(status="pending", output=receipt,
                              background_task_id=None)
        if isinstance(value, asyncio.Task):
            # fire-and-forget 收据；Task 挂 add_done_callback 固定 watcher
            # （D13，非扩展点）：完成回调取终值 → normalize_output →
            # output_to_blocks → 标注块 + 结果块的多块 EVENT 入队；
            # 任务异常 → 标注块 + 错误文本块（与同步 error 同语义，LLM 可见）
            if caller is not None:
                value.add_done_callback(
                    lambda t: asyncio.ensure_future(
                        self._deliver_async_result(t, caller)))
                # B9：补既有缺口——返回 Task 路径此前无强引用持有者（GC 隐患），
                # 现统一入注册表（完成/异常/取消即弃）
                task_id = caller.track_background_task(value)
                return ToolResult(status="pending", output=None,
                                  background_task_id=task_id)
            return ToolResult(status="pending", output=None)
        # 职责 5：归一化在 try 之外——浅层判别、幂等；
        # 违禁块（ToolCallBlock/ThinkingBlock）ValueError 属作者 bug，
        # 上抛框架错误通道，不被吞成 ToolResult(error)
        value = await normalize_output(value)
        return ToolResult(status="completed", output=value)

    async def _deliver_async_result(self, task: "asyncio.Task", caller: "Agent") -> None:
        """异步工具完成回调（框架固定行为，非扩展点）：取终值 → 归一 →
        塑形 → 标注块 + 结果块的 EVENT 消息（``source="tool_result"``、
        STEER 优先级）入调用方队列；任务异常 → 标注块 + 错误文本块
        （LLM 可见）。内部 API，不属稳定契约。
        """
        marker = TextBlock(text=f"final result of async tool {self.definition.name}: ")
        if task.cancelled():
            blocks = [marker, TextBlock(text="async task cancelled")]
        elif (exc := task.exception()) is not None:
            blocks = [marker, TextBlock(text=str(exc))]   # 与同步 error 同语义
        else:
            value = await normalize_output(task.result())
            blocks = [marker, *output_to_blocks(value)]
        await caller.enqueue_message(Message(
            kind=MessageKind.EVENT, source="tool_result",
            content=blocks, priority=MessagePriority.STEER))

    async def _drive_asyncgen(self, agen: "AsyncGenerator", caller: "Agent") -> None:
        """后台驱动 async generator（`_deliver_async_result` 的兄弟）：后续
        yield 逐段投递 EVENT（``source="tool_result"``、STEER 优先级）；
        中途异常投递错误块 + 诊断日志（与同步 error 同语义，LLM 可见）；
        取消投递「已取消」后裸 ``raise`` （任务以 cancelled 终态结束）。
        内部 API，不属稳定契约。
        """
        marker = TextBlock(text=f"async tool {self.definition.name}: ")
        try:
            async for item in agen:
                blocks = await self._background_blocks(marker, item)
                try:
                    await caller.enqueue_message(Message(
                        kind=MessageKind.EVENT, source="tool_result",
                        content=blocks, priority=MessagePriority.STEER))
                except Exception:
                    # 投递失败不掩盖原结局（B3/B4 边界——enqueue_message 只入
                    # 内存队列，失败仅钩子/极端场景）
                    _logger.warning(
                        "async tool %s: report delivery failed", self.definition.name)
        except asyncio.CancelledError:
            try:
                await caller.enqueue_message(Message(
                    kind=MessageKind.EVENT, source="tool_result",
                    content=[marker, TextBlock(text="async task cancelled")],
                    priority=MessagePriority.STEER))
            except Exception:
                pass   # B4：统一尝试投递「已取消」，失败静默
            raise      # 裸 raise：Task 真正进入 cancelled 终态（取消失效的
                       # 唯一保证——不 raise 则任务继续跑）
        except Exception as exc:
            try:
                await caller.enqueue_message(Message(
                    kind=MessageKind.EVENT, source="tool_result",
                    content=[marker, TextBlock(text=str(exc))],
                    priority=MessagePriority.STEER))
            except Exception:
                pass
            _logger.exception("async tool %s failed in the background", self.definition.name)

    async def _background_blocks(self, marker: TextBlock, item: Any) -> list[ContentBlock]:
        """yield 值归一化塑形（与 `_deliver_async_result`、`as_message` 同一
        实现）；浅层违禁块（``ToolCallBlock`` / ``ThinkingBlock``）容错转
        普通文本错误说明——后台任务已脱离调用栈，「抛异常」无人接收（与
        completed 路径的 ``ValueError`` 框架错误通道区分）。内部 API，
        不属稳定契约。
        """
        if _has_forbidden_block(item):
            return [marker, TextBlock(
                text="report content contained forbidden blocks (tool calls / thinking blocks); omitted")]
        return [marker, *output_to_blocks(await normalize_output(item))]


def _has_forbidden_block(value: Any) -> bool:
    """浅层违禁块检测（与 `normalize_output` 的浅层判别同口径——顶层值或
    list/tuple 成员；深层埋藏由塑形期 ``StructBlock`` 构造校验兜底）。
    内部 API，不属稳定契约。"""
    if isinstance(value, (ToolCallBlock, ThinkingBlock)):
        return True
    if isinstance(value, (list, tuple)):
        return any(_has_forbidden_block(v) for v in value)
    return False


class ScriptTool(Tool):
    """script 工具的作者基类——类属性声明 + 框架自动生成 `ToolDefinition`。

    .. rubric:: 功能介绍

    工具作者不直接构造 `ToolDefinition`，而是在子类上声明
    ``name`` / ``description`` / ``args_model`` 类属性；``__init__`` 时
    框架自动生成 ``self.definition``。``name`` **必填**（0904 契约：不再
    由类名 kebab 推断——ScriptTool 禁止没有 name 字段）；手写子类须类体
    显式 ``name``（或显式 ``definition``），``.fya`` / ``.py`` 文件通道的
    规范名 = 文件身份（文件名/目录名，加载层注入，类体显式 ``name`` 与
    文件身份不符抛 :class:`flowing.errors.NameMismatchError`）。
    ``args_model`` 可省略——从 ``execute()`` 签名的类型标注与默认值构建
    模型。

    script 工具共有三条等价的定义通道：手写本类子类（主路径）；
    ``@flowing_tool`` 打标函数（框架自动提升为等价子类）；``.fya`` 的
    ``callable:`` 指针指向裸函数。规范名来源：手写通道 = 类体显式
    ``name``（必填）；文件通道（``.fya`` / ``.py`` / 打标函数）= 文件名 /
    目录名身份（声明面权威，加载层注入）。``description`` 按显式声明 >
    类 docstring 整体 > ``execute()`` docstring 整体的三级回退链取（0904：整体）。

    .. rubric:: 使用示例

    .. code-block:: python

        from flowing import ScriptTool
        from pydantic import BaseModel, Field

        class PayArgs(BaseModel):
            # 声明即模型：LLM schema 与执行校验都从这里派生
            order_id: str = Field(description="订单 ID")
            amount: float = Field(ge=0.01)   # 约束直接进 schema 与校验

        class MakePayment(ScriptTool):
            \"\"\"对指定订单发起支付。仅在用户明确确认支付意图后调用。\"\"\"

            name = "make-payment"         # 必填：不再由类名推断
            description = "发起支付"
            args_model = PayArgs          # 参数声明（BaseModel 子类）

            async def execute(self, *, order_id: str, amount: float,
                              caller: Agent) -> dict:
                ...

    后台工具形态（async generator）：``execute`` 写成 async generator——
    第一个 ``yield`` 是「准备完成」标记（可空值），`Tool.__call__` 等待它
    作为 ``pending`` 收据（``tool_status="pending"`` 的 TOOL 消息带内容）；
    后续每个 ``yield`` 由框架后台驱动并逐段投递 EVENT 消息（LLM 可见）；
    首 yield 前只允许轻量准备，长任务必须放在首 yield 之后（违反的后果
    是收据延迟——作者责任）。「只要后台、不要中间报告」的普通 async
    ``execute`` 可声明类属性 ``background = True`` （仅 script 型生效）走
    同一 ``pending`` 通道：

    .. code-block:: python

        class RunWorkflowTool(ScriptTool):
            async def execute(self, *, path: str, caller: Agent, **args):
                wf = resolve_workflow(path)                     # 轻量准备
                yield {"status": "started", "workflow": path}   # ① 收据
                await wf.run(**args)                            # 长任务（后台）
                yield {"status": "done", "workflow": path}      # ② EVENT

    async generator 禁止带值 ``return`` （PEP 525 只允许裸 ``return``）
    ——最终呈现 = 最后一条 ``yield``；没有 ``yield`` 的函数不是 async
    generator（走普通执行路径）；``background = True`` 只对普通 async
    ``execute`` （单次返回）生效，async gen 无需标记。

    .. rubric:: 行为要点

    - 互斥规则：类属性声明（``name`` / ``description`` / ``args_model``）
      与手写 ``definition`` 类属性互斥；同时存在时框架发警告，以显式
      ``definition`` 为准。
    - 声明通道互斥：``callable:`` 指向的函数不允许带 ``@flowing_tool``
      装饰（打标 = 自动提升通道，``callable:`` = 显式指针通道，二选一）；
      违反抛 :class:`flowing.errors.FormatError`。
    - script 工具注册为全局单例（Runtime 一份，所有 Agent 共享）——同一
      份用户代码不应实例化多次。
    - 每个 ``.py`` 文件至多一个打标函数；与 Tool 子类同文件并存或含多个
      打标函数 → :class:`flowing.errors.AmbiguousToolError` （定向查找阶段
      判别）。
    - 类属性在 ``__init__`` 前已就绪，无时序问题。

    :raises flowing.errors.MissingSchemaError: 既无 ``args_model`` 声明、
      ``execute()`` 参数又缺类型标注时（签名构建不出字段类型）。
    :raises flowing.errors.NameMismatchError: 文件通道（``.fya`` / ``.py``
      加载）类体显式 ``name`` 与文件身份（文件名/目录名）不符时（防错位）；
      手写直接构造通道无此错误（``name`` 缺失抛
      :class:`flowing.errors.MissingFieldError`）。

    .. seealso::

        - :func:`flowing.tool.flowing_tool` —— 打标函数通道。
        - :func:`flowing.tool._infer_from_execute` —— 签名推断的内部实现。
        - :mod:`flowing.params` —— 声明层规则与桥接子集。
    """

    name: str
    """规范工具名（kebab-case）。**必填**（0904 契约：不再由类名 kebab
    化推断）：手写子类须类体显式声明；``.fya`` / ``.py`` 文件通道由加载层
    以文件身份（文件名/目录名）注入，类体显式 ``name`` 与文件身份不符抛
    :class:`flowing.errors.NameMismatchError`；与显式 ``definition`` 互斥。
    """
    description: str
    """工具描述。类属性声明；缺省时按三级回退链取：显式 ``description``
    > 类 docstring **整体** > ``execute()`` docstring **整体**（0904：不再取首段）。
    """
    args_model: type[BaseModel] | None
    """参数声明：Pydantic ``BaseModel`` 子类，声明即模型——LLM schema 由
    ``model_json_schema()`` 派生、执行校验即模型本身；``None`` 时由
    ``_infer_from_execute(self.execute)`` 从签名构建模型。
    """


    def __init__(self) -> None:
        """自动生成 ``self.definition`` （同步构造，不触网、不注册）。

        .. rubric:: 行为要点

        - 顺序：显式 ``definition`` 类属性存在 → 直接使用（同时声明
          ``name`` / ``description`` / ``args_model`` 时发告警日志，仍以
          显式 ``definition`` 为准——互斥规则）；否则要求类体显式
          ``name``（0904 契约：必填、不再由类名推断；缺失抛
          :class:`flowing.errors.MissingFieldError`），``description`` 按
          三级回退链取（显式声明 > 类 docstring **整体** >
          ``execute()`` docstring **整体**，皆无 → 空串），``args_model`` 未声明时按
          ``self.args_model or _infer_from_execute(self.execute)`` 取值，
          最后生成 ``ToolDefinition`` （``params_schema`` 取
          ``args_model.model_json_schema()["properties"]``）。
        - 后置条件：``self.definition`` 非 ``None``；``self._has_caller``
          已按 ``execute`` 签名检测完毕。
        """
        cls = type(self)
        if "definition" in cls.__dict__:
            if any(key in cls.__dict__ for key in ("name", "description", "args_model")):
                # 互斥规则：与 name/description/args_model 同时声明 -> 告警日志，
                # 以显式 definition 为准
                _logger.warning(
                    "ScriptTool subclass %s declares both definition and "
                    "name/description/args_model — mutually exclusive rule: the explicit definition wins",
                    cls.__name__)
            self.definition = cls.__dict__["definition"]
        else:
            # name 必填（0904 契约：不再由类名 kebab 推断——ScriptTool 禁止
            # 没有 name 字段）。类体显式 name（或经 .fya/.py 文件身份注入的
            # name）即规范名；只看本类 __dict__——继承来的 name 不参与
            # （与 agent 侧 _load_agent_from_py 的口径一致）
            explicit_name = cls.__dict__.get("name")
            if explicit_name is None:
                raise MissingFieldError(
                    "name", cls.__name__)   # FormatError 族：声明缺必填字段
            args_model = getattr(self, "args_model", None)
            if args_model is None:
                args_model = _infer_from_execute(self.execute)  # 从 execute 签名构建模型
            # description 三级回退链：显式声明 > 类 docstring 整体 >
            # execute() docstring 整体；皆无 -> 空串。注意用 cls.__doc__
            # 而非 inspect.getdoc(cls)——后者会继承基类 docstring
            description = getattr(cls, "description", None)
            if description is None:
                # 0904：无显式 description 时回退 docstring **整体**（cleandoc
                # 全文），与用户裁决一致（此前取首段）
                description = (_whole_doc(cls.__doc__)
                               or _whole_doc(self.execute.__doc__) or "")
            self.definition = ToolDefinition(
                name=explicit_name, description=description,
                # 声明即模型：schema 从模型派生，但先收敛到桥接子集——
                # pydantic 的 model_json_schema() 会给 property 附 title 等
                # 展示键，直接进 definition 会在 agent 侧 schema_to_model
                # 桥接时被超子集校验拒绝（F1：工具声明三通道皆可用）。
                params_schema=bridge_properties(
                    args_model.model_json_schema()["properties"]))
        self._has_caller = "caller" in inspect.signature(self.execute).parameters
        self._execution = None
        # 创建时定内部校验模型——声明即模型，无需再编译；
        # 显式 definition 路径下若未带模型，就地从 execute 签名构建兜底
        self._args_model = getattr(self, "args_model", None) or _infer_from_execute(self.execute)


# ──────────────────────────────────────────────────────────────────
# 三个具体工具类（cli / request / mcp）共用的模板与渲染助手
# ──────────────────────────────────────────────────────────────────

_ENV_JINJA = jinja2.Environment(autoescape=False, undefined=jinja2.StrictUndefined)
"""``{{ env.X }}`` 凭证模板的一次性渲染环境。

顶层 ``env`` 绑定 ``os.environ`` 只读视图；``StrictUndefined`` 使缺失变量
fail fast（渲染方就地包成 ``FormatError``），不静默降级为空串。渲染产物
不进消息、不落盘。内部 API。
"""


def _render_env_templates(values: "dict[str, str] | None") -> "dict[str, str] | None":
    """把映射的每个字符串值按 ``{{ env.X }}`` 模板一次性渲染。内部 API。

    求值时点：装配期（工具实例构造，连接 / 请求之前一次完成）。缺失变量 →
    :class:`flowing.errors.FormatError` （fail fast，不静默降级）。
    """
    if values is None:
        return None
    rendered: dict[str, str] = {}
    for key, value in values.items():
        try:
            rendered[key] = _ENV_JINJA.from_string(str(value)).render(
                env=MappingProxyType(os.environ))
        except jinja2.UndefinedError as exc:
            raise FormatError(f"environment variable referenced by template {value!r} is missing: {exc}") from exc
    return rendered


def _auth_headers(auth: dict[str, Any]) -> dict[str, str]:
    """``auth`` 语法糖 → 请求头 dict（``basic`` / ``bearer`` / ``api_key``）。

    内部 API。未知类型 → ``FormatError`` （声明期 fail fast）。
    """
    auth_type = auth.get("type")
    if auth_type == "basic":
        cred = base64.b64encode(
            f"{auth.get('username', '')}:{auth.get('password', '')}".encode()
        ).decode()
        return {"Authorization": f"Basic {cred}"}
    if auth_type == "bearer":
        return {"Authorization": f"Bearer {auth.get('value', '')}"}
    if auth_type == "api_key":
        return {str(auth["header"]): str(auth.get("value", ""))}
    raise FormatError(f"unknown auth type: {auth_type!r} (must be basic / bearer / api_key)")


class _ShellRaw(str):
    """``| raw`` 旁路标记类型（finalize 识别后不再转义）。内部 API。"""


def _shell_raw_filter(value: Any) -> _ShellRaw:
    """``{{ arg | raw }}`` 旁路自动转义；每次渲染命中即告警。内部 API。"""
    _logger.warning("CliTool command template uses the | raw bypass of auto-escaping — "
                    "ensure raw concatenation values are trusted (command-injection risk is on you)")
    return _ShellRaw(str(value))


def _shell_finalize(value: Any) -> str:
    """CliTool 命令模板的 finalize：插入值一律 ``shlex.quote`` 转义；
    ``_ShellRaw`` 原样放行。内部 API。"""
    if isinstance(value, _ShellRaw):
        return str(value)
    return shlex.quote(str(value))


_CLI_JINJA = jinja2.Environment(
    autoescape=False, finalize=_shell_finalize,
    undefined=jinja2.StrictUndefined)   # 模板变量缺失 = 声明笔误，渲染期暴露
_CLI_JINJA.filters["raw"] = _shell_raw_filter

_ENV_URL_JINJA = jinja2.Environment(autoescape=False, undefined=jinja2.DebugUndefined)
"""RequestTool URL 的装配期 env 渲染环境。内部 API。

URL 是两阶段模板：装配期先渲染 ``{{ env.X }}`` （DebugUndefined 把非 env
的占位原样保留为 ``{{ name }}`` 文本），执行期再以 args 渲染路径参数
（StrictUndefined）。env 引用缺失时在执行期暴露为渲染错误（error 结果）
——headers / auth 的 env 引用才是装配期 fail fast（`_ENV_JINJA`）。
"""

_SHELL_EXECUTABLES: dict[str, "str | None"] = {
    "sh": None,          # create_subprocess_shell 默认 /bin/sh
    "bash": "bash",
    "ps": "pwsh",
    "powershell": "powershell",
    "cmd": "cmd",
}
"""CliTool ``shell`` 声明 → 子进程 executable 映射（``None`` = 默认 sh）。

``ps`` 取 PowerShell 7 的 ``pwsh``。可执行文件不存在 → 子进程启动失败，
由 ``__call__`` 包装为 ``status="error"`` 结果。内部 API。
"""


def _mcp_input_schema_to_params(input_schema: "dict[str, Any] | None") -> dict[str, dict[str, Any]]:
    """MCP ``inputSchema`` （完整 JSON Schema object）→ 框架 properties 映射。

    内部 API。两点归一：

    - property 键裁剪到 :data:`flowing.params.SCHEMA_KEYWORDS` 子集——
      MCPServer 等服务端生成的 inputSchema 带 ``title`` 等超子集键，
      不裁剪会在 ``schema_to_model`` 桥接时炸 ``FormatError``；
    - required 口径对齐（框架以 ``default`` 有无派生）：``required``
      列表之外的 property 若无 ``default`` 补 ``default=None``——否则
      可选参数会被 ``schema_to_model`` 建成必填字段。
    """
    from flowing.params import SCHEMA_KEYWORDS

    schema = input_schema or {}
    props = {k: {kk: vv for kk, vv in v.items() if kk in SCHEMA_KEYWORDS}
             for k, v in schema.get("properties", {}).items()}
    required = set(schema.get("required") or ())
    for key, prop in props.items():
        if key not in required and "default" not in prop:
            prop["default"] = None
    return props


class McpTool(Tool):
    
    """MCP 工具实例——连接 MCP 服务器并代理其暴露的工具 schema。

    .. rubric:: 功能介绍

    ``type: mcp`` 的实例类。两种来源互斥：``command`` （本地 stdio 进程）
    或 ``url`` （远程端点，``/sse`` 结尾走 SSE、其余走 streamable HTTP）。
    默认 `ToolDefinition` 来自 MCP 服务器 ``list_tools()`` 返回的 schema，
    可经 ``overrides`` 局部覆写。MCP 工具无用户代码，是纯参数化配置——
    每个声明独立实例（区别于 script 单例）。

    命名规则：MCP 声明块代理的是一组服务端工具，注册 / 解析时每个实际
    工具的规范名 = ``<fya 声明名>-<server 暴露工具名>`` （如声明
    ``name: github``、服务端暴露 ``create-issue`` → 注册规范名
    ``github-create-issue``）——服务端工具名空间天然带声明名前缀，不同
    MCP 来源的同名工具不撞名。Agent 侧引用（``tools:`` 条目 /
    ``add_tool``）按合成名引用（可照常 ``as`` 别名）。

    .. rubric:: 使用示例

    .. code-block:: yaml

        # 本地 stdio 进程
        name: search
        type: mcp
        description: 搜索互联网内容。在需要查找最新信息时调用。
        command: npx
        args: ["-y", "@modelcontextprotocol/server-brave-search"]
        env:
          BRAVE_API_KEY: "{{ env.BRAVE_API_KEY }}"

        # 远程端点
        name: github
        type: mcp
        url: https://mcp.example.com/github/sse
        headers:
          Authorization: "Bearer {{ env.GITHUB_TOKEN }}"
        tools: [create-issue, list-prs]     # 服务端工具子集
        overrides:
          create-issue:
            args:
              title: {description: "Issue 标题，不超过 80 字符。"}

    模板中的 ``env`` 是渲染上下文顶层对象（绑定 ``os.environ``），凭证经
    ``{{ env.X }}`` 注入，不硬编码、不进消息、不落盘；缺失变量在装配期
    fail fast（``FormatError``）。

    .. rubric:: 行为要点

    - 来源识别：存在 ``command`` → stdio；存在 ``url`` → 远程；两者都有
      → `AmbiguousMcpSourceError`；两者都无 → `MissingMcpSourceError`。
    - 同名冲突：同命名空间规范名重名注册永远抛 `ToolNameConflictError`
      ——注册名为合成名 ``<声明名>-<server 暴露名>``，撞名即声明名重复，
      须换声明名（或注册到不同命名空间）。
    - MCP 服务器的 ``outputSchema`` 自动填入 `ToolDefinition.output_schema`
      （随声明携带，adapter 白名单不映射；当前实现不据此做结果校验，也
      不喂模型）。
    - 连接失败 / 服务端 ``isError`` → ``status="error"`` 结果；无连接重试
      策略。

    .. seealso::

        - :class:`flowing.tool.ToolRegistry` —— 重名约束的执行者。
    """


    def __init__(
        self,
        *,
        definition: ToolDefinition,
        command: str | None = None,
        args: list[str] | None = None,
        env: dict[str, str] | None = None,
        url: str | None = None,
        headers: dict[str, str] | None = None,
        tools: list[str] | None = None,
        overrides: dict[str, Any] | None = None,
    ) -> None:
        """按来源声明构造 MCP 工具实例。

        :param definition: 工具声明（通常来自 ``list_tools()`` schema 经
          ``overrides`` 覆写后的产物）。
        :param command: stdio 模式启动命令；与 ``url`` 互斥。
        :param args: 启动命令参数列表。
        :param env: 子进程环境变量（值支持 ``{{ env.X }}`` 模板）。
        :param url: 远程端点 URL；与 ``command`` 互斥。
        :param headers: 远程模式请求头（值支持模板）。
        :param tools: 只暴露的服务端工具名子集；``None`` 全量暴露。
        :param overrides: 对服务端 schema 的局部覆写
          （``{工具名: {description/args: ...}}``）。
        :raises flowing.errors.AmbiguousMcpSourceError: ``command`` 与
          ``url`` 同时给出。
        :raises flowing.errors.MissingMcpSourceError: 两者均未给出。
        """
        if command is not None and url is not None:
            raise AmbiguousMcpSourceError("both command and url were given")  # 来源互斥
        if command is None and url is None:
            raise MissingMcpSourceError("neither command nor url was given")
        self.definition = definition
        self.command = command
        self.args = args
        # {{ env.X }} 模板装配期一次性渲染：缺失变量 FormatError
        # fail fast；渲染产物只进连接配置，不进消息、不落盘
        self.env = _render_env_templates(env)
        # url 无路径参数阶段（区别于 RequestTool 的两阶段），装配期一次渲染；
        # 缺失 env 变量 FormatError fail fast（与 _render_env_templates 同口径）
        if url is not None:
            try:
                self.url = _ENV_JINJA.from_string(url).render(env=MappingProxyType(os.environ))
            except jinja2.UndefinedError as exc:
                raise FormatError(f"environment variable referenced by template {url!r} is missing: {exc}") from exc
        else:
            self.url = None
        self.headers = _render_env_templates(headers)
        self.tools = tools
        self.overrides = overrides
        self._execution = None
        # 声明实例（组代理）为 None，不可执行；list_tools() 展开产物置为
        # 服务端工具名。内部 API。
        self._server_tool_name: str | None = None
        # 创建时定内部校验模型（fya 声明经 params.schema_to_model
        # 桥接——definition.params_schema 即桥接产物 schema，再建最终模型）
        self._args_model = schema_to_model("Args", self.definition.params_schema)

    @asynccontextmanager
    async def _connect(self) -> "Any":
        """建立一次 MCP 会话（惰性连接：``list_tools`` / ``execute`` 各连
        一次，用后关闭；无连接池、无重试策略）。内部 API，不属稳定契约。

        连接失败异常上抛（``execute`` 内由 ``__call__`` 包装为
        ``status="error"`` 结果）。url 形态的传输判别：URL 路径以
        ``/sse`` 结尾 → SSE；其余 → streamable HTTP。stdio 的 ``env``
        直传 ``StdioServerParameters`` （SDK 内与默认环境合并）。
        """
        from mcp import ClientSession, StdioServerParameters   # 函数内 import：mcp SDK 重，非 MCP 用户不付 import 成本

        if self.command is not None:
            from mcp.client.stdio import stdio_client

            params = StdioServerParameters(
                command=self.command, args=self.args or [], env=self.env)
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    yield session
        elif (self.url or "").rstrip("/").endswith("/sse"):
            from mcp.client.sse import sse_client

            async with sse_client(self.url, headers=self.headers) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    yield session
        else:
            from mcp.client import streamable_http as _streamable_http

            # mcp 2.x：headers 不再作 transport 级参数，须配置在自建的
            # httpx2.AsyncClient 上（SDK 文档口径：follow_redirects=True，
            # 长活 GET 流需 Timeout(30, read=300)）；无 headers 时省略
            # http_client，用 SDK 内置默认客户端（同口径超时）。
            if self.headers:
                import httpx2

                async with httpx2.AsyncClient(
                        headers=self.headers, follow_redirects=True,
                        timeout=httpx2.Timeout(30, read=300)) as http_client:
                    async with _streamable_http.streamable_http_client(
                            self.url, http_client=http_client) as (read, write):
                        async with ClientSession(read, write) as session:
                            await session.initialize()
                            yield session
            else:
                async with _streamable_http.streamable_http_client(
                        self.url) as (read, write):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        yield session

    async def list_tools(self) -> "list[McpTool]":
        """连接服务器拉取 ``list_tools()`` schema，产出逐工具代理实例列表。

        .. rubric:: 功能介绍

        装配期 schema 拉取的唯一入口：``ToolRegistry.get`` 命中 mcp 型
        TOOL.fya 只产声明实例（骨架 definition）；装配层在 get 解析完成
        后调用本方法一次——每个服务端工具产一个独立 `McpTool` 代理实例，
        规范名 = ``<fya 声明名>-<server 暴露工具名>``，由装配层经
        ``ToolRegistry.register`` 按合成名注册（撞名 →
        ``ToolNameConflictError``，注册表层承载）。``execute`` 不依赖本
        方法（惰性连接），但 LLM 可见声明必须由本方法的产物承载。

        .. rubric:: 行为要点

        - ``tools`` 子集过滤：声明了 ``tools`` 时仅展开子集内的服务端
          工具；``None`` 全量展开。
        - ``overrides`` 应用：``{工具名: {description/args: ...}}``——
          ``description`` 整体替换；``args`` 经
          :func:`flowing.params.apply_param_overrides` 对 inputSchema
          派生的 properties 稀疏覆写（非法关键字 fail-fast）。
        - 服务端 ``outputSchema`` 自动填入
          :attr:`ToolDefinition.output_schema`——存储、随声明携带；当前
          实现不据此做结果校验，也不喂模型。
        - MCP ``inputSchema`` → 框架 properties 映射的 required 口径对齐
          ：框架以 ``default`` 有无派生 requiredness，服务端 schema 的
          ``required`` 列表之外的 property 若无 ``default`` 补
          ``default=None``——否则可选参数会被 ``schema_to_model`` 建成
          必填字段。
        """
        async with self._connect() as session:
            result = await session.list_tools()
        produced: list[McpTool] = []
        for server_tool in result.tools:
            if self.tools is not None and server_tool.name not in self.tools:
                continue   # tools 子集之外的工具不暴露
            override = (self.overrides or {}).get(server_tool.name) or {}
            params = _mcp_input_schema_to_params(server_tool.input_schema)
            if "args" in override:
                params = apply_param_overrides(params, override["args"])
            definition = ToolDefinition(
                name=f"{self.definition.name}-{server_tool.name}",   # 合成名 <声明名>-<server 名>
                description=override.get("description",
                                         server_tool.description or ""),
                params_schema=params,
                output_schema=server_tool.output_schema)   # 自动填入（存储、随声明携带）
            tool = McpTool(
                definition=definition, command=self.command, args=self.args,
                env=self.env, url=self.url, headers=self.headers)
            tool._server_tool_name = server_tool.name
            produced.append(tool)
        return produced

    async def execute(self, **kwargs: Any) -> Any:
        """代理调用服务端工具：惰性连接 → ``call_tool`` → 取回产物。

        .. rubric:: 行为要点

        - 本方法只存在于 ``list_tools()`` 展开产物（``_server_tool_name``
          已置位）上；声明实例（组代理）直接执行 → ``RuntimeError``
          （按合成名 ``<声明名>-<server 名>`` 引用，属声明笔误）。
        - 连接失败 / 服务端 ``isError`` 返回 → 抛异常，由 ``__call__``
          包装为 ``status="error"`` 结果（无连接重试）。
        - 返回形态：服务端 ``structuredContent`` 优先；否则文本块拼合
          （单块 → str，多块 → list[str]，无 → ``None``）。
        """
        if self._server_tool_name is None:
            raise RuntimeError(
                f"MCP declaration {self.definition.name!r} is a tool-group proxy and cannot be executed directly"
                " — reference its expanded products by the synthetic name <decl-name>-<server tool name>")
        async with self._connect() as session:
            result = await session.call_tool(self._server_tool_name,
                                             arguments=kwargs)
        if result.is_error:
            text = "".join(getattr(block, "text", "") for block in result.content)
            raise RuntimeError(
                f"MCP tool {self._server_tool_name} got a server error: {text}")
        if result.structured_content is not None:
            return result.structured_content
        texts = [block.text for block in result.content
                 if getattr(block, "text", None) is not None]
        if not texts:
            return None
        return texts[0] if len(texts) == 1 else texts


class CliTool(Tool):
    
    """CLI 工具实例——Jinja2 命令模板 + shell 执行。

    .. rubric:: 功能介绍

    ``type: cli`` 的实例类。``args`` （参数 schema）必填，无自动推断来源；
    命令体 ``command`` 为 Jinja2 模板，渲染上下文为 LLM 传入的 args。
    把「跑个命令」类能力零代码化；安全默认值：框架自动转义模板插入值，
    原始拼接必须显式 ``{{ arg | raw }}`` 且框架输出警告。

    .. rubric:: 使用示例

    .. code-block:: yaml

        # run-tests/TOOL.fya
        name: run-tests
        type: cli
        description: 运行项目测试套件。在需要验证代码正确性时调用。
        shell: sh
        command: |
          cd {{ working_dir }} && python -m pytest {{ test_path }} --json-report
        args:
          working_dir:                    # 完整写法（无 default → 必填）
            type: string
            description: 项目根目录的绝对路径
          test_path:                      # 完整写法 + default（可选）
            type: string
            default: src/
            description: 测试路径
        output:
          type: object
          properties:
            exit_code:
              type: integer
            stdout:
              type: string
            stderr:
              type: string

    .. rubric:: 行为要点

    - ``shell`` 可选值：``sh`` （默认）/ ``bash`` / ``ps`` / ``powershell`` /
      ``cmd``；非法值在构造期抛 :class:`flowing.errors.FormatError`
      （声明期尽早报错，不留到执行期）。
    - 返回值恒为 ``{exit_code, stdout, stderr}`` 三字段字典（``output:``
      声明进入 `ToolDefinition.output_schema`，但不做字段提取）。
    - 非零退出码不等于 ``status="error"``——exit_code 是正常输出数据；
      仅进程无法启动等框架级失败才产生 ``error``。

    :raises flowing.errors.MissingSchemaError: 未声明 ``args`` 时（解析
      ``.fya`` 阶段）。

    .. seealso::

        - :class:`flowing.tool.RequestTool` —— 另一类零代码工具。
    """


    def __init__(
        self,
        *,
        definition: ToolDefinition,
        command: str,
        shell: Literal["sh", "bash", "ps", "powershell", "cmd"] = "sh",
    ) -> None:
        """构造 CLI 工具实例。

        :param definition: 工具声明；``params`` 必填（来自 ``.fya``
          ``args:``）。
        :param command: Jinja2 命令模板；插入值自动转义，``| raw`` 旁路并
          告警。
        :param shell: 执行 shell，默认 ``sh``；非法值构造期抛
          :class:`flowing.errors.FormatError` （fail fast，不留到执行期）。
        """
        if not definition.params_schema:
            raise MissingSchemaError("cli tools must declare args (no automatic inference source)")
        if shell not in _SHELL_EXECUTABLES:
            # 加载期 fail-fast（声明笔误），不留到执行期 KeyError
            raise FormatError(
                f"invalid shell declaration: {shell!r} (allowed values: "
                f"{', '.join(sorted(_SHELL_EXECUTABLES))})")
        self.definition = definition
        self.command = command
        self.shell = shell
        self._execution = None
        # 创建时编译一次模板（不在每次调用时编译）；
        # 模板语法错误（声明笔误）在构造期暴露
        self._template = _CLI_JINJA.from_string(command)
        # 创建时定内部校验模型（fya 声明经 params.schema_to_model
        # 桥接——definition.params_schema 即桥接产物 schema，再建最终模型）
        self._args_model = schema_to_model("Args", self.definition.params_schema)

    async def execute(self, **kwargs: Any) -> dict[str, Any]:
        """渲染命令模板 → shell 执行 → ``{exit_code, stdout, stderr}``。

        .. rubric:: 行为要点

        - 插入值自动经 ``shlex.quote`` 转义（finalize 单点）；显式
          ``{{ arg | raw }}`` 旁路并输出告警日志（每次渲染命中）。
        - 非零退出码不是 error——exit_code 是正常输出数据；仅子进程无法
          启动等框架级失败抛异常（由 ``__call__`` 包装为
          ``status="error"`` 结果）。
        - stdout / stderr 按 UTF-8 解码（``errors="replace"`` 容错）。
        """
        command = self._template.render(**kwargs)   # 渲染上下文 = LLM args
        executable = _SHELL_EXECUTABLES[self.shell]
        popen_kwargs: dict[str, Any] = {}
        if executable is not None:
            popen_kwargs["executable"] = executable
        proc = await asyncio.create_subprocess_shell(
            command, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, **popen_kwargs)
        stdout, stderr = await proc.communicate()
        return {
            "exit_code": proc.returncode,
            "stdout": stdout.decode("utf-8", errors="replace"),
            "stderr": stderr.decode("utf-8", errors="replace"),
        }


class RequestTool(Tool):
    
    """HTTP/HTTPS 请求工具实例——按 ``args`` 构造请求，零代码。

    .. rubric:: 功能介绍

    ``type: request`` 的实例类。``url`` 与 ``args`` 必填；参数到请求的
    映射自动完成，可用 ``body`` / ``query`` 显式覆盖。与 `CliTool` 同理：
    把「调一个 HTTP API」降为纯声明；凭证只经 ``{{ env.X }}`` 模板进入
    请求头，不进消息、不落盘。

    .. rubric:: 使用示例

    .. code-block:: yaml

        # create-order/TOOL.fya
        name: create-order
        type: request
        description: 创建新订单，在用户确认购物车内容后调用。
        url: https://api.example.com/v1/orders
        method: POST                       # 默认 POST
        headers:
          Authorization: "Bearer {{ env.SHOP_API_KEY }}"
        auth:                              # headers 的语法糖；冲突时 auth 优先
          type: api_key                    # basic / bearer / api_key
          header: X-API-Key
          value: "{{ env.API_KEY }}"
        args:
          user_id:
            type: string
            description: 用户 ID
          items:
            type: array
            description: 购物车商品列表
          currency: CNY                  # 糖：字面量 → {type: string, default: CNY}
        output:
          type: object
          properties:
            order_id:
              type: string
            total_amount:
              type: number
        timeout: 30                        # 秒，默认 30
        expected_status: [200, 201]        # 默认 [200, 201]

    .. rubric:: 行为要点

    - URL 模板中出现的 ``{{ arg_name }}`` 识别为路径参数，自动从
      body / query 排除；
    - 非路径参数按 ``method`` 决定去向：``POST`` / ``PUT`` / ``PATCH``
      → JSON body；``GET`` / ``DELETE`` → query string；``body`` /
      ``query`` 声明可显式覆盖；
    - ``auth`` 与 ``headers`` 同时声明时，``auth`` 生成的头优先；
    - 响应默认按 JSON 解析作为返回值；声明 ``output`` 时按 schema 提取
      字段，无关字段忽略（非 JSON 响应回退为文本）；
    - 响应状态码不在 ``expected_status`` 内 → ``status="error"`` 结果
      （含状态码与响应摘要），不抛异常。

    :raises flowing.errors.MissingSchemaError: 未声明 ``args`` 时。

    .. seealso::

        - :class:`flowing.tool.CliTool` —— 零代码工具的另一形态。
    """


    def __init__(
        self,
        *,
        definition: ToolDefinition,
        url: str,
        method: Literal["GET", "POST", "PUT", "PATCH", "DELETE"] = "POST",
        headers: dict[str, str] | None = None,
        auth: dict[str, Any] | None = None,
        query: list[str] | None = None,
        body: list[str] | None = None,
        expected_status: list[int] | None = None,
        timeout: float = 30.0,
    ) -> None:
        """构造 Request 工具实例。

        :param definition: 工具声明；``params`` 必填，``output_schema``
          对应 ``.fya`` 的 ``output:``。
        :param url: 端点 URL，支持 Jinja2 模板（路径参数占位）。
        :param method: HTTP 方法，默认 ``POST``。
        :param headers: 静态请求头，值支持模板。
        :param auth: 认证语法糖（``basic`` / ``bearer`` / ``api_key``）；
          与 ``headers`` 冲突时优先。
        :param query: 强制走 query string 的参数名列表。
        :param body: 强制走 JSON body 的参数名列表。
        :param expected_status: 预期成功状态码；``None`` 等价 ``[200, 201]``。
        :param timeout: 超时秒数，默认 30。
        """
        if not definition.params_schema:
            raise MissingSchemaError("request tools must declare args")
        self.definition = definition
        # URL 两阶段渲染：装配期先渲染 {{ env.X }}（非 env
        # 占位原样保留），执行期再渲染路径参数（见 execute）
        self.url = _ENV_URL_JINJA.from_string(url).render(
            env=MappingProxyType(os.environ))
        self.method = method
        # {{ env.X }} 模板装配期一次性渲染：凭证只经模板进
        # 请求头，不进消息、不落盘；缺失变量 FormatError fail fast
        self.headers = _render_env_templates(headers)
        self.auth = _render_env_templates(auth)
        # auth 语法糖在构造期展开为请求头（未知类型 FormatError fail fast）；
        # 执行期与 headers 冲突时本表优先（见类 docstring 行为要点）
        self._auth_headers: dict[str, str] = (
            _auth_headers(self.auth) if self.auth else {})
        self.query = query
        self.body = body
        self.expected_status = [200, 201] if expected_status is None else expected_status
        self.timeout = timeout
        self._execution = None
        # URL 模板的路径参数名在创建期提取（jinja2 AST 静态分析）——执行期
        # 这些参数从 body/query 排除（见类 docstring 行为要点第 1 条）
        self._path_params: frozenset[str] = frozenset(
            jinja2.meta.find_undeclared_variables(_ENV_JINJA.parse(self.url)))
        # 创建时定内部校验模型（fya 声明经 params.schema_to_model
        # 桥接——definition.params_schema 即桥接产物 schema，再建最终模型）
        self._args_model = schema_to_model("Args", self.definition.params_schema)

    async def execute(self, **kwargs: Any) -> Any:
        """按 args 构造 HTTP 请求并取回响应（args → 请求映射规则的执行体）。

        .. rubric:: 行为要点

        - URL 模板以 args 渲染（路径参数不转义——URL 结构由声明方负责），
          路径参数自动从 body / query 排除；
        - 其余参数去向：``query`` / ``body`` 声明显式覆盖优先；否则按
          ``method``——``POST`` / ``PUT`` / ``PATCH`` → JSON body，
          ``GET`` / ``DELETE`` → query string；
        - ``auth`` 展开的请求头与 ``headers`` 冲突时 auth 优先；
        - 响应状态码不在 ``expected_status`` 内 → 抛异常（含状态码与响应
          摘要），由 ``__call__`` 包装为 ``status="error"`` 结果，不向
          调用方抛；
        - 响应默认按 JSON 解析；声明了 ``output`` （``definition.output_schema``）时按 schema 的 properties 提取字段，无关字段
          忽略；非 JSON 响应回退为文本。
        """
        import httpx

        url = _ENV_JINJA.from_string(self.url).render(**kwargs)   # 路径参数渲染（不转义）
        headers = dict(self.headers or {})
        headers.update(self._auth_headers)   # auth 与 headers 冲突时 auth 优先
        query_names = set(self.query or ())
        body_names = set(self.body or ())
        body_methods = self.method in ("POST", "PUT", "PATCH")
        query_params: dict[str, Any] = {}
        json_body: dict[str, Any] = {}
        for key, value in kwargs.items():
            if key in self._path_params:
                continue   # URL 路径参数自动从 body/query 排除
            if key in query_names:   # query/body 显式覆盖优先
                query_params[key] = value
            elif key in body_names:
                json_body[key] = value
            elif body_methods:   # 未显式指定：按 method 默认去向
                json_body[key] = value
            else:
                query_params[key] = value   # GET/DELETE → query string
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.request(
                self.method, url, headers=headers,
                params=query_params or None, json=json_body or None)
        if resp.status_code not in self.expected_status:
            raise RuntimeError(
                f"request to {url} returned unexpected status {resp.status_code}"
                f" (expected {self.expected_status}): {resp.text[:500]}")
        try:
            data = resp.json()
        except json.JSONDecodeError:
            return resp.text   # 非 JSON 响应回退为文本
        output_schema = self.definition.output_schema
        if output_schema and isinstance(data, dict):
            fields = output_schema.get("properties")
            if fields:   # 声明 output 时按 schema 提取字段，无关字段忽略
                data = {k: data[k] for k in fields if k in data}
        return data


TOOL_NAMING = NamingRules(
    suffixes=(".tool.fya", ".fya", ".py"),
    generic_names=frozenset({"TOOL.fya", "TOOL.py", "tool.fya", "tool.py"}),
)
"""Tool 资源的路径形态身份名推断规则表（:class:`flowing.paths.NamingRules`）。

``TOOL.fya`` / ``TOOL.py`` / ``tool.fya`` / ``tool.py`` 通用文件名命中时
身份名取目录名；其余按后缀剥离取文件名（``.tool.fya`` 先于 ``.fya``），
结果经 snake → kebab 规范化。使用方：:func:`flowing.parser.normalize_entries`
（``naming=TOOL_NAMING``）与 name 断言的推断侧（`ToolRegistry.get`）。

.. seealso:: :data:`flowing.runtime.AGENT_NAMING`、
    :data:`flowing.plugins.skills.SKILL_NAMING`
"""


def _launch_project_root() -> "Path | None":
    """读 launch 上下文的项目根（``runtime._current_project_root``
    ContextVar）；无 launch 上下文（测试 / 裸用注册表）→ ``None``。
    内部 API，不属稳定契约。"""
    try:
        from flowing.runtime import _current_project_root   # 局部 import：破 tool → runtime 模块级循环边
        return _current_project_root.get()
    except Exception:
        return None


def _dir_candidates(name: str) -> list[str]:
    """``<name>/`` 目录内的定向查找链（首个存在者生效）：
    ``TOOL.fya > <name>.tool.fya > <name>.fya > TOOL.py > tool.py >
    <name_snake>.py``。内部 API。"""
    snake = kebab_to_snake(name)
    return ["TOOL.fya", f"{name}.tool.fya", f"{name}.fya",
            "TOOL.py", "tool.py", f"{snake}.py"]


_TOOL_FYA_RESERVED = frozenset({
    "type", "name", "description", "args", "output", "callable", "command",
    "shell", "url", "method", "headers", "auth", "query", "body",
    "expected_status", "timeout", "env", "tools", "overrides", "background",
})
"""TOOL.fya 保留字段集——其余字段原样落为 tool 实例的普通属性（如
``requires_approval``，见 `Tool` 行为要点「实例属性开放」；框架不解析、
不据此做任何自动行为）。``background`` 仅 script 型合法；cli / request /
mcp 声明 → ``FormatError`` （见 `_tool_from_fya`）。内部 API。"""


def _tool_from_fya(path: Path, identity: str) -> Tool:
    """``.fya`` 命中的实例化分派：按 ``type:`` 构造四型工具实例。内部 API。

    .. rubric:: 行为要点

    - ``name:`` 显式声明仅作一致性断言（不符 → ``NameMismatchError``）；
    - ``args:`` 经 :func:`flowing.params.expand_args_schema` 归一为
      properties（``mcp`` 例外：其 ``args`` 是启动命令参数列表而非参数
      schema——MCP 的参数 schema 由 ``list_tools()`` 拉取填充，属
      `McpTool` 装配链，本层只落声明字段）；
    - ``output:`` → ``ToolDefinition.output_schema``；
    - ``type`` 缺失或非法 → ``FormatError``；各类型必填字段缺失 →
      ``FormatError`` （``cli`` 缺 ``args`` / ``request`` 缺 ``args`` 由
      构造器的 ``MissingSchemaError`` 承载）。
    """
    from flowing.parser import load_fya_yaml   # 模块头依赖图保持单向（parser 不 import tool）

    fields = load_fya_yaml(path.read_text(encoding="utf-8"))
    tool_type = fields.get("type")
    if tool_type not in ("script", "cli", "request", "mcp"):
        raise FormatError(
            f"type of {path} is missing or invalid: {tool_type!r} (must be script / cli / request / mcp)")
    explicit_name = fields.get("name")
    if explicit_name is not None and explicit_name != identity:
        raise NameMismatchError(explicit_name, identity, str(path))
    description = fields.get("description")
    output_schema = fields.get("output")
    params = ({} if tool_type == "mcp"   # mcp 的 args 是命令参数，非参数 schema
              else expand_args_schema(fields.get("args") or {}))
    definition = ToolDefinition(
        name=identity, description=description or "",
        params_schema=params, output_schema=output_schema)
    if tool_type == "script":
        tool = _script_tool_from_fya(path, identity, fields, params)
    elif tool_type == "cli":
        command = fields.get("command")
        if command is None:
            raise FormatError(f"cli tool at {path} is missing the command field")
        tool = CliTool(definition=definition, command=command,
                       shell=fields.get("shell", "sh"))
    elif tool_type == "request":
        url = fields.get("url")
        if url is None:
            raise FormatError(f"request tool at {path} is missing the url field")
        tool = RequestTool(
            definition=definition, url=url,
            method=fields.get("method", "POST"), headers=fields.get("headers"),
            auth=fields.get("auth"), query=fields.get("query"),
            body=fields.get("body"),
            expected_status=fields.get("expected_status"),
            timeout=fields.get("timeout", 30.0))
    else:  # mcp
        tool = McpTool(
            definition=definition, command=fields.get("command"),
            args=fields.get("args"), env=fields.get("env"),
            url=fields.get("url"), headers=fields.get("headers"),
            tools=fields.get("tools"), overrides=fields.get("overrides"))
    # 非保留字段原样落为实例普通属性（框架不解析、不做任何自动行为）
    for key, value in fields.items():
        if key not in _TOOL_FYA_RESERVED:
            setattr(tool, key, value)
    # background 仅 script 型合法——script 显式落属性（值须布尔），
    # 其他型声明 → FormatError（解析期 fail fast，不静默忽略）
    if "background" in fields:
        if tool_type == "script":
            if not isinstance(fields["background"], bool):
                raise FormatError(f"background of {path} must be a boolean")
            tool.background = fields["background"]   # 显式 False 与缺省等价，但为一致性落属性无害
        else:
            raise FormatError(
                f"the background field of {path} is only supported for script tools (current type: {tool_type})")
    return tool


def _script_tool_from_fya(
    path: Path, identity: str, fields: dict[str, Any],
    params: dict[str, dict[str, Any]],
) -> Tool:
    """``type: script`` 的 TOOL.fya 装配：``callable: {路径}::{函数名或类名}``
    指针加载 → `ScriptTool` 子类实例化 / 裸函数提升。内部 API。

    .. rubric:: 行为要点

    - ``callable:`` 指向已打标函数 → ``FormatError`` （通道互斥：显式
      指针通道与自动提升通道二选一）；
    - fya ``args`` 声明存在时经 :func:`flowing.params.schema_to_model`
      桥接为校验模型（声明即模型）；缺省从 callable 签名构建
      （``_infer_from_execute``）；
    - fya 的显式 ``description`` / ``output`` 声明压过一切兜底来源
      （callable docstring 整体是函数路径的最后回退，0904）。
    """
    import importlib.util

    callable_ref = fields.get("callable")
    if not callable_ref or "::" not in str(callable_ref):
        raise FormatError(
            f"script tool at {path} must declare callable: <path>::<function-or-class-name>")
    path_part, _, symbol = str(callable_ref).partition("::")
    root = _launch_project_root()
    impl_path = resolve_path(
        path_part, project_root=root if root is not None else Path.cwd(),
        source_dir=path.parent)   # callable 路径相对 TOOL.fya 所在目录
    spec = importlib.util.spec_from_file_location(
        f"flowing_tool_file_{abs(hash(str(impl_path)))}", impl_path)
    module = importlib.util.module_from_spec(spec)   # type: ignore[union-attr]
    spec.loader.exec_module(module)   # type: ignore[union-attr]
    if not hasattr(module, symbol):
        raise FormatError(f"{symbol} does not exist in {impl_path} (callable: pointer missed)")
    target = getattr(module, symbol)
    description = fields.get("description")
    output_schema = fields.get("output")
    if isinstance(target, type) and issubclass(target, ScriptTool):
        # 0904 契约（同 _tool_from_py）：.fya callable 通道规范名 = 文件身份
        # identity（TOOL.fya 目录/文件名）；类体显式 name 与身份不符报错；
        # 无 definition 也无显式 name 时以身份注入（不再类名推断）
        explicit_name = target.__dict__.get("name")
        if explicit_name is not None and explicit_name != identity:
            raise NameMismatchError(explicit_name, identity, str(path))
        if explicit_name is None and "definition" not in target.__dict__:
            target.name = identity
        tool = target()
    elif callable(target):
        if hasattr(target, "__flowing_tool_name__"):
            raise FormatError(
                f"callable: points to the already-marked function {symbol} — "
                "the explicit-pointer and auto-promotion channels are mutually exclusive (declaration channels conflict)")
        args_model = (schema_to_model(f"{kebab_to_pascal(identity)}Args", params)
                      if params else _infer_from_execute(target))
        cls = type(kebab_to_pascal(identity), (ScriptTool,), {
            "execute": staticmethod(target),
            "name": identity,
            "description": description or _whole_doc(target.__doc__) or "",
            "args_model": args_model,
        })
        tool = cls()
    else:
        raise FormatError(f"{symbol} of {impl_path} is neither a function nor a ScriptTool subclass")
    # fya 显式声明（description / output）压过一切兜底来源（优先级链头部）
    if description is not None or output_schema is not None:
        d = tool.definition
        tool.definition = ToolDefinition(
            name=d.name,
            description=description if description is not None else d.description,
            params_schema=d.params_schema,
            output_schema=output_schema if output_schema is not None else d.output_schema,
            strict=d.strict)
    return tool


class ToolRegistry:
    """Runtime 全局工具注册表——``ns::name`` 全限定键 → Tool 实例。

    .. rubric:: 功能介绍

    每个 Runtime 持有一个实例（``runtime.tool_registry``）。注册时间线：
    无启动扫描（框架没有默认扫描目录）——核心工具（``finish`` /
    ``subagent-invoke``）随 ``Runtime.__init__`` 注册；插件工具在阶段一
    ``install()`` 注册；文件形态工具由 :meth:`get` 引用触发惰性解析并
    注册；MCP / CLI / Request 在 Agent 解析 ``.fya`` 时创建实例并注册。

    重名约束按 ``ns::name`` 全限定键判定：同一命名空间内重名 → 后注册者
    抛 ``ToolNameConflictError``；不同命名空间的同名工具允许共存。需要
    同一 MCP 服务器不同配置时用不同命名空间或规范名，需要相同实例时复用
    已有注册。裸名引用的注册表视图依次查 ``default::``、``builtin::``
    （``default`` 优先 = 插件覆盖原生行为的通道）；自定义命名空间的资源
    只能以 ``ns::name`` 全限定名引用。别名冲突不存在——别名是 `ToolEntry`
    层（Agent 本地）的概念。

    .. rubric:: 使用示例

    .. code-block:: python

        runtime.tool_registry.register(MakePayment())          # script 单例
        tool = runtime.tool_registry.get("make-payment")       # 按规范名取

    .. rubric:: 行为要点

    - 不变量：任意时刻一个 ``ns::name`` 全限定键至多映射一个 Tool 实例
      （不同命名空间同名允许共存）。
    - 不提供别名查找（别名在 `ToolEntry` 层）；不提供 unregister（销毁
      随 Runtime 生命周期）。

    .. seealso::

        - :attr:`flowing.runtime.Runtime.tool_registry` —— 挂载点。
        - :class:`flowing.tool.ToolEntry` —— 按 ``name_ori`` 查本表。
    """

    _tools: dict[str, Tool]
    """``ns::规范名`` → Tool 实例（命名空间规则见 ``flowing.runtime``）。
    内部 API，不属稳定契约。
    """

    def __init__(self) -> None:
        # spec 骨架无显式构造段：空注册表（无启动扫描——「无默认扫描目录」基调）
        self._tools = {}

    def register(self, tool: Tool, *, name: str | None = None,
                 namespace: str | None = None) -> None:
        """注册工具实例。

        :param tool: 工具实例；script 类型应注册全局单例。
        :param name: 规范名覆写；``None`` 时取 ``tool.definition.name``。
        :param namespace: 命名空间；``None`` → ``"default"``。注册表 key 为
          ``ns::name``——核心内置工具归 ``builtin::``；裸名引用的注册表
          视图依次查 ``default::``、``builtin::`` （``default`` 优先 =
          插件覆盖原生行为的通道），自定义命名空间只能以 ``ns::name``
          全限定名引用。
        :raises flowing.errors.ToolNameConflictError: ``ns::name`` 完整键
          （含命名空间）已存在（不同命名空间的同名工具允许共存）。
        """
        ns = namespace or "default"
        bare = name if name is not None else tool.definition.name
        key = f"{ns}::{bare}"
        if key in self._tools:
            raise ToolNameConflictError(f"tool with the canonical name already registered: {key}")  # 全键重名永远不允许
        self._tools[key] = tool
        tool.registry_key = key   # 回写全键（Entry 装配对文件派生工具落账 name_ori 的依据）

    def get(self, name_or_path: str, *,
            source_dir: Path | None = None) -> Tool:
        """工具的唯一解析入口——注册表快路径 + 文件链慢路径合一。

        .. rubric:: 功能介绍

        形态判别委托 :func:`flowing.paths.classify_ref` （词法唯一来源），
        三分语义：

        - 限定名（含 ``::``，如 ``myplugin::web-search``）：只查注册表
          精确键，不走文件查找链（命名空间无法反向映射到文件）；
        - 裸名（如 ``payment``）：``source_dir`` 提供时先走定向文件查找
          链（相对 ``source_dir``——文件覆盖注册表）：命中后按所在目录
          派生键（``@/`` 下相对、根外绝对、文件夹式取上层目录，仅作内部
          身份标识）短路复用已注册实例，未注册才实例化并注册；
          ``source_dir`` 缺省时跳过文件链。之后查注册表裸名视图——
          ``default::`` 优先于 ``builtin::`` （插件覆盖原生行为的通道）；
        - 路径形态：``@/`` 经 ``project_root`` 定位（无需 ``source_dir``，
          无 launch 上下文时无法锚定 → ``ValueError``）；``./`` / ``../``
          需 ``source_dir``，缺省时报错。跳过注册表，定位后走候选链；
          命中后同样注册（命名空间派生规则同上）。

        定向查找链（按规范名 ``<name>``，``<name_snake>`` 为其 snake_case
        形式，转换经 :func:`flowing.paths.kebab_to_snake`；首个存在者生效，
        探测委托 :func:`flowing.paths.probe_candidates`）：:

            目录内（<name>/ 存在时）：
                TOOL.fya > <name>.tool.fya > <name>.fya
                > TOOL.py > tool.py > <name_snake>.py
            目录外：
                <name>.tool.fya > <name>.fya > <name_snake>.py

        链上顺序只是确定性的先后规则——不推荐同一链路真的同时存在多个候选
        文件（读者需回溯优先级才能确定生效者）。

        .. rubric:: 行为要点

        - 形参承载规范名 / 限定名 / 路径，非别名——别名到规范名的换算在
          Agent 绑定层（``ToolEntry``）。
        - 继续向下仅裸名语境：裸名查找时目录存在但无合法入口 → 继续链上
          下一项；显式路径语境下目录无候选 → 直接报错（定点引用的目录为
          空几乎必为笔误）。
        - fail-fast 口径：裸名未注册时，``source_dir`` 提供则先走文件
          查找链、均不命中才报错；``source_dir`` 缺省则只查注册表、不
          命中即报错，不做文件探测。纯存在性检查用 ``__contains__``
          （仅认全限定键）。
        - 热路径口径：``ToolEntry.llm_definition()`` （每轮上下文组装）
          与 ``before_tool_call`` 审批路径调本方法时必命中注册表快路径
          ——Entry 在装配期已解析落账（文件命中的落账派生限定键，注册表
          命中的落账裸名），文件解析是声明期行为，运行时不触发文件 IO。
        - ``.fya`` 与同名 ``.py`` 并存 → 告警 + ``.fya`` 优先。
        - ``.py`` 命中后：恰好一个 ``@flowing_tool`` 打标函数 →
          ``_auto_generate_tool`` 提升；或恰好一个 `ScriptTool` 子类 →
          实例化；两者并存 / 多个打标函数 →
          :class:`flowing.errors.AmbiguousToolError`；皆无 →
          :class:`flowing.errors.FormatError`。
        - name 断言：命中对象的显式 ``name`` 声明（``.fya`` 字段 / 类
          属性 / 装饰器参数）必须与 ``<name>`` 一致，不符抛
          :class:`flowing.errors.NameMismatchError`；``<name>`` 的推断
          本体为 :func:`flowing.paths.infer_name` （规则表
          :data:`TOOL_NAMING`）。
        - 不做 glob 展开（``tools:`` 条目的 glob 在装配层展开后逐条进本
          方法）。

        :param name_or_path: 规范名（裸名）、限定名（``ns::name``）或
          路径形态字符串。
        :param source_dir: 裸名文件链的查找根与 ``./`` / ``../`` 的相对
          基准。缺省（``None``）时：裸名只查注册表（``default::`` /
          ``builtin::``），相对路径报错。声明期调用点（``.fya`` 装配、
          ``Agent.add_tool``）义务性传入引用方 Agent 的 ``source_file``
          所在目录——推荐经 :meth:`flowing.agent.Agent.get_tool` 自动携带。
        :return: 已注册的 Tool 实例。
        :raises flowing.errors.ToolNotFoundError: 注册表与查找链均不命中。
        :raises flowing.errors.FormatError: 显式路径目录无候选、``.py``
          无任何合法定义、或 ``callable:`` 指向已装饰函数。
        :raises ValueError: ``@/`` 路径无 launch 上下文（无法锚定项目根）。

        .. seealso::

            - :func:`flowing.tool.flowing_tool` —— 打标通道。
            - :meth:`flowing.runtime.Runtime.get_agent_class` ——
              同构的 Agent 解析管线。
        """
        # 精确键短路（先于形态判别）：文件派生限定键的命名空间含路径特征
        # （目录派生，如 "@/order-agent::payment" / 绝对路径形态），过不了
        # classify_ref 的限定名判别（左段含 / 会判成路径形态）——注册表在场
        # 证据优先于词法分流，docstring 承诺的「llm_definition / 审批路径
        # 必命中注册表快路径」靠此成立
        if name_or_path in self._tools:
            return self._tools[name_or_path]
        form = classify_ref(name_or_path)
        if form == "qualified":
            # 限定名（ns::name）：只查注册表精确键，不走文件查找链
            # （命名空间无法反向映射到文件；命中已在上方精确键短路返回）
            raise ToolNotFoundError(f"tool not registered: {name_or_path}")
        if form == "bare":
            if source_dir is not None:
                # 裸名 + source_dir：先走定向文件查找链（相对 source_dir——
                # 文件覆盖注册表）；命中后按所在目录派生键短路复用/实例化注册
                probed = self._probe_name_chain(name_or_path, source_dir)
                if probed is not None:
                    return self._resolve_hit(*probed, ref=name_or_path)
            # 注册表裸名视图：default:: 优先于 builtin::（插件覆盖原生行为
            # 的通道）；source_dir 缺省时跳过文件链、不命中即报错，不做文件探测
            for key in (f"default::{name_or_path}", f"builtin::{name_or_path}"):
                if key in self._tools:
                    return self._tools[key]
            raise ToolNotFoundError(f"tool not registered and no lookup chain hit: {name_or_path}")
        # 路径形态（慢路径，声明期行为）：@/ 锚 launch 上下文项目根（无需
        # source_dir）；./ ../ 需 source_dir（缺省 -> ValueError，resolve_path
        # 现有口径）。无 launch 上下文时 @/ 无法锚定 -> ValueError（编程错误）
        root = _launch_project_root()
        if root is None and name_or_path.replace("\\", "/").startswith("@/"):
            raise ValueError("@/ path resolution requires a launch context (_current_project_root not registered)")
        resolved = resolve_path(
            name_or_path,
            project_root=root if root is not None else Path.cwd(),   # 哑根：@/ 已在上方拒绝
            source_dir=source_dir)
        if resolved.is_dir():
            dir_name = infer_name(resolved, naming=TOOL_NAMING)   # 目录：basename 即目录名
            candidates = _dir_candidates(dir_name)
            hit = probe_candidates(resolved, candidates)
            if hit is None:
                # 显式路径语境：目录无候选 -> 直接报错（定点引用的目录为空
                # 几乎必为笔误），不继续向下
                raise FormatError(f"explicit path directory has no valid tool entry: {resolved}")
            return self._resolve_hit(hit, True, resolved, candidates,
                                     ref=name_or_path)
        if not resolved.exists() or not (
                resolved.name.endswith(".fya") or resolved.suffix == ".py"):
            raise ToolNotFoundError(f"tool not registered and no lookup chain hit: {name_or_path}")
        # 直指文件的显式路径：候选列表仅服务于 .fya/.py 并存告警
        identity = infer_name(resolved, naming=TOOL_NAMING)
        candidates = [resolved.name, f"{kebab_to_snake(identity)}.py"]
        return self._resolve_hit(resolved, False, resolved.parent, candidates,
                                 ref=name_or_path)

    def _probe_name_chain(
        self, name: str, source_dir: Path
    ) -> "tuple[Path, bool, Path, list[str]] | None":
        """裸名的定向文件查找链。内部 API，不属稳定契约。

        返回 ``(命中路径, 是否文件夹式命中, 探测基准目录, 候选名列表)``，
        全部未命中 → ``None`` （调用方继续查注册表裸名视图）。

        目录内（``<name>/`` 存在时）：``TOOL.fya > <name>.tool.fya >
        <name>.fya > TOOL.py > tool.py > <name_snake>.py``；目录存在但无
        合法入口 → 继续链上下一项（「继续向下」仅裸名语境）。目录外：
        ``<name>.tool.fya > <name>.fya > <name_snake>.py``。
        """
        directory = source_dir / name
        if directory.is_dir():
            candidates = _dir_candidates(name)
            hit = probe_candidates(directory, candidates)
            if hit is not None:
                return hit, True, directory, candidates
            # 裸名语境：目录存在但无合法入口 -> 继续链上下一项
        snake = kebab_to_snake(name)
        candidates = [f"{name}.tool.fya", f"{name}.fya", f"{snake}.py"]
        hit = probe_candidates(source_dir, candidates)
        if hit is not None:
            return hit, False, source_dir, candidates
        return None

    def _resolve_hit(self, hit: Path, folder_form: bool, base_dir: Path,
                     candidates: list[str], *, ref: str) -> Tool:
        """候选命中 → 派生键短路 / 实例化注册。内部 API，不属稳定契约。

        派生键 = ``<所在目录派生命名空间>::<身份名>`` （文件夹式资源取上层
        目录；``@/`` 下根相对、根外绝对，仅作内部身份标识）——已注册则
        短路复用（不重复实例化）；未注册则按后缀分派实例化（``.fya`` →
        :func:`_tool_from_fya`；``.py`` → :meth:`_tool_from_py`）并落账、
        回写 ``tool.registry_key``。
        """
        identity = infer_name(hit, naming=TOOL_NAMING)
        if hit.name.endswith(".fya"):
            # .fya 与同名 .py 并存 -> 告警 + .fya 优先（链上顺序已保证优先，
            # 此处只补告警）
            coexisting = [c for c in candidates
                          if c.endswith(".py") and (base_dir / c).exists()]
            if coexisting:
                warnings.warn(
                    f"a same-name .fya and .py coexist; the .fya wins: {hit}"
                    f" (coexisting: {', '.join(coexisting)})")
        ns_dir = hit.parent.parent if folder_form else hit.parent
        derived_key = f"{self._derived_namespace(ns_dir)}::{identity}"
        if derived_key in self._tools:
            return self._tools[derived_key]   # 派生键短路复用（文件解析是声明期行为）
        tool = (_tool_from_fya(hit, identity)
                if hit.name.endswith(".fya") else self._tool_from_py(hit, identity))
        self._tools[derived_key] = tool
        tool.registry_key = derived_key   # 回写全键（Entry 装配落账 name_ori 的依据）
        return tool

    @staticmethod
    def _derived_namespace(ns_dir: Path) -> str:
        """所在目录 → 派生命名空间字符串（``@/`` 下根相对、根外绝对——
        :func:`flowing.paths.to_project_path` 口径；无 launch 上下文时退
        化为绝对路径，与「根外绝对」一致）。内部 API，不属稳定契约。"""
        root = _launch_project_root()
        if root is not None:
            return to_project_path(ns_dir, project_root=root)
        return str(ns_dir)

    def _tool_from_py(self, path: Path, identity: str) -> Tool:
        """``.py`` 命中的实例化分派。内部 API，不属稳定契约。

        恰好一个 ``@flowing_tool`` 打标函数 → :func:`_auto_generate_tool`
        提升；恰好一个 `ScriptTool` 子类 → 实例化；两者并存 / 多个打标
        函数 / 多个子类 → ``AmbiguousToolError``；皆无 → ``FormatError``。
        子类的规范名 = 文件身份（``identity``）：类体显式 ``name`` 与身份
        不符 → ``NameMismatchError``（防错位）；类既无显式 ``name`` 也无
        ``definition`` 时以 ``identity`` 注入（0904 契约：不再由类名推断）。
        打标函数的装饰器参数断言在 :func:`_auto_generate_tool` 内。
        """
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            f"flowing_tool_file_{abs(hash(str(path)))}", path)
        module = importlib.util.module_from_spec(spec)   # type: ignore[union-attr]
        spec.loader.exec_module(module)   # type: ignore[union-attr]
        # 只认本文件定义的符号（__module__ 过滤掉 import 进来的）
        marked = [
            obj for obj in vars(module).values()
            if callable(obj) and hasattr(obj, "__flowing_tool_name__")
            and getattr(obj, "__module__", None) == module.__name__
        ]
        subclasses = [
            obj for obj in vars(module).values()
            if isinstance(obj, type) and issubclass(obj, ScriptTool)
            and obj is not ScriptTool and obj.__module__ == module.__name__
        ]
        if marked and subclasses:
            raise AmbiguousToolError(
                f"{path} contains both a @flowing_tool-marked function and a ScriptTool subclass")
        if len(marked) > 1:
            raise AmbiguousToolError(f"{path} contains multiple @flowing_tool-marked functions")
        if len(subclasses) > 1:
            raise AmbiguousToolError(
                f"{path} contains multiple ScriptTool subclasses (the spec does not list the multi-subclass form; "
                "it is routed to the AmbiguousToolError channel)")
        if not marked and not subclasses:
            raise FormatError(f"no @flowing_tool-marked function or ScriptTool subclass in {path}")
        if marked:
            return _auto_generate_tool(marked[0])
        cls = subclasses[0]
        # 0904 契约：ScriptTool 不再由类名推断 name。.py 文件通道的规范名 =
        # 文件身份（文件名/目录名，声明面权威，与 @flowing_tool 通道同源）：
        # 类体显式 name 与文件身份不符 -> NameMismatchError（防错位）；类既无
        # definition 也无显式 name 时以文件身份注入（不再回退类名推断）。
        explicit_name = cls.__dict__.get("name")
        if explicit_name is not None and explicit_name != identity:
            raise NameMismatchError(explicit_name, identity, str(path))
        if explicit_name is None and "definition" not in cls.__dict__:
            cls.name = identity
        return cls()

    def get_tool_class(self, name_or_path: str, *,
                       source_dir: Path | None = None) -> type[Tool]:
        """取已解析工具的类对象（与 ``Runtime.get_agent_class`` 对称）。

        .. rubric:: 功能介绍

        薄委托 ``type(self.get(...))``——不另开解析路径，单一解析权威仍是
        :meth:`get`；script 打标工具返回 ``_auto_generate_tool`` 提升出
        的 ``ScriptTool`` 子类。Agent 侧解析产物是类、Tool 侧注册产物是
        单例实例；编译发射、测试断言（``issubclass``）、子类化扩展等场景
        要的是类而非实例——薄委托保证类语义与实例语义永不漂移。

        .. rubric:: 行为要点

        - 解析 / 注册 / 缓存语义全部继承 :meth:`get` （含限定名只查注册
          表、命名空间派生、fail-fast 口径）。
        - 不绕过注册表直接加载文件；不实例化新对象（取的是已注册单例的
          类）。

        :param name_or_path: 同 :meth:`get`。
        :param source_dir: 同 :meth:`get`。
        :return: 已注册单例的类（``type(实例)``）。
        :raises flowing.errors.ToolNotFoundError: 同 :meth:`get`。

        .. seealso:: :meth:`get` —— 唯一解析入口；
            :meth:`flowing.runtime.Runtime.get_agent_class` —— 对称的
            Agent 侧入口。
        """
        return type(self.get(name_or_path, source_dir=source_dir))

    def __contains__(self, name: str) -> bool:
        """全限定键（``命名空间::规范名``）是否已注册。

        .. rubric:: 功能介绍

        ``x in registry`` 运算符的落点。只认全限定键：``"builtin::read"
        in registry`` 为真；裸名永不命中（``"read" in registry`` 恒为假，
        即便 ``builtin::read`` 在场）。裸名解析（``default::`` 优先、
        ``builtin::`` 兜底的优先级链）是 :meth:`get` 的专属职责——若
        ``in`` 也做裸名展开，同一对象上将存在两套语义重叠的查询通道，且
        ``in`` 的解析结果不可见（只回布尔值），排查更绕。保持 ``in``
        廉价、无歧义、零解析逻辑。

        .. rubric:: 使用示例

        .. code-block:: python

            "builtin::read" in runtime.tool_registry   # True（全限定键）
            "read" in runtime.tool_registry            # False——裸名请用 get()：
            runtime.tool_registry.get("read")          # 走命名空间优先级解析链
        """
        return name in self._tools

    def __iter__(self) -> Iterator[Tool]:
        """遍历全部已注册实例（顺序不保证）。"""
     
        return iter(self._tools.values())


_FLOWING_TOOL_MARKS: set[Callable[..., Any]] = set()
"""进程级打标表（``@flowing_tool`` 登记处）。

发现机制是函数对象上的 ``__flowing_tool_name__`` 标记属性（``.py`` 命中后
模块扫描逐对象检查）；本表仅作打标行为的登记——import 期不需要 Runtime
存在，多 Runtime 安全。内部 API，不属稳定契约。
"""


def flowing_tool(fn: Callable[..., Any] | None = None, *,
                 name: str | None = None) -> Any:
    """``@flowing_tool`` 打标装饰器——把裸函数标记为 script 工具。

    .. rubric:: 功能介绍

    script 工具的两条定义通道之一（另一条是手写 `ScriptTool` 子类）。
    装饰器只打标不注册：在函数上记录标记（进程级打标表），实例化与注册
    推迟到工具被引用时由 :meth:`flowing.tool.ToolRegistry.get` 完成——
    import 期不需要 Runtime 存在，多 Runtime 安全。

    两种用法：``@flowing_tool`` （名字由文件名 / 目录名推断）或
    ``@flowing_tool("make-payment")`` （参数仅作一致性断言——必须与推断名
    一致，不符抛 :class:`flowing.errors.NameMismatchError`）。定义处的
    显式打标让「这个函数是工具」成为作者意图而非框架猜测。

    .. rubric:: 行为要点

    - 每个 ``.py`` 文件至多一个打标函数；与 Tool 子类同文件并存或含多个
      打标函数 → :class:`flowing.errors.AmbiguousToolError`。
    - 通道互斥：被 ``TOOL.fya`` 的 ``callable: {路径}::{函数名}`` 指向的
      函数不允许打标（显式指针通道与自动提升通道二选一），违反抛
      :class:`flowing.errors.FormatError`。
    - 名字推断：文件名去 ``.py`` 并 snake → kebab 规范化；``TOOL.py`` /
      ``tool.py`` 通用名时取目录名。
    - 不实例化 Tool、不触碰任何 Runtime / 注册表；返回原函数。

    :param fn: 被装饰函数（无参用法由 Python 装饰器协议传入）。
    :param name: 一致性断言参数；``None`` 时纯推断。

    .. seealso::

        - :class:`flowing.tool.ScriptTool` —— 另一条定义通道。
        - :meth:`flowing.tool.ToolRegistry.get` —— 消费点。
        - :class:`flowing.errors.NameMismatchError` —— 断言失败。
    """
    # 打标：fn.__flowing_tool_name__ = name（None = 纯推断）；登记进程级
    # 打标表；返回原函数；实例化/注册推迟到 get（import 期无 Runtime 依赖）
    if isinstance(fn, str):
        # @flowing_tool("make-payment") 位置形态（spec 使用示例）：字符串
        # 实参即一致性断言名（关键字形态 name=... 等价）
        name, fn = fn, None

    def _mark(f: Callable[..., Any]) -> Callable[..., Any]:
        f.__flowing_tool_name__ = name  # type: ignore[attr-defined]
        _FLOWING_TOOL_MARKS.add(f)
        return f

    return _mark(fn) if fn is not None else _mark


def _infer_from_execute(execute: Callable[..., Any]) -> "type[BaseModel]":
    """从 ``execute()`` 签名构建 Pydantic 参数模型。内部 API，不属稳定契约。

    .. rubric:: 行为要点

    - 来源：参数类型标注 → 字段类型（``str`` / ``int`` / ``float`` /
      ``bool`` / ``list`` / ``dict`` 等直接映射，复杂标注交 Pydantic）；
      默认值 → 字段 default（有默认 → 可选，无 → 必填）；``caller``
      参数跳过（框架注入，不是 LLM 参数）；``*args`` / ``**kwargs``
      形态跳过（不进 schema）。
    - 构建经 ``pydantic.create_model`` （与 ``.fya`` 桥接
      :func:`flowing.params.schema_to_model` 同一建模入口）。
    - :raises flowing.errors.MissingSchemaError: 任一业务参数缺类型标注
      （构建不出字段类型）。

    .. seealso::

        - :class:`flowing.tool.ScriptTool` —— 调用点。
    """
    import inspect

    fields: dict[str, Any] = {}
    for param_name, param in inspect.signature(execute).parameters.items():
        if param_name in ("self", "caller"):
            continue  # caller 为框架注入，不是 LLM 参数
        if param.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
            continue  # **kwargs（如 FinishTool 动态字段）不进 schema
        if param.annotation is inspect.Parameter.empty:
            raise MissingSchemaError(f"parameter lacks a type annotation: {param_name}")
        if param.default is inspect.Parameter.empty:
            fields[param_name] = (param.annotation, ...)   # 无默认 → 必填
        else:
            fields[param_name] = (param.annotation, param.default)  # 有默认 → 可选
    from pydantic import create_model
    ArgsModel: type[BaseModel] = create_model("InferredArgs", **fields)
    return ArgsModel


def _auto_generate_tool(fn: Callable[..., Any]) -> Tool:
    """把打标函数（``@flowing_tool``）包装为 `ScriptTool` 子类实例。
    内部 API，不属稳定契约。

    .. rubric:: 行为要点

    - 等价于 ``type("<PascalName>", (ScriptTool,), {"execute":
      staticmethod(fn)})`` 后实例化；元信息按「文件名 / docstring / 类型
      标注」提取（名字推断与装饰器参数断言规则见 :func:`flowing_tool`）。
    - :raises flowing.errors.MissingSchemaError: 参数缺类型标注。
    - :raises flowing.errors.AmbiguousToolError: 同一 ``.py`` 同时存在
      打标函数与 Tool 子类、或多个打标函数（由定向查找层抛出，不在本
      函数内）。

    .. seealso::

        - :class:`flowing.tool.ScriptTool` —— 两条定义路径的共同产物。
        - :func:`flowing_tool` —— 打标入口。
    """
    args_model = _infer_from_execute(fn)  # 元信息提取：签名构建模型
    # name ← 文件名去 .py 并 snake → kebab 规范化（TOOL.py/tool.py 通用名
    # 取目录名）；装饰器参数若给出仅作一致性断言（不符 -> NameMismatchError）
    name = infer_name(fn.__code__.co_filename, naming=TOOL_NAMING)
    declared = getattr(fn, "__flowing_tool_name__", None)
    if declared is not None and declared != name:
        raise NameMismatchError(declared, name, fn.__code__.co_filename)
    # description 三级回退链在打标函数形态下的落点：显式声明无通道
    # （装饰器无 description 形参）、无类 docstring —— 取函数 docstring 首段
    description = _whole_doc(fn.__doc__) or ""
    cls = type(kebab_to_pascal(name), (ScriptTool,), {
        "execute": staticmethod(fn),
        "name": name,
        "description": description,
        "args_model": args_model,
    })
    return cls()
