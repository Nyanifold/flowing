"""Flowing 工具系统（``flowing.tool``）——能力三正交中的 Tool 维度。

.. rubric:: 功能介绍

本模块定义 Flowing 的 Tool 子系统公开类型：可执行对象（`Tool` /
`ScriptTool` 及三种类型的具体实现 `McpTool` / `CliTool` / `RequestTool`）、
LLM 可见声明（`ToolDefinition`）、Agent 级绑定（`ToolEntry`）、
调用与结果（`ToolCall` / `ToolResult` / `ToolStatus`）、
全局注册表（`ToolRegistry`）、类型标记（`ToolType`）、装饰器
（`flowing_tool`）与媒体族（`Image` / `File` / `Audio` / `Video` /
`MediaConverter` / `register_media_converter` / `normalize_output` /
`output_to_blocks`）。
框架自带工具（核心内置 ``SubagentInvokeTool`` 与标准件 ``FinishTool``
等六个文件/shell 工具）在 :mod:`flowing.builtins`（P3-06 裁决）。

能力三正交（Tool / 子 Agent / Skill 共享的同一模式，不可合并）：

=============  ========================  =================  =======================
概念           Tool                      子 Agent           Skill
=============  ========================  =================  =======================
可执行对象     `Tool`（``execute()``）   Agent 类           Skill 内容
LLM 可见声明   `ToolDefinition`          catalog XML 条目   catalog XML 条目
Agent 级绑定   `ToolEntry`               ``SubagentEntry``  ``SkillEntry``（扩展）
=============  ========================  =================  =======================

绑定层的存在理由：同一个 `FinishTool` 在 ReviewerAgent 上 LLM 应看到
``score``/``pass``/``issues``，在 PaymentAgent 上应看到
``transaction_id``/``status``/``charged_amount``——通过 entry 覆写而不改全局
注册表。

.. rubric:: 设计动机

- **机制 vs 策略**：框架只提供「工具是什么、如何声明、如何调度、在哪挂钩子」
  的机制；「什么该被审批、参数该取什么值」是策略，由 `before_tool_call`
  handler、Composable 与应用层决定。框架**不做框架级 interrupt**——审批在
  handler 内部 ``await`` 暂停实现。
- **三正交分离**：`Tool` 只对执行负责，`ToolDefinition` 只对「向 LLM 说清楚
  自己是什么」负责（可序列化纯数据），`ToolEntry` 只对「这个 Agent 如何
  使用这个 Tool」负责。三者各自演化互不侵入。
- **最小核心**：Skill 完全经由 Tool 机制实现（``skill-load`` 是普通工具），
  可整体剥离；Tool 在逻辑 Turn 引擎中有硬编码调用点，不可剥离。

.. rubric:: 工具调用流水线（时序不变量）

LLM 在一次逻辑 Turn 中发出工具调用后，框架按以下**固定顺序**处理：

1. ``Agent.tool_call(tool_call)`` 收到 `ToolCall`，**仅按别名**
   （``tool_call.name``）在 ``agent._tool_entries`` 中查找 `ToolEntry`，
   不回退规范名；找不到抛 ``UnknownToolError``。
2. dispatch ``before_tool_call`` 钩子（value 即 `ToolCall`）：
   handler 可改写 ``tool_call.args`` 后返回；可将 ``tool_call.shortcut``
   置 ``True`` 短路后续 handler；可 ``raise Intercepted`` 硬阻断——
   框架捕获后返回 `ToolResult.blocked(...)`，**不执行**工具。
3. ``Agent._normalize()``（内部 API，不属稳定契约）在钩子**之后**执行：
   **LLM 视角校验**（别名命名、隐藏参数已排除的 schema）→ 别名映射
   回规范名 → specified 惰性求值注入（固定值直给；注入表达式
   ``{{ self.inject('key') }}`` 在此沿 provide 链上溯取值）→
   schema 默认值填充。即 `ToolEntry.resolve()` 的聚合在此发生。
4. `Tool.__call__` 调度层：``caller`` 自动传入（若签名声明）、**内部
   校验**（S-33 裁决落点：聚合终值对创建时编译的 ``_args_model``
   Pydantic 模型，失败走框架错误通道、在 ``try`` 之外上抛）、Task 包装
   （cancel 注入）、返回值包装为 `ToolResult`。
5. 结果归一化与塑形：``Agent.tool_call`` 收尾对 `ToolResult.output`
   幂等归一（`normalize_output`，封 shortcut / 钩子改写两缝，D19）；
   随后经 `ToolResult.as_message()` 塑形（内部调 `output_to_blocks`，
   D22）转为 ``kind=TOOL`` 消息进入消息级树（``Message.id`` +
   ``parent_id`` 链），消息完整后 append 落盘。配对元数据在消息字段
   （``tool_call_id`` / ``tool_status``），``content`` 只含纯内容块
   （D1/D3）。

.. rubric:: 参数合并优先级（定稿）

::

    specified（含注入表达式）  >  LLM args  >  schema 默认值

- **specified 是防 LLM 篡改通道**（``user_id`` / ``trace_id`` 等敏感值
  不经过 LLM 消息、不可被 LLM 覆盖）——这是安全边界基石。注入
  （旧 ``inject`` 键，R-4 裁决已删除）不再有独立通道：specified 值写
  Parsable 表达式 ``"{{ self.inject('user_id') }}"`` 即注入——调用时
  以调用方 Agent 为上下文求值、沿 provide 链上溯（链断裂抛
  ``MissingProvideError``）。
- specified 参数（固定值与注入表达式）对 LLM **不可见**（从 LLM schema
  中移除），因此正常情况下三来源不会真正冲突；优先级链只在防御性场景
  （LLM hallucinate 出同名参数）下生效。
- Skill 的参数优先级方向**一致**（specified 同为最高），见
  ``flowing.plugins.skills`` 规约——「specified 参数对 LLM 不可见 ⇒ 幻觉
  同名参数不采信」是全框架统一的安全不变量（M-54 最终裁决）。

.. rubric:: 审批模式（无框架级 interrupt）

``requires_approval`` 是工具 ``.fya`` 的**非保留字段**：框架不解析、不检查、
不以此做任何自动行为，它直接成为 tool 对象的普通属性。审批完全在
``before_tool_call`` handler 内部 ``await`` 实现：不需要审批 → 原值通过；
用户改参数 → 返回修改后的 `ToolCall` 传给下一个 handler；用户拒绝 →
``raise Intercepted`` → 调度链停止，框架生成 `ToolResult.blocked(...)`。
调度器看到的只是「一个返回较慢的 handler」。

.. rubric:: 文档 14 对齐

本模块不出现任何物理 Turn 结构。「Turn 结束」一律指
**逻辑执行阶段**结束（`TurnContext` 收尾），载体是消息级树；工具结果以
``kind=TOOL`` 消息落树，与逻辑 Turn 无物理绑定。

.. rubric:: 稳定性

本模块全部公开签名属跨版本稳定契约；``_`` 前缀符号
（`_infer_from_execute` / `_auto_generate_tool` / `Tool._has_caller` /
`Tool._execution` / `Tool._args_model`）为内部 API，不属稳定契约。
``_coerce`` 的唯一声明在 :mod:`flowing.params`（C-08 裁决），本模块
经 import 使用。

.. seealso::

    - :mod:`flowing.agent` —— ``Agent.tool_call`` / ``Agent._normalize`` /
      ``Agent._tool_entries`` 的完整时序。
    - :mod:`flowing.hooks` —— ``before_tool_call`` 钩子的 dispatch 算法
      （改写链 / shortcut / Intercepted 重抛）。
    - :mod:`flowing.params` —— 参数声明（BaseModel / JSON Schema）与桥接。
    - :mod:`flowing.message` —— ``kind=TOOL`` 消息与消息级树。
"""

from __future__ import annotations   # S-43 裁决③：注解延迟求值，配合 TYPE_CHECKING 破注解级循环边

import asyncio
import base64
import binascii
import hashlib
import inspect
import json
import logging
import mimetypes
import os
import warnings
from collections.abc import Callable, Iterator
from dataclasses import asdict, dataclass, field, is_dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from flowing.errors import (
    AmbiguousMcpSourceError,
    AmbiguousToolError,
    FormatError,
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

from flowing.params import _coerce, apply_param_overrides, expand_args_schema, schema_to_model
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
"""工具执行结果状态四值（定稿）。

- ``"completed"``：正常完成，``output`` 承载返回值。
- ``"pending"``：异步收据——``execute`` 返回了 ``asyncio.Task``，框架只回
收据，真正结果稍后以独立消息到达（fire-and-forget 语义边界）。
- ``"blocked"``：被 ``before_tool_call`` 钩子 ``raise Intercepted`` 硬阻断，
工具**未执行**；只能经 `ToolResult.blocked` 工厂产生。
- ``"error"``：执行抛异常的**正常产物**——LLM 可见、**不触发**任何错误
钩子（核心错误钩子仅 ``on_provider_error``，见 :mod:`flowing.hooks`）。
"""

ToolType = Literal["script", "mcp", "cli", "request"]
"""四种工具类型判别值（``.fya`` 的 ``type:`` 字段取值，定稿）。

- ``"script"``：Python callable（`ScriptTool` 子类 / 裸函数 / ``callable:``
指向），**全局单例**注册，所有 Agent 共享同一实例。
- ``"mcp"``：MCP 服务器（stdio / 远程），每定义独立实例。
- ``"cli"``：命令行工具（Jinja2 命令模板），每定义独立实例。
- ``"request"``：HTTP/HTTPS 请求，每定义独立实例。

script 是单例因为其业务逻辑是用户代码，不应实例化多次；其余三类只是
参数化配置，无用户代码。
"""


# ──────────────────────────────────────────────────────────────────
# 结果归一化与媒体通道（05 定案：D4–D10、D12、D19、D22）
# ──────────────────────────────────────────────────────────────────


def _validate_carrier(carrier: "Image | File | Audio | Video") -> None:
    """载体四类共用构造校验（作者 bug → 框架错误通道 ValueError）。

    - ``data`` / ``path`` 至少其一；
    - ``path`` 须为绝对路径。
    """
    if carrier.data is None and carrier.path is None:
        raise ValueError(f"{type(carrier).__name__} 的 data 与 path 至少其一")
    if carrier.path is not None and not Path(carrier.path).is_absolute():
        raise ValueError(
            f"{type(carrier).__name__}.path 须为绝对路径: {carrier.path!r}")


@dataclass
class Image:
    """执行层媒体载体（图片）——工具作者侧词汇，Block 对作者彻底透明（D6）。

    .. rubric:: 功能介绍

    `execute()` 返回媒体的**可选元数据包装**（不是必经路径）：裸 ``bytes``
    / ``pathlib.Path`` 直收；需要显式 MIME、文件名、或推翻推断结果
    （如 ``File(path="x.png")`` 强制文件块而非图片块）时才用本载体。
    第三方库对象（如 ``PIL.Image.Image``）也直收（D10 注册表）。命名无
    ``Block`` 后缀 = 层次宣言：作者接口面（含直构 `ToolResult` 的逃生舱）
    完全不出现消息层 Block。

    .. rubric:: 行为规约

    - ``data`` / ``path`` 至少其一，都没有 → ``ValueError``（作者 bug，
      框架错误通道）；``path`` 须绝对路径，相对路径 → ``ValueError``。
    - MIME 路由（D7）：推断链 = 显式 ``mime_type`` > path 后缀
      （``mimetypes``）> bytes 魔数 > 来源库元数据；``image/*`` →
      ``ImageBlock``、``audio/*`` → ``AudioBlock``、``video/*`` →
      ``VideoBlock``、其余/推不出 → ``FileBlock``（**宁文件勿图**）。
      显式载体声明永远压过推断。
    - 文件名（D8）：产物 ``MediaBlock.name`` 必填；填充链 = 显式
      ``name`` > ``path`` 文件名 > 合成 ``<sha256(data)[:12]>.<ext>``
      （ext 由 MIME 反推，MIME 未知 → ``.bin``）。
    - 载体 → 块的 I/O 转换只发生在 `normalize_output`（async）。

    .. seealso::

        - :func:`flowing.tool.normalize_output` —— 载体 → 块的唯一转换点。
        - :class:`flowing.message.ImageBlock` —— 转换产物（消息层）。
    """

    data: bytes | str | None = None
    """bytes 原始数据 / str base64；与 ``path`` 至少其一。"""
    path: str | os.PathLike | None = None
    """绝对路径（收 ``pathlib.Path``）；相对路径 → ``ValueError``。"""
    mime_type: str | None = None
    """显式 MIME；缺省走推断链（D7），显式声明永远压过推断。"""
    name: str | None = None
    """文件名；缺省 = path 文件名 > hash.ext 合成（D8）。"""

    def __post_init__(self) -> None:
        _validate_carrier(self)


@dataclass
class File:
    """执行层媒体载体（文件）——与 `Image` 同构（D6）。

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
    """执行层媒体载体（音频）——与 `Image` 同构（D6），产物 ``AudioBlock``。"""

    data: bytes | str | None = None
    path: str | os.PathLike | None = None
    mime_type: str | None = None
    name: str | None = None

    def __post_init__(self) -> None:
        _validate_carrier(self)


@dataclass
class Video:
    """执行层媒体载体（视频）——与 `Image` 同构（D6），产物 ``VideoBlock``。"""

    data: bytes | str | None = None
    path: str | os.PathLike | None = None
    mime_type: str | None = None
    name: str | None = None

    def __post_init__(self) -> None:
        _validate_carrier(self)


@dataclass
class MediaConverter:
    """第三方库对象 → 执行层载体的转换注册表条目（D10）。

    .. rubric:: 行为规约

    - 条目统一为 ``(module, qualname, convert)`` 三元组；匹配算法
      ``any(f"{c.__module__}.{c.__qualname__}" == target
      for c in type(obj).__mro__)``——沿 MRO 查全名，**子类命中**。
    - 天然惰性：下游任何时候 import 的库下一次判别即生效，无初始化
      快照问题；模块 reload 后旧类对象仍命中。
    - 非行为：ABC 虚拟子类（``__subclasshook__``）不命中；同模块同名
      伪造不防。
    """

    module: str
    """库模块全名，如 ``"PIL.Image"``。"""
    qualname: str
    """类限定名，如 ``"Image"``。"""
    convert: Callable[[Any], "Image | File | Audio | Video"]
    """第三方对象 → 执行层载体四类之一的转换函数。"""


def _pil_image_convert(img: Any) -> "Image":
    """内置 Pillow 转换器（D10）：``PIL.Image.Image`` → PNG bytes 的 `Image` 载体。

    PIL 未安装不影响本定义的存在——匹配走 MRO 全名字符串，不 import PIL；
    convert 被调用时 ``PIL.Image.Image`` 实例已存在，PIL 必然已装入。
    """
    import io

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return Image(data=buf.getvalue(), mime_type="image/png")


_MEDIA_CONVERTERS: list[MediaConverter] = [
    # 内置条目（D10）：Pillow——PIL.Image.Image 实例（含子类）经 MRO 全名
    # 匹配命中，convert 取 PNG bytes 包 Image 载体（mime_type="image/png"）
    MediaConverter("PIL.Image", "Image", convert=_pil_image_convert),
]
"""媒体转换注册表（模块级）。匹配算法与语义见 `MediaConverter`（D10）。"""


def register_media_converter(
    tp_or_module: type | str,
    qualname: str | None = None,
    convert: Callable[[Any], "Image | File | Audio | Video"] | None = None,
) -> None:
    """注册第三方库对象 → 媒体载体的转换器（D10）。

    .. rubric:: 行为规约

    - 直传 ``type`` 对象时降级存名字（``tp.__module__`` /
      ``tp.__qualname__``）——全库只有 MRO 全名一种匹配机制。
    - :raises ValueError: 同 ``(module, qualname)`` 重复注册。

    .. rubric:: 使用示例

    .. code-block:: python

        register_media_converter(
            "PIL.Image", "Image",
            lambda img: Image(data=_png_bytes(img), mime_type="image/png"))
        register_media_converter(pydub.AudioSegment, convert=...)  # 直传 type

    :param tp_or_module: ``type`` 对象（降级存名字）或模块全名字符串。
    :param qualname: 类限定名；``tp_or_module`` 为 ``type`` 时省略。
    :param convert: 转换函数，产物为执行层载体四类之一。
    """
    # 归一为 (module, qualname, convert) 三元组存 _MEDIA_CONVERTERS；
    # 同 (module, qualname) 重复注册 → ValueError
    if isinstance(tp_or_module, type):
        # 直传 type 降级存名字（全库只有 MRO 全名一种匹配机制）
        if qualname is not None:
            raise ValueError("tp_or_module 为 type 时 qualname 应省略")
        module, qname = tp_or_module.__module__, tp_or_module.__qualname__
    else:
        module, qname = tp_or_module, qualname
    # 三要素缺失属调用方笔误（spec 未具名异常类型，按编程错误通道 ValueError）
    if not module or not qname or convert is None:
        raise ValueError(
            "register_media_converter 需要 module / qualname / convert 三要素")
    if any(c.module == module and c.qualname == qname for c in _MEDIA_CONVERTERS):
        raise ValueError(f"媒体转换器重复注册: {module}.{qname}")
    _MEDIA_CONVERTERS.append(
        MediaConverter(module=module, qualname=qname, convert=convert))


def _sniff_mime(data: bytes) -> str | None:
    """bytes 魔数嗅探（推测点 8 定稿）：只内置常见魔数，推不出返回 ``None``
    （调用方按「宁文件勿图」落 ``FileBlock``）。"""
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
    """D7 MIME 路由：``image/*`` / ``audio/*`` / ``video/*`` → 对应块；
    其余 / 推不出 → ``FileBlock``（宁文件勿图）。"""
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
    """D8 文件名合成：``<sha256(data)[:12]>.<ext>``（ext 由 MIME 反推，未知 → ``.bin``）。"""
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

    - MIME 推断链（D7）：显式 ``mime_type`` > path 后缀（``mimetypes``）>
      bytes 魔数；``forced`` 非 None（显式载体声明）时块类别不再经路由。
    - 文件名填充链（D8）：显式 ``name`` > path 文件名 > hash.ext 合成。
    """
    if isinstance(data, str):
        # data str = base64 形态（见 Image.data 字段注释）；解码为 raw 统一处理
        try:
            raw = base64.b64decode(data, validate=True)
        except binascii.Error as exc:
            raise ValueError(f"载体 data 字符串须为合法 base64: {exc}") from exc
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
"""显式载体四类 → 强制块类别（显式声明永远压过推断，D7）。"""


def _find_media_converter(obj: Any) -> MediaConverter | None:
    """D10 注册表匹配：沿 ``type(obj).__mro__`` 查 ``module.qualname`` 全名，子类命中。"""
    mro_names = {f"{c.__module__}.{c.__qualname__}" for c in type(obj).__mro__}
    for converter in _MEDIA_CONVERTERS:
        if f"{converter.module}.{converter.qualname}" in mro_names:
            return converter
    return None


async def normalize_output(value: Any) -> Any:
    """把 ``execute`` 返回值 / ``ToolResult.output`` 原料归一化（浅层判别，D5）。

    .. rubric:: 功能介绍

    产出**五形态**（D4）：``None`` | 基础值原样 | 单块 | 纯基础 list |
    混合 list（基础成员原样保留 + 非基础成员已转块）。**幂等**——已归一
    的值再跑一遍结果不变（块原样放行、基础保留），这是 D19 双调用点
    （`Tool.__call__` + ``Agent.tool_call`` 收尾）安全的前提。内部 API。

    「基础类型」：``None`` / ``bool`` / ``int`` / ``float`` / ``str`` /
    ``dict`` / ``list`` / ``tuple`` / dataclass 实例 / pydantic
    ``BaseModel`` 实例（JSON 兼容及其常见载体）。

    .. rubric:: 行为规约

    1. ``value`` 是 ``list`` / ``tuple``（顶层；tuple 归一为 list）——
       浅层检查每个成员（**不递归**）：全基础 → 原样返回（JSON 形态）；
       有非基础成员 → 逐成员：基础成员原样保留、非基础成员单独转块
       （D9 混排不报错）。
    2. ``value`` 非 list/tuple：基础类型 → 原样返回；非基础 → 合法块
       原样 / 可转化转单块 / 违禁块 ``ValueError`` / 不可转化
       ``ValueError``（同 D12 通道）。

    - 合法块 = ``TextBlock`` / ``StructBlock`` / ``MediaBlock`` → 原样
      放行。
    - 载体四类 / 裸 ``bytes`` / ``Path`` / 注册表命中对象 → 转对应块
      （async，可读盘；MIME 路由 D7、文件名合成 D8）。
    - 违禁块（D12）：``ToolCallBlock`` / ``ThinkingBlock`` 出现在结果中
      （任何层级、任何路径）→ ``ValueError``（框架错误通道，作者 bug）。
    - 不可转换对象 → ``ValueError``（框架错误通道，作者 bug——返回值形态
      由工具编写方决定，不可能由 LLM 调用产生；D11）。
    - 媒体 → 块的 I/O 转换只发生在本方法（async）；塑形（五形态 →
      content 块列表）是纯同步，见 `output_to_blocks`。
    - 边缘情况：深层埋藏的非 JSON 对象（``[{"a": pil_image}]``）归一化期
      放行，`as_message` 塑形时 ``StructBlock`` 构造校验失败 →
      ``ValueError``（诚实失败点，不追求早发现）。

    .. seealso::

        - :func:`flowing.tool.output_to_blocks` —— 下游塑形统一出口（D22）。
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
            # 违禁块（D12）：浅层出现 -> 框架错误通道（作者 bug）；深层埋藏
            # 的块由塑形期 StructBlock 构造校验同通道兜住（D5 诚实失败点）
            raise ValueError(f"工具结果中出现违禁块类型: {type(v).__name__}")
        if isinstance(v, ContentBlock):
            # 合法块（TextBlock/StructBlock/MediaBlock）原样放行；其余块类型
            # 按违禁同通道处理（D12 兜底）
            if isinstance(v, (TextBlock, StructBlock, MediaBlock)):
                return v
            raise ValueError(f"工具结果中出现违禁块类型: {type(v).__name__}")
        if isinstance(v, (Image, File, Audio, Video)):
            # 载体四类：显式声明压过推断（D7），块类别由载体类型强制
            return await _media_to_block(
                data=v.data, path=v.path, mime_type=v.mime_type, name=v.name,
                forced=_CARRIER_BLOCK[type(v)])
        if isinstance(v, bytes):
            return await _media_to_block(
                data=v, path=None, mime_type=None, name=None, forced=None)
        if isinstance(v, Path):
            # 裸 Path 与载体同口径：相对路径 → ValueError（spec 未单列裸
            # Path 的相对路径处置，按载体规则同口径落实——见报告对照表）
            if not v.is_absolute():
                raise ValueError(f"裸 Path 结果须为绝对路径: {v!r}")
            return await _media_to_block(
                data=None, path=v, mime_type=None, name=None, forced=None)
        converter = _find_media_converter(v)
        if converter is not None:
            # 注册表命中（D10）：先转载体四类之一，再走载体通道
            carrier = converter.convert(v)
            return await _media_to_block(
                data=carrier.data, path=carrier.path, mime_type=carrier.mime_type,
                name=carrier.name, forced=_CARRIER_BLOCK[type(carrier)])
        raise ValueError(
            f"工具结果类型不可转换: {type(v).__name__}")   # D11：作者 bug

    if isinstance(value, (list, tuple)):
        # 顶层序列：浅层判别（不递归）——全基础原样（tuple 归一为 list）；
        # 有非基础成员则基础保留、非基础逐成员转块（D9 混排不报错）
        items = list(value)
        if all(_is_basic(v) for v in items):
            return items
        return [await _convert_one(v) for v in items]
    return await _convert_one(value)


def output_to_blocks(output: Any, *, error: str | None = None) -> list[ContentBlock]:
    """五形态 ``output`` → 消息 ``content`` 块列表（同步塑形统一出口，D22）。

    .. rubric:: 功能介绍

    塑形只有这一处实现。消费方三处，永远走同一条代码路径：
    `ToolResult.as_message`（内部调本函数）、异步任务完成回调
    （``add_done_callback`` 固定 watcher，D13）、cron
    ``default_tool_executor``（D14）——后两处自行加标注块（D15）后产
    EVENT 消息。

    .. rubric:: 行为规约

    - 纯同步、无 I/O——原料 → 块的转换已在 `normalize_output` 完成。
    - ``error`` 非 ``None`` 时末尾追加 ``TextBlock(error)``。
    - 前置：``output`` 已是五形态之一（出 ``Agent.tool_call`` 恒成立，
      D19）；深层埋藏的非 JSON 对象在本函数内 ``StructBlock`` 构造校验
      失败 → ``ValueError``（框架错误通道）。

    .. seealso::

        - :func:`flowing.tool.normalize_output` —— 上游归一化（D5）。
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
        # 深层埋藏非 JSON 对象在此诚实失败，D5）
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
    """LLM 发出的单次工具调用；``before_tool_call`` 钩子的 value 类型。

    .. rubric:: 功能介绍

    `ToolCall` 是「LLM 想调什么」的纯数据载体：从 PROVIDER 消息的工具调用
    块解析而来，经 ``before_tool_call`` 钩子链传递，最终在 ``_normalize()``
    中被解包为零散参数喂给 `Tool.execute()`。**注意**：`ToolCall` 只是调用
    意图，不是可执行对象；`execute()` 不接收 `ToolCall` 整体入参。

    .. rubric:: 设计动机

    钩子 value 统一携带 ``shortcut`` 短路字段（钩子系统定稿约定），
    `ToolCall` 作为 ``before_tool_call`` 的 value 遵守同一契约。把「调用
    意图」与「执行对象」分开，使审批/改写类 handler 可以在不接触执行层的
    情况下检查与修改调用。

    .. rubric:: 使用示例

    .. code-block:: python

        # 钩子 handler 统一签名 (agent, value)——写成方法形态时 self 即承载
        # Agent：``self`` 只是第一参数的占位符，方法形态 ``(self, tool_call)``
        # 与自由函数形态 ``(agent, tool_call)`` 本质没有区别
        async def _request_approval(self, tool_call):
            tool = self.runtime.tool_registry.get(
                self._tool_entries[tool_call.name].name_ori
            )
            if not getattr(tool, "requires_approval", False):
                return tool_call                      # 原值通过
            response = await approval_service.request(tool_call)
            if response.action == "deny":
                raise Intercepted("用户拒绝")          # 硬阻断 → blocked
            return response.modified_tool_call        # 改写后继续

    .. rubric:: 行为规约

    - handler 三种合法出口：返回（可能改写的）`ToolCall` / 置 ``shortcut``
      短路 / ``raise Intercepted``；普通异常**直接上抛**，无兜底钩子。
    - 非行为：`ToolCall` 不做参数校验、不认识 specified——参数聚合
      全部发生在其后的 ``_normalize()``。
    - 边缘情况：``args`` 只包含 LLM 原始传入值；hook 改写后出现的同名键
      会在 ``resolve()`` 中被 specified 覆盖（优先级见模块 docstring）。

    .. rubric:: 调用关系（审计）

    - 被调：``flowing.agent.Agent.tool_call()`` （每次工具调用管线入口）；
      ``flowing.hooks.HookList.dispatch()`` （作为 ``before_tool_call``
      钩子链 value，每次 dispatch）
    - 实例化方：:meth:`from_block`（逻辑 Turn 循环遍历响应 content 的
      tool_call block 时，逐字调用点见 ``flowing.agent.Agent._run_turn``
      工具调用循环）；编程路径由 cron / workflow 直接构造（id 约定见
      :attr:`id` 字段注释）

    .. seealso::

        - :class:`flowing.tool.ToolResult` —— 调用的结果载体。
        - :class:`flowing.tool.ToolEntry` —— ``resolve()`` 的参数聚合。
        - :mod:`flowing.hooks` —— dispatch 算法与 ``shortcut`` 契约。
    """

    id: str
    """Provider 侧的调用 ID（如 OpenAI ``tool_call_id``）。是与结果侧
    ``kind=TOOL`` 消息 ``tool_call_id`` 字段严格配对的依据（配对锚从块层
    搬到消息字段，D1/D3；``as_message`` 接线，C-05），也是恢复时扫描孤立
    tool_call 的匹配键（M-25）。
    编程路径（cron / workflow / 手动构造的 ToolCall）由调用方生成合成
    字符串——推荐形如 ``<来源类型名>-<uuid4>``（workflow →
    ``workflow-…``、cron → ``cron-…``，S-41 裁决②）的有语义前缀，
    亦接受无语义的 uuid；仅作追踪，不参与配对。
    """
    name: str
    """LLM 看到的工具名——即 `ToolEntry.name_alias`（别名），**不是**规范名。
    ``Agent.tool_call()`` 仅按此名在 ``_tool_entries`` 中查找，不回退规范名。
    """
    args: dict[str, Any]
    """LLM 原始传入的参数字典（键为 LLM 可见名，可能是 ``param_aliases``
    中的别名）。``before_tool_call`` handler 可直接改写本字典；别名→规范名
    的映射、specified 聚合在 ``_normalize()`` 中发生，不在本对象上。
    """
    shortcut: "ToolResult | None" = None
    """钩子短路字段（钩子系统统一契约，spec-draft 06 §16.4 / comment §18）：
    初值 ``None``，dispatch 以「**非 None** 即短路」门控。handler 把一个
    `ToolResult` 放进本字段后返回——链停止、默认执行被替代（该
    `ToolResult` 直接作为本次调用的结果，典型场景：缓存命中跳过工具
    执行），``after_tool_call`` 照常触发。与 ``raise Intercepted`` 的
    区别：shortcut 是「正常收场、结果由 handler 提供」，Intercepted 是
    「硬阻断、结果为 blocked、after 不触发」。shortcut 产物不进
    `Tool.__call__`，其 ``output`` 可为原料形态——经 ``Agent.tool_call``
    收尾的 `normalize_output` 幂等归一（D19）。
    """

    @classmethod
    def from_block(cls, block: ToolCallBlock) -> "ToolCall":
        """从消息层 :class:`flowing.message.ToolCallBlock` 解析为标准化 `ToolCall`.

        .. rubric:: 功能介绍

        剥离 ContentBlock 的通用字段（``type``），产出 ``ToolCall(id, name, args,
        shortcut=None)``。逻辑 turn 循环遍历响应 content 的 tool_call block 时，
        经本方法提取后传给 ``Agent.tool_call()``。

        .. rubric:: 设计动机

        消息层（block，可序列化、交错混排）与执行层（`ToolCall`，带 ``shortcut``
        短路字段供钩子链使用）是两张正交的面孔；本方法是唯一的官方转换点。
        **转换点定在 ToolCall 侧而非 block 侧**（S-43 裁决④，由原
        ``ToolCallBlock.as_tool_call()`` 迁移而来）：「tool 认识 message、
        message 不认识 tool」，message.py 不再 import tool.py，消除全库
        唯一的模块级循环依赖（message ↔ tool）。

        .. rubric:: 行为规约

        - 纯函数：不修改入参 block，不产生副作用。
        - 返回值的 ``shortcut`` 恒为 ``None``（短路是钩子链运行期状态，不来自消息）。

        .. rubric:: 测试案例

        - 前置：``ToolCallBlock(type="tool_call", id="c1", name="read_file",
          args={"path": "a.py"})`` → 操作：``ToolCall.from_block(block)`` →
          期望：``ToolCall(id="c1", name="read_file", args={"path": "a.py"},
          shortcut=None)``。

        .. rubric:: 调用关系（审计）

        - 调用：无（dataclass 构造，`cls(...)` 自身）
        - 被调：``flowing.agent.Agent._run_turn`` 工具调用循环（时机：响应
          content 遇 tool_call block 时，每次一个）

        .. seealso::
           :class:`flowing.message.ToolCallBlock`、
           :meth:`flowing.tool.ToolResult.as_message`（反向转换：结果 → 消息）。
        """
        # shortcut 恒为 None 初值（C-06 裁决后 ToolCall 已含 id/shortcut
        # 字段；短路是钩子链运行期状态，不来自消息）
        return cls(id=block.id, name=block.name, args=block.args)


@dataclass
class ToolResult:
    """工具执行结果；LLM 可见的调用回执（status/output/error + duration，05 定案 D4/D11）。

    .. rubric:: 功能介绍

    `ToolResult` 是工具调用链的最终产物：无论成功、失败、异步还是被阻断，
    对 LLM 与消息树都呈现为同一个结构。结果字段为 ``status`` / ``output``
    / ``error`` 三者（``output`` 是**唯一结果字段**，构造期收原料——
    基础值 / 载体四类 / 裸 ``bytes``·``Path`` / 第三方库对象均可，D6）。

    .. rubric:: 设计动机

    - ``status="error"`` 是**正常产物而非异常**：LLM 应当看到工具失败并自行
      决策（重表述、换工具、向用户报告），因此 error 结果**不触发**任何错误
      钩子——核心错误钩子仅 ``on_provider_error``（机制 vs 策略）。
    - ``status="blocked"`` 与 ``"error"`` 区分：blocked 表示「工具根本没执行
      （被审批/守卫阻断）」，error 表示「执行了但失败」；二者对 LLM 的语义
      与审计含义不同，不可合并。
    - ``pending`` 承载 fire-and-forget 边界：框架只回收据，不阻塞逻辑 Turn
      等待异步任务。
    - ``output`` 单字段五形态（D4）：归一化只做非做不可的转换（原料→块），
      纯 JSON/str 原样放行让钩子消费方拿到自然形态；配对元数据
      （``tool_call_id`` / ``tool_status``）在消息层，不在本对象上（D1/D3）。

    .. rubric:: 使用示例

    .. code-block:: python

        # 工具作者通常不直接构造 ToolResult——框架自动包装：
        async def execute(self, *, order_id: str, caller: Agent) -> dict:
            return {"tx": "abc"}      # → ToolResult(status="completed",
                                      #              output={"tx": "abc"})

        # 媒体返回（载体为可选元数据包装，D6）：
        async def execute(self, *, url: str):
            return [f"已截取 {url}", Image(path=png_path)]

        # 审批阻断路径（框架内部行为）：
        result = ToolResult.blocked("用户拒绝了 delete-file 调用")

    .. rubric:: 行为规约

    - 不变量：``status == "blocked"`` 的实例只能经 `ToolResult.blocked`
      工厂产生，其工具从未进入 `Tool.__call__`。
    - 不变量：``status == "error"`` 时 ``error`` 字段非空、``output`` 可为
      None；``status == "completed"`` 时 ``error is None``、``output``
      可为 None（语义=工具无实质返回；归一化第一形态，塑形为空
      content）。
    - 不变量（D19）：出 ``Agent.tool_call`` 的 ``output`` 恒为**五形态**
      之一——``None`` / 基础值 / 单块 / 纯基础 list / 混合 list（其中块
      只有 ``TextBlock`` / ``StructBlock`` / ``MediaBlock``）；归一点 =
      `Tool.__call__` + ``Agent.tool_call`` 收尾（幂等再归一，封
      shortcut / 钩子改写两缝）。
    - 违禁块（D12）：结果中（任何层级、任何路径）出现 ``ToolCallBlock`` /
      ``ThinkingBlock`` → ``ValueError``（框架错误通道，作者 bug）。
    - 非行为：`ToolResult` 不携带 trace 等观测数据——观测走钩子
      与快照层，不进 LLM 可见结构。**例外**：``duration``（执行时长，
      T3 裁决）由调度层写入，但不进入 ``as_message`` 产物（LLM 不可见）。
    - 边缘情况：深层埋藏的非 JSON 对象归一化期放行，`as_message` 塑形时
      ``StructBlock`` 构造校验失败 → ``ValueError``（D5 诚实失败点）。

    .. rubric:: 测试案例

    - 前置：``execute`` 返回 ``{"tx": "abc"}`` → 操作：框架包装 → 期望：
      ``status == "completed" and output == {"tx": "abc"} and error is None``。
    - 前置：``before_tool_call`` handler ``raise Intercepted("拒绝")`` →
      操作：dispatch → 期望：返回 ``status == "blocked"`` 的 ToolResult，
      工具的 ``execute`` 未被调用。
    - 前置：``execute`` 抛 ``ValueError("bad")`` → 期望：
      ``status == "error"``、``error`` 含 ``"bad"``、不触发 ``on_provider_error``。
    - 前置：``execute`` 返回 ``Image(path="plot.png")`` → 期望：归一化后
      ``output`` 为 ``ImageBlock``（单块形态），`as_message` 塑形产
      ``content == [ImageBlock(...)]``。

    .. rubric:: 调用关系（审计）

    - 被调：``after_tool_call`` 钩子链（每次工具执行后 dispatch，见
      ``flowing.hooks`` 模块规约）；``flowing.tool.ToolResult.as_message()``
      （逻辑 Turn 收尾转消息）
    - 实例化方：``flowing.tool.Tool.__call__`` 调度层（每次工具执行的
      返回值包装）；``flowing.tool.ToolResult.blocked()`` （``blocked``
      状态唯一来源）；shortcut / 钩子改写路径的产物经 ``Agent.tool_call``
      收尾归一（D19）

    .. seealso::

        - :class:`flowing.tool.ToolCall` —— 调用意图载体。
        - :func:`flowing.tool.normalize_output` —— 归一化（D5，幂等）。
        - :func:`flowing.tool.output_to_blocks` —— 塑形统一出口（D22）。
        - :meth:`flowing.tool.Tool.__call__` —— 自动包装的调度层。
        - :mod:`flowing.errors` —— ``Intercepted`` 与普通异常的边界。
    """

    status: ToolStatus
    """执行状态四值之一，见 `ToolStatus`。
    """
    output: Any = None
    """唯一结果字段（D4）。构造期收原料；经 `normalize_output` 归一后、
    出 ``Agent.tool_call`` 恒为五形态之一（``None`` / 基础值 / 单块 /
    纯基础 list / 混合 list，D19）。``completed`` 时承载返回值；
    ``pending`` 时为 ``None``（收据）；``error`` 时可为 None；
    ``blocked`` 时为 ``None`` 或阻断原因 str。
    """
    error: str | None = None
    """错误描述，仅 ``status == "error"`` 时有值。对 LLM 可见（LLM 应能据此
    自我纠正）；**不触发**错误钩子。
    """
    duration: float | None = None
    """执行时长（秒），由 ``Tool.__call__`` 调度层在起止点写入（T3 裁决：
    携带但不进入 ``as_message`` 产物——LLM 不可见）。消费方：
    ``after_tool_call`` handler、审计日志、测试断言。
    """

    @classmethod
    def blocked(cls, reason: str | None = None) -> "ToolResult":
        """构造「被钩子硬阻断」的结果（``status="blocked"`` 的唯一来源）。

        .. rubric:: 功能介绍

        当 ``before_tool_call`` 链中任一 handler ``raise Intercepted`` 时，
        框架捕获该哨兵异常并调用本工厂生成阻断结果；工具本体不执行。

        .. rubric:: 设计动机

        审批/守卫是策略，但「阻断后给 LLM 什么回执」是机制——统一由本工厂
        生成，保证所有阻断路径的产物结构一致（LLM 可据此向用户说明
        「该操作被拦截」而不是「执行失败」）。

        :param reason: 阻断原因（通常取 ``Intercepted`` 的消息），LLM 可见。
        :return: ``status="blocked"``、``error`` 为 ``None`` 的 `ToolResult`。

        .. rubric:: 行为规约

        - 后置条件：返回实例满足 ``status == "blocked" and error is None``；
          ``reason`` 非空时作为 ``output``（str 基础值），`as_message`
          塑形为 ``[TextBlock(reason)]``——LLM 可见形态即文本块，不再是
          ``{"status": "blocked", "reason": ...}`` JSON（05 定案 §5.2）。
        - 非行为：本工厂不记录审计日志——审计由 handler 或快照层负责。

        .. rubric:: 调用关系（审计）

        - 调用：``无``
        - 被调：``flowing.agent.Agent.tool_call()`` （捕获
          ``flowing.errors.Intercepted`` 后生成阻断结果，每次钩子硬阻断）

        .. seealso::

            - :class:`flowing.errors.Intercepted` —— 触发本工厂的哨兵异常。
        """
        return cls(status="blocked", output=reason if reason else None, error=None)

    def as_message(self, tool_call_id: str, *, source: str | None = None) -> Message:
        """把本结果塑形为 ``kind=TOOL`` 的消息，供落消息级树（内部调
        `output_to_blocks`，D22）。

        .. rubric:: 功能介绍

        逻辑 Turn 收尾时，框架将每次工具调用的 `ToolResult` 经本方法转为一条
        ``Message(kind=TOOL, ...)``，以 ``parent_id`` 链入消息级树并在消息
        完整后 append 落盘。配对元数据上移到消息字段（``tool_call_id`` /
        ``tool_status``，D1/D3），``content`` 只含纯内容块——塑形（五形态
        → content 块列表）委托模块级统一出口 `output_to_blocks`，与异步
        完成回调、cron 消费同一实现。

        ``tool_call_id`` 在本方法接线（C-05 裁决）：`ToolResult` 本体不携带
        调用 id——工具执行层（``Tool.__call__``）从未见过 ``ToolCall`` 对象，
        全库唯一同时持有两者的点是 ``Agent.tool_call`` 管线，由它在收尾
        转换时传入 ``tool_call.id``，完成与 ``ToolCallBlock.id`` 的严格配对。

        .. rubric:: 设计动机

        文档 14 后树节点一律是消息，工具结果不例外；把「结果 → 消息」的转换
        收在 `ToolResult` 上，使 turn 循环无需知晓塑形细节。id 走参数而
        非 ``ToolResult`` 字段，避免执行层引入「可空中态」。

        :param tool_call_id: 配对的目标调用 id（``ToolCall.id`` /
          ``ToolCallBlock.id``）；编程路径（cron/workflow 构造的 ToolCall）
          同样经此接线，配对不断链。
        :param source: 消息 ``source`` 字段；缺省填 ``"tool_result"``
          （S-15 裁决：与异步工具结果 EVENT 消息的既有约定同值；工具身份
          经 ``tool_call_id`` 配对反查，不由 source 携带）。
        :return: ``kind=TOOL``、``tool_call_id``/``tool_status`` 接线、
          ``content`` 为 `output_to_blocks` 产物（纯内容块，无协议块）的
          新 `Message`；``synthetic=False``、``turn_end=False``。

        .. rubric:: 行为规约

        - 前置：``self.output`` 已是五形态之一（出 ``Agent.tool_call`` 恒
          成立，D19）；深层埋藏的非 JSON 对象在塑形时 ``StructBlock``
          构造校验失败 → ``ValueError``（框架错误通道）。
        - ``ToolResult.error`` 仅 ``status="error"`` 时非 None，本方法
          无需自行判断，直接透传给 `output_to_blocks`（末尾追加
          ``TextBlock(error)``）。
        - 非行为：本方法**不**负责 append 落盘与 ``parent_id`` 接线——那是
          ``Agent._append_message`` 的职责；本方法只产出未接线的 `Message`。
        - 边缘情况：``status="pending"`` 也产生消息（收据消息，
          ``output=None`` → ``content=[]``；空 tool_result 的 API 层兜底
          属 adapter 职责）；异步任务真正完成时的结果由框架另行产生多块
          EVENT 消息（D13/D14），与本收据互不覆盖。

        .. rubric:: 调用关系（审计）

        - 调用：:func:`flowing.tool.output_to_blocks`（每次调用，塑形
          统一出口）；``flowing.message.Message`` 构造（接线与落盘
          由 ``flowing.agent.Agent._append_message`` 负责）
        - 被调：``flowing.agent.Agent._run_turn`` 工具调用循环（时机：
          每次 ``tool_call()`` 返回后，``result.as_message(tool_call.id)``
          接线配对再挂树，C-05 裁决）

        .. seealso::

            - :func:`flowing.tool.output_to_blocks` —— 塑形统一出口（D22）。
            - :class:`flowing.message.Message` —— 消息级树的节点结构。
            - :meth:`flowing.agent.Agent._append_message` —— 落树时序。
        """
        # D22：塑形统一出口 output_to_blocks；配对元数据（tool_call_id /
        # tool_status）在消息字段，content 只含纯内容块（D1/D3）
        return Message(
            kind=MessageKind.TOOL,
            tool_call_id=tool_call_id,   # 由 Agent.tool_call 管线接线（ToolCall.id），见 docstring
            tool_status=self.status,
            content=output_to_blocks(self.output, error=self.error),  # error 仅 error 态非 None，直接透传
            # S-15 裁决：source 缺省 "tool_result"（与异步 EVENT 结果同值）
            source=source if source is not None else "tool_result",
            synthetic=False,
            turn_end=False,
        )


@dataclass
class ToolDefinition:
    """LLM 可见的工具声明——可序列化的纯数据，不含任何执行逻辑。

    .. rubric:: 功能介绍

    三正交中的「LLM 可见声明」层：``name`` / ``description`` / ``params`` /
    ``output_schema`` / ``strict``。它出现在 `Context.tools` 中；
    ``llm_definition()`` 产物**携带** ``output_schema`` 等非直发字段，
    adapter 组装请求为**白名单语义**——只取 ``name`` / ``description`` /
    ``params`` 等已知字段构造各家 function-calling schema，多带的字段
    天然不会被映射（D21）。

    .. rubric:: 设计动机

    工具作者**不直接构造** `ToolDefinition`（`ScriptTool` 类属性声明 +
    框架自动生成是主路径）；本类独立存在的理由是：同一 Tool 在不同 Agent 上
    需要不同 LLM 视图（见 `ToolEntry`），视图必须是可自由复制、覆写的纯
    数据，不能与执行对象纠缠。

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

    .. rubric:: 行为规约

    - 不变量：可 JSON 序列化（``params_schema`` 即 JSON Schema
      properties dict）；不得持有 callable、连接等运行时对象。
    - 非行为：不做参数校验——LLM 视角校验在 ``_normalize()``、内部校验
      在 `Tool.__call__`（S-33 裁决，模型于创建时编译为 ``_args_model``）。
    - ``strict`` 语义：``True``（默认）时 Provider 层按 ``params`` 严格约束
      LLM 入参；``False`` 时工具层不限制参数（用于「参数由下游自行校验」
      的工具）。

    .. rubric:: 调用关系（审计）

    - 被调：``flowing.tool.ToolDefinition.clone_with_overrides()``
      （每次覆写生成新声明）；``flowing.context.Context.tools`` 消费
      （Provider adapter 翻译时机：未见规约）
    - 实例化方：``flowing.tool.ScriptTool.__init__()`` （script 工具
      实例化时自动生成）；``flowing.tool._auto_generate_tool()`` （裸
      函数包装路径）

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
    """参数声明表，键为规范参数名、值为 JSON Schema property dict
    （B1 裁决：声明层即 schema；required 由 ``default`` 有无派生）。
    来源两条：Python 层 BaseModel 子类经 ``model_json_schema()`` 派生，
    fya 层展开式声明经 :func:`flowing.params.expand_args_schema` 归一化。
    执行层校验模型经 :func:`flowing.params.schema_to_model` 桥接。
    ``default`` 只接受 JSON 兼容类型——复杂对象（DB 连接、HTTP 客户端）
    走标识符引用 + ``caller`` 获取。
    """
    output_schema: dict[str, Any] | None = None
    """返回值结构声明（JSON Schema 片段）。``.fya`` 的 ``output:`` 字段与
    Agent 侧 ``output:`` 通用覆写最终都落在这里；``None`` 表示不约束。

    校验语义（D16）：仅约束 JSON 形态结果——**归一化前**对原料校验，
    原料含非基础成员（媒体等）则跳过；MCP ``outputSchema`` 自动填入
    本字段、仅作运行时校验（不喂模型）；媒体能力归模型 capability 层，
    不进工具声明。随 ``llm_definition()`` 产物携带（D21），adapter
    白名单取用、天然不被映射。
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
        """生成覆写后的**新** `ToolDefinition`，原始对象不变。

        .. rubric:: 功能介绍

        `ToolEntry.llm_definition()` 的唯一覆写机制：别名、参数局部覆写、
        描述覆写、隐藏参数移除，全部经本方法一次性完成。

        .. rubric:: 设计动机

        「返回新对象，原始不变」是绑定层不污染全局注册表的结构性保证——
        `ToolRegistry` 中的定义永不被 Agent 级覆写修改。

        :param name: 新名字（通常传 `ToolEntry.name_alias`）；``None`` 保持
          原名。
        :param override_params: ``{规范参数名: {JSON Schema 关键字: 新值}}``
          稀疏补丁（B1 裁决：覆写端零糖，类型必须写 ``type:``）——只覆写
          出现的关键字，未出现的参数与关键字保持原值（深合并回填）。
          补丁应用委托 :func:`flowing.params.apply_param_overrides`——
          关键字超出桥接子集时抛 :class:`flowing.errors.FormatError`
          （笔误 fail-fast；「理解推迟」模型的最后一站校验）。requiredness
          不主动推断：没写 ``default`` 沿用基底；显式给 ``default`` 变可选。
        :param override_description: 描述覆写；``None`` 保持原描述。
        :param specified_params: 要从 LLM 视图中**移除**的参数名集合
          （调用方传入 ``set(specified.keys())``——注入表达式也是 specified
          的一种值形态，R-4 裁决后不再有独立 inject 通道）。
        :return: 新的 `ToolDefinition`；``self`` 不被修改。

        .. rubric:: 行为规约

        - 后置条件：返回值与 ``self`` 是不同对象；``self.params_schema`` 内容不变。
        - 边缘情况：``override_params`` 中出现 ``params`` 不存在的键 →
          视为**新增参数**（`FinishTool` 动态 schema 即依赖此语义）；
          ``specified_params`` 中出现不存在的键 → 静默忽略。
        - 非行为：不做参数别名应用（``param_aliases`` 的改名由
          `ToolEntry.llm_definition()` 第四步在返回值上完成）。

        .. rubric:: 测试案例

        - 前置：``params_schema = {"amount": {type: number, default: 0,
          description: 金额}}`` → 操作：``clone_with_overrides(
          override_params={"amount": {"description": "支付金额"}})`` →
          期望：新定义中 ``type``/``default`` 原样、description 被替换，
          原定义不变。
        - 前置：同上 → 操作：``specified_params={"amount"}`` → 期望：新定义
          ``params_schema`` 中无 ``"amount"`` 键（required 同步重算）。

        .. rubric:: 调用关系（审计）

        - 调用：``无`` （纯数据变换，返回新对象）
        - 被调：``flowing.tool.ToolEntry.llm_definition()`` 第 3 步（每次
          ``Agent._assemble_context()`` 现场求值）

        .. seealso::

            - :meth:`flowing.tool.ToolEntry.llm_definition` —— 本方法的
              唯一框架调用点。
        """
        params: dict[str, dict[str, Any]] = {k: dict(v) for k, v in self.params_schema.items()}
        if specified_params:
            for key in specified_params:
                params.pop(key, None)  # 不存在的键静默忽略（行为规约）；
            # specified 参数由 specified 值兜底，requiredness 无需维护
        if override_params:
            params = apply_param_overrides(params, override_params)
            # 非法关键字 fail-fast / 未知键新增参数 / 稀疏回填,语义见该函数
        return ToolDefinition(
            name=name or self.name,
            description=override_description or self.description,
            params_schema=params,
            output_schema=self.output_schema,  # 透传：随 llm_definition 产物携带（D21），覆写不触及
            strict=self.strict,
        )


def _first_paragraph(doc: str | None) -> str | None:
    """docstring 首段提取（T6 三级回退链的「首段」口径）：cleandoc 后按
    空行切首段，段内换行折叠为空格；无内容 → ``None``。内部 API。"""
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

    .. rubric:: 行为规约

    - ``param_aliases`` 方向为 ``LLM 别名 → 规范名``；本函数对
      ``params_schema`` 键做反向改名——**仅改名**，property 内容原样，
      其余字段（name/description/output_schema/strict）透传。
    - 撞名（改名结果与既有键撞车，含两个规范名经别名映射到同一名称）→
      :class:`flowing.errors.FormatError`（绑定声明笔误，fail-fast）；
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
                f"参数别名应用撞名：{new_key!r}（param_aliases 与既有参数冲突）")
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

    `ToolEntry` 回答「这个 Agent 如何使用这个 Tool」：LLM 看到的别名、参数
    覆写、注入参数、指定值、参数别名。每个 Agent 实例的
    ``agent._tool_entries`` 持有自己的 entry 集合，互不共享。

    .. rubric:: 设计动机

    核心动机是 `FinishTool` 案例：同一工具在不同 Agent 上 LLM 应看到不同
    schema——覆写必须发生在 Agent 级绑定层，而不是全局注册表。

    .. rubric:: 使用示例

    .. code-block:: python

        entry = ToolEntry(
            name_alias="pay",
            name_ori="make-payment",
            override_params={                       # JSON Schema 稀疏补丁（零糖）
                "amount": {"description": "支付金额（元），上限 50000"},
                "currency": {"default": "USD"},
            },
            specified={
                # 固定值与注入表达式都是 specified（R-4：inject 键已删除）
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
                amount: {description: "支付金额（元），上限 50000"}   # 稀疏补丁（覆写端零糖）
                currency: USD                  # 裸值 → specified（固定值）
                user_id: "{{ self.inject('user_id') }}"   # 注入表达式 → specified
                working_dir as cwd: _          # as 改名 + 空补丁(_ 语义见下)
        ---
        $tools.pay.args.cwd.description:       # 深层块:向空补丁逐字段写入
        本订单的工作目录

    .. rubric:: 行为规约

    - ``agent.fya`` 覆写 ``args:`` 的判别（框架按键与值自动区分；``as``
      切分唯一实现为 :func:`flowing.parser.split_as`；**判别执行点 =
      :meth:`flowing.agent.Agent.add_tool`**——``.fya`` 装配层透传
      ``EntryRef``，程序化调用收同构 ``body`` dict，两者经同一判别代码）：

      - 值是 Dict → ``override_params`` 稀疏补丁（JSON Schema 关键字，
        零糖——类型必须写 ``type:``；合成时经
        :func:`flowing.params.apply_param_overrides` 应用，未出现的关键字
        从基底回填）；
      - 键含 ``<name> as <alias>`` → ``param_aliases``；**允许带值**——
        值部分按本表其它行照常判别（改名与补丁/延迟可叠加）；
      - 其它值（Parsable 源，含 ``{{ self.inject('key') }}`` 注入表达式）→
        ``specified``（LLM 不可见，调用时以求值上下文现场求值——注入
        表达式在此沿 provide 链上溯）；
      - 值是 ``_``（``PENDING``）→ **空补丁**：装配时解析为空；深层块
        （如示例 ``$tools.pay.args.cwd.description:``）可逐字段填充
        （导航规则见 :class:`flowing.subagents.SubagentEntry` 行为规约——
        只认别名：有别名的条目/参数键，规范名段不再可寻址）；未被填充
        则合成时从基底定义全量回填，
        **不报错**（与字段位 ``_`` 的「必须兑现」语义不同）。
    - ``enabled=False`` 时条目在 ``_assemble_context()`` 中被跳过（不进
      `Context.tools`，LLM 不可见），但编程式路径仍可经注册表访问——
      「可见性」与「可执行性」分离。
    - 非行为：entry 不持有 Tool 实例引用——执行时按 ``name_ori`` 现场查
      `ToolRegistry`。
    - 不变量：``specified`` 中的参数（固定值与注入表达式）对 LLM 不可见；
      其值优先级最高（防 LLM 篡改通道）。

    .. rubric:: 测试案例

    - 前置：同一 `FinishTool` 注册在 `ToolRegistry`；两个 Agent 的 entry
      分别带不同 ``override_params`` → 操作：各自 ``llm_definition()`` →
      期望：产出两个不同 schema 的 `ToolDefinition`，注册表原定义不变。

    .. rubric:: 调用关系（审计）

    - 被调：``flowing.agent.Agent.tool_call()`` （仅按别名在
      ``_tool_entries`` 查找，每次工具调用）；
      ``flowing.agent.Agent._normalize()`` （每次工具调用参数聚合）；
      ``flowing.agent.Agent._visible_tools()`` （每次上下文组装）
    - 实例化方：:meth:`flowing.agent.Agent.add_tool`（``.fya`` ``tools:``
      条目装配与程序化调用的**统一入口**，逐条目各一次）

    .. seealso::

        - :class:`flowing.tool.ToolRegistry` —— 规范名 → Tool 的全局表。
        - :class:`flowing.tool.ToolDefinition` —— 覆写产物类型。
    """

    name_alias: str
    """LLM 看到的工具名（别名）。工具调用**仅按别名查找**，不回退规范名——
    不同 Agent 对同一工具注册了不同别名/覆写，回退会绕开 Agent 级绑定。
    """
    name_ori: str
    """规范名——`ToolRegistry` 中的 key，查找可执行对象的唯一依据。
    """
    override_description: Parsable | None = None
    """覆写 LLM 看到的描述；``None`` 使用注册表原描述。**是 Parsable**
    （用户裁决，取代 M-99 的「普通 str」）：``llm_definition()`` 时以
    调用方 Agent 为上下文**自动求值**（求值面内，与 SubagentEntry 的
    description 覆写同律）——声明期可写模板/表达式，组装时拿到渲染后
    字符串。
    """
    override_params: dict[str, dict[str, Any]] | None = None
    """参数局部覆写：``{规范参数名: {子属性: 新值}}``，只写与默认不同的
    字段；含 ``output`` 通用覆写（``.fya`` 的 ``output:`` 与 ``args:``
    覆写合并进同一 ``override_params``，对 ``llm_definition()`` 与
    ``resolve()`` 完全透明）。
    """
    specified: dict[str, Parsable] = field(default_factory=dict)
    """指定值参数（LLM 不可见）。默认空 dict。值为 `Parsable`，在
    ``resolve()`` 时以调用方 Agent 局部变量为上下文惰性求值。两种值形态
    （R-4 裁决，``inject`` 字段已删除）：固定值（``Parsable("USD")``）
    与**注入表达式**（``Parsable("{{ self.inject('user_id') }}")``——
    求值时沿 provide 链上溯，链断裂抛 ``MissingProvideError``）。
    """
    param_aliases: dict[str, str] = field(default_factory=dict)
    """LLM 参数名 → 规范参数名。默认空 dict。LLM 看到别名，``resolve()``
    第一步映射回规范名。
    """
    enabled: bool = True
    """是否对 LLM 可见；``False`` 时不进 `Context.tools`，但仍可编程式调用。
    """

    def llm_definition(self, runtime: Runtime, agent: Agent) -> ToolDefinition:
        """生成本 Agent 视角下 LLM 可见的 `ToolDefinition`（四步）。

        .. rubric:: 功能介绍

        上下文组装（``Agent._assemble_context()``）时对每个 ``enabled``
        entry 调用本方法，产物进入 `Context.tools`。每次现场求值，无缓存。

        .. rubric:: 行为规约（四步，顺序不变量）

        1. 从 ``runtime.tool_registry`` 按 ``name_ori`` 取规范 Tool 的默认
           ``definition``；
        2. 计算 ``hidden = set(self.specified.keys())``——specified 参数
           （固定值与注入表达式）对 LLM 不可见；
        3. ``override_description`` 非 ``None`` 时以 ``agent`` 为上下文
           **现场 resolve**（Parsable 自动求值，求值面内——用户裁决，
           取代 M-99 的「普通 str」），随后
           ``definition.clone_with_overrides(self.name_alias,
           self.override_params, <渲染后描述>,
           specified_params=hidden)`` 生成新定义（原始定义不变）；
        4. 应用参数别名：把 LLM 可见 schema 中的参数名从规范名改为
           ``param_aliases`` 中的别名。

        :param runtime: 当前 Runtime（取其 ``tool_registry``）。
        :param agent: 调用方 Agent（``override_description`` 的 Parsable
          渲染上下文）。
        :return: 覆写后的新 `ToolDefinition`；注册表中的原始定义不变。
        :raises ToolNotFoundError: ``name_ori`` 不在注册表中（创建管线应已
          保证不触发；运行时出现即注册表被外部改动的信号）。

        .. rubric:: 测试案例

        - 前置：注册表含 ``make-payment``（params_schema:
          amount/currency/user_id），entry 为 ``specified={"user_id":
          Parsable("{{ self.inject('user_id') }}")},
          param_aliases={"sum": "amount"}`` → 操作：
          ``llm_definition(runtime, agent)`` → 期望：产物无 ``user_id``、
          有 ``sum`` 无 ``amount``、名字为别名。

        .. rubric:: 调用关系（审计）

        - 调用：``flowing.tool.ToolRegistry.get()`` （第 1 步，经
          ``runtime.tool_registry`` 按 ``name_ori`` 取规范定义）；
          ``flowing.tool.ToolDefinition.clone_with_overrides()``
          （第 3 步）
        - 被调：``flowing.agent.Agent._visible_tools()`` （每次
          ``Agent._assemble_context()``，无缓存现场求值）

        .. seealso::

            - :meth:`flowing.tool.ToolDefinition.clone_with_overrides` ——
              第 3 步的覆写语义。
        """
        tool = runtime.tool_registry.get(self.name_ori)
        hidden = set(self.specified.keys())   # specified（固定值/注入表达式）对 LLM 不可见
        # override_description 是 Parsable：以调用方 agent 为上下文现场 resolve
        # （求值面内，用户裁决）；None 时保持注册表原描述
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
        """把 LLM args + specified（含注入表达式）聚合为 `execute()` 的完整参数（两步）。

        .. rubric:: 功能介绍

        本方法在 ``Agent._normalize()`` 内部被调用（钩子 ``before_tool_call``
        **之后**），产出按规范名组织的最终参数字典。

        .. rubric:: 设计动机

        只收参数声明表 ``params_schema``（``tool.definition.params_schema``），
        不持有 `Tool` 引用——`ToolEntry` 保持在「绑定/声明」层，不依赖
        「执行」层（S-33 裁决）。调用方（``Agent._normalize``）已持有 Tool
        实例，顺手传入声明表即可。

        .. rubric:: 行为规约（两步，后写覆盖先写 = 优先级递增）

        1. **LLM args**：逐键经 ``param_aliases`` 映射回规范名；
        2. **specified**：`Parsable` 以 ``agent`` 局部变量为上下文惰性求值
           （固定值直给；注入表达式 ``{{ self.inject('key') }}`` 在此沿
           provide 链上溯——R-4：不再有独立 inject 步骤），并按
           ``params_schema`` 中对应 property 做 `_coerce` 兼容转换后覆盖
           同名字段；声明表无此键时跳过转换、保留原值（最终由
           `Tool.__call__` 的内部校验兜底报错）。

        schema 默认值不在本方法填充——由 ``_normalize()`` 第 3 步填充。

        :param agent: 调用方 Agent（provide 链上溯与 Parsable 渲染上下文）。
        :param args: LLM 原始参数（键可能是别名）。
        :param params_schema: 规范参数名 → JSON Schema property 的声明表，
            取 ``tool.definition.params_schema``（覆写后的 LLM 视图不
            适用——本方法一律按注册表规范定义）。
        :return: 规范名 → 值的完整参数字典，供 `Tool.__call__` 按 ``execute()``
          签名匹配分发。
        :raises flowing.errors.MissingProvideError: 注入表达式中的 key
          沿 provide 链上溯不到任何提供者（调用时求值抛出）。

        .. rubric:: 测试案例

        - 前置：``args={"sum": 100}``、``specified={"currency":
          Parsable("CNY"), "user_id": Parsable("{{ self.inject('user_id')
          }}")}``、``param_aliases={"sum": "amount"}``、
          ``params_schema={"amount": ..., "currency": ..., "user_id":
          ...}``、provide 链可提供 ``user_id="alice"`` → 操作：
          ``resolve(agent, args, params_schema)`` → 期望：``{"amount":
          100, "currency": "CNY", "user_id": "alice"}``。

        .. rubric:: 调用关系（审计）

        - 调用：``flowing.parsable.Parsable.resolve()`` （第 2 步
          specified 惰性求值——注入表达式在此触发
          ``flowing.agent.Agent.inject()`` 的 provide 链上溯）；
          ``flowing.params._coerce()`` （第 2 步兼容转换）
        - 被调：``flowing.agent.Agent._normalize()`` 第 2 步（每次工具
          调用，``before_tool_call`` 钩子之后）

        .. seealso::

            - :meth:`flowing.agent.Agent.inject` —— 注入表达式的
              provide 链上溯执行点。
            - :meth:`flowing.parsable.Parsable.resolve` —— 惰性求值。
            - :func:`flowing.params._coerce` —— 兼容类型转换（C-08 裁决：
              唯一声明在 params 模块）。
        """
        resolved: dict[str, Any] = {}
        for key, value in args.items():  # 第 1 步：LLM args 逐键别名 → 规范名
            resolved[self.param_aliases.get(key, key)] = value
        for key, parsable in self.specified.items():  # 第 2 步：specified 惰性求值后覆盖（固定值/注入表达式同路）
            value = parsable.resolve(agent)  # -> Any（注入表达式 {{ self.inject(...) }} 在求值中沿 provide 链上溯）
            prop = params_schema.get(key)  # -> property dict | None（声明表由调用方传入，S-33）
            if prop is not None:
                value = _coerce(value, prop)  # 声明表无此键时跳过转换，内部校验兜底
            resolved[key] = value
        return resolved


class Tool:
    """可执行对象基类——三正交中的「执行」层。

    .. rubric:: 功能介绍

    `Tool` 只对执行负责：持有一份默认 `ToolDefinition`，暴露 `execute()`；
    框架调度层经 `__call__` 统一调用。四种工具类型（script/mcp/cli/request）
    均以本类（或其子类）为最终产物。

    .. rubric:: 设计动机

    调度职责（awaitable 检测、Task 包装、caller 注入、返回值包装）集中在
    `__call__`，让 `execute()` 保持「零散参数进、普通值出」的最简单签名——
    工具作者不需要知道 `ToolResult` 的存在。

    .. rubric:: 使用示例

    直接子类化通常只用于内置工具；应用代码应使用 `ScriptTool`：

    .. code-block:: python

        class FinishTool(Tool):
            definition = ToolDefinition(name="finish", ...)

            async def execute(self, *, summary: str = "", caller: Agent) -> dict:
                ...

    .. rubric:: 行为规约

    - 非行为：`Tool` 不认识钩子、不查注册表、不做参数聚合——这些都在
      ``Agent.tool_call()`` / ``_normalize()`` 一侧。
    - 实例属性开放：``.fya`` 中的非保留字段（如 ``requires_approval: true``）
      直接成为 tool 对象的普通属性，框架不解析、不据此做任何自动行为。
    - 不变量：``self._execution`` 仅在 `__call__` 调度期间非 None；
      工具内部可检查 ``self._execution.cancel.is_set()`` 以响应 cancel
      （协作式取消，见 :mod:`flowing.agent` 的 `Execution` 契约）。

    .. rubric:: 调用关系（审计）

    - 调用：``无`` （基类不发起框架调用）
    - 被调：``flowing.agent.Agent.tool_call()`` （经 ``__call__`` 调度，
      每次工具调用）
    - 实例化方：各子类构造路径——script 单例（``ToolRegistry.get``
      引用触发的惰性解析 / 用户直接构造后经 ``register()``）、
      MCP/CLI/Request（Agent 解析 ``.fya`` 时）、插件工具（如
      ``flowing.plugins.skills.SkillPlugin`` 阶段一注册 ``SkillLoadTool``）

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
    实例为 ``None``。Entry 装配对**文件派生工具**落账本字段为
    ``name_ori``（含目录派生命名空间的限定键，热路径精确命中）；注册表
    命中（``default::``/``builtin::``）的条目仍记裸名。内部 API。
    """
    _has_caller: bool
    """注册/实例化时经 ``inspect`` 检测 `execute()` 签名是否声明 ``caller``
    参数的缓存标记。内部 API，不属稳定契约。
    """
    _execution: Execution | None
    """当前调度的执行追踪对象（cancel 信号载体），仅 `__call__` 期间有效。
    内部 API，不属稳定契约。
    """
    _args_model: type[BaseModel]
    """内部校验用的 Pydantic 模型——**工具创建时**编译一次、终身复用
    （S-33 裁决：不在每次调用时编译）。来源（B1 裁决）：Python 层
    ``args_model`` 声明直接用（声明即模型）；未声明时从 ``execute()``
    签名构建（``_infer_from_execute``）；fya 声明经
    :func:`flowing.params.schema_to_model` 桥接。由各子类 ``__init__``
    在 ``definition`` 落定后赋值；覆写 ``__init__`` 的子类必须调
    ``super().__init__()`` 或自行赋值，否则 `__call__` 的内部校验无模型
    可用。与 LLM 可见 JSON Schema 同源（双视图契约，见
    :mod:`flowing.params`），永不漂移。**不做**与 ``execute()`` 签名
    的一致性检查——签名差异可能是合法 trick（``**kwargs`` 透传、装饰器
    包装），真写岔由调用时内部校验或直接测试暴露。
    内部 API，不属稳定契约。
    """

    async def execute(self, **kwargs: Any) -> Any:
        """工具业务逻辑入口——**零散参数 + 可选 caller**，不接收 `ToolCall`。

        .. rubric:: 功能介绍

        子类覆写本方法。参数来自 `ToolEntry.resolve()` 聚合后的完整字典，
        由 `__call__` 按签名匹配分发；声明 ``caller: Agent`` 参数时框架自动
        传入调用方 Agent（用于注入表达式求值 / ``get_resource`` / 访问
        调用方状态）。

        .. rubric:: 设计动机

        废弃「接收完整 `ToolCall` 对象」的旧写法：工具作者面对的是业务参数，
        不是框架内部结构。同步函数同样合法（`__call__` 做 awaitable 检测）。

        .. rubric:: 使用示例

        .. code-block:: python

            async def execute(self, *, order_id: str, amount: float,
                              caller: Agent) -> dict:
                return await payment_service.charge(
                    order_id=order_id, amount=amount)

        .. rubric:: 行为规约

        - 返回值由 `__call__` 自动包装：普通值 → ``completed``；抛异常 →
          ``error``；返回 ``asyncio.Task`` → ``pending``（fire-and-forget
          收据，框架不等待）。
        - 非行为：不自行构造 `ToolResult`；不处理 specified（固定值/注入
          表达式，已由调度层聚合进参数）；复杂对象（连接池、客户端）不进
          参数——以标识符字符串传入，经 ``caller`` 获取真实对象。
        - 边缘情况：长时间运行的工具应周期性检查
          ``self._execution.cancel.is_set()`` 以支持协作式取消。

        .. rubric:: 调用关系（审计）

        - 调用：``无`` （基类占位，业务调用由子类覆写实现）
        - 被调：``flowing.tool.Tool.__call__`` 调度层（每次工具执行）

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
        """框架调度层统一入口——**用户永不覆写**。

        .. rubric:: 功能介绍

        ``Agent.tool_call()`` 在 ``_normalize()`` 之后经本方法执行工具。
        职责固定五项：

        1. **awaitable 检测**：``inspect.isawaitable``——同步 ``execute``
           直接调用，异步 ``execute`` await；
        2. **Task 包装**：异步执行包装为 ``asyncio.Task`` 并关联
           ``execution``（cancel 注入的落点——置 abort 信号而非强杀协程）；
        3. **caller 自动传入**：依 ``_has_caller``（注册时 inspect 检测）决定
           是否传 ``caller=``；
        4. **内部校验**（S-33 裁决，自 ``_normalize`` 迁入）：caller 注入
           之后、``execute`` 之前，聚合终值按 ``self._args_model``（创建时
           编译的 Pydantic 产物）校验。specified（固定值/注入表达式求值
           结果）/ 默认值属可信来源，本步失败是**框架/宿主配置错误**——异常**在 ``try`` 之外
           上抛**框架错误通道并记日志，**不**被 ``except Exception`` 吞成
           ``ToolResult(error)``、不进入 LLM 可见文本（不泄漏隐藏参数的
           存在）；
        5. **结果归一与包装**（D5/D12/D19）：返回值（含直通 `ToolResult`
           逃生舱的 ``output``）经 `normalize_output` 归一——浅层判别、
           幂等；归一化中的违禁块（``ToolCallBlock`` / ``ThinkingBlock``）
           ``ValueError`` 属作者 bug，与职责 4 同走框架错误通道**在
           ``try`` 之外上抛**；``execute`` 内异常 → ``ToolResult(error)``
           （不触发错误钩子）；返回 ``asyncio.Task`` →
           ``ToolResult(pending)``，并给 Task 挂 ``add_done_callback``
           （**固定行为，非扩展点**，D13）——完成回调取终值 →
           `normalize_output` → `output_to_blocks` → 标注块 + 结果块的
           多块 EVENT 入队；任务异常 → 标注块 + 错误文本块（与同步
           error 同语义，LLM 可见）。

        :param resolved_args: `ToolEntry.resolve()` 产出并经默认值填充的
          规范名参数字典；已经过 LLM 视角校验，但**尚未**过内部校验
          （本方法职责 4）。
        :param caller: 调用方 Agent；仅当 ``execute`` 声明了 ``caller``
          参数时实际传入。
        :param execution: 本次调用的执行追踪对象；挂到 ``self._execution``
          供工具内部检查 abort 信号。
        :return: 包装后的 `ToolResult`。
        :raises pydantic.ValidationError: 内部校验失败（specified/inject/
          默认值的配置错误）——框架错误通道，非 LLM 可见产物。

        .. rubric:: 行为规约

        - 前置条件：``resolved_args`` 已经过 LLM 视角校验（``_normalize``
          第 1 步）与默认值填充（第 3 步）。
        - **enqueue 契约**：普通 ``async def execute`` 被 await 到底，结果
          只作为返回值交给 ``Agent.tool_call``，**不 enqueue**。仅当
          ``execute`` 返回 ``asyncio.Task`` 时，本方法返回 ``pending``
          收据，并由固定 watcher 在 Task 完成时 **enqueue EVENT**
          （``source="tool_result"``）。因此工具结果是否入队，只由
          ``execute`` 的返回形态决定，与调用上下文无关。
        - 非行为：不重试、不超时兜底、不审批——重试由可选的
          ``use_retry()`` 提供，审批在 ``before_tool_call``。
        - 边缘情况：``execution.cancel`` 在 `execute` 运行期间被置位 →
          本方法不强制中断 Task，工具自行协作退出；工具不响应时由
          cancel/stop 族的上层策略处理。

        .. rubric:: 调用关系（审计）

        - 调用：``flowing.tool.Tool.execute()`` （每次调度；awaitable
          检测后调用或 await）；
          :func:`flowing.tool.normalize_output`（职责 5，每次同步完成
          路径的归一化）
        - 被调：``flowing.agent.Agent.tool_call()`` （``_normalize()``
          之后，每次工具调用）

        .. seealso::

            - :meth:`flowing.agent.Agent.tool_call` —— 上游调用点（其收尾
              对 shortcut / 钩子改写产物幂等再归一，D19）。
            - :class:`flowing.agent.Execution` —— cancel/pause 信号契约。
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
        # 职责 4（S-33）：内部校验——caller 注入之后、try 之外。
        # specified/inject/默认值的配置错误 -> 上抛框架错误通道 + 日志，
        # 不被下方 except Exception 吞成 ToolResult(error)、不进 LLM 可见文本
        # （不泄漏隐藏参数的存在）；日志只记工具名，不记参数值（防敏感值泄露）
        try:
            self._args_model.model_validate(resolved_args)
        except ValidationError:
            _logger.exception(
                "工具 %s 内部校验失败（specified/inject/默认值配置错误）",
                getattr(self, "definition", None) and self.definition.name
                or type(self).__name__)
            raise
        try:
            result = self.execute(**resolved_args)  # -> Any（同步）或 awaitable（异步）
            if inspect.isawaitable(result):
                task = asyncio.ensure_future(result)  # Task 包装（abort 为协作式，不强杀）
                value = await task
            else:
                value = result
        except Exception as exc:
            # 异常路径：包装为 error 结果，不触发错误钩子（LLM 可见的正常产物）
            return ToolResult(status="error", error=str(exc))
        finally:
            self._execution = None  # 不变量：_execution 仅 __call__ 期间有效
        if isinstance(value, asyncio.Task):
            # fire-and-forget 收据；Task 挂 add_done_callback 固定 watcher
            # （D13，非扩展点）：完成回调取终值 → normalize_output →
            # output_to_blocks → 标注块 + 结果块的多块 EVENT 入队；
            # 任务异常 → 标注块 + 错误文本块（与同步 error 同语义，LLM 可见）
            if caller is not None:
                value.add_done_callback(
                    lambda t: asyncio.ensure_future(
                        self._deliver_async_result(t, caller)))
            return ToolResult(status="pending", output=None)
        # 职责 5（D5/D12/D19）：归一化在 try 之外——浅层判别、幂等；
        # 违禁块（ToolCallBlock/ThinkingBlock）ValueError 属作者 bug，
        # 上抛框架错误通道，不被吞成 ToolResult(error)
        value = await normalize_output(value)
        return ToolResult(status="completed", output=value)

    async def _deliver_async_result(self, task: "asyncio.Task", caller: "Agent") -> None:
        """异步工具完成回调（D13 固定行为，非扩展点）：取终值 → 归一 → 塑形
        → 标注块 + 结果块的 EVENT 消息（``source="tool_result"``、STEER
        优先级）入调用方队列；任务异常 → 标注块 + 错误文本块（LLM 可见）。

        **内部 API，不属稳定契约。**
        """
        marker = TextBlock(text=f"异步工具 {self.definition.name} 的最终结果：")
        if task.cancelled():
            blocks = [marker, TextBlock(text="异步任务被取消")]
        elif (exc := task.exception()) is not None:
            blocks = [marker, TextBlock(text=str(exc))]   # 与同步 error 同语义
        else:
            value = await normalize_output(task.result())
            blocks = [marker, *output_to_blocks(value)]
        await caller.enqueue_message(Message(
            kind=MessageKind.EVENT, source="tool_result",
            content=blocks, priority=MessagePriority.STEER))


class ScriptTool(Tool):
    """script 工具的作者基类——类属性声明 + 框架自动生成 `ToolDefinition`。

    .. rubric:: 功能介绍

    工具作者**不直接构造** `ToolDefinition`，而是在子类上声明 ``description``
    / ``args_model`` 类属性（B1 裁决：参数声明 = Pydantic BaseModel
    子类，声明即模型——schema 由 ``model_json_schema()`` 派生、执行
    校验即模型本身）；``__init__`` 时框架自动生成
    ``self.definition``。``name`` 可省略——缺省由类名 kebab 化推断
    （``MakePayment`` → ``make-payment``），显式声明仅作一致性断言
    （不符抛 :class:`flowing.errors.NameMismatchError`）。``args_model``
    也可省略——从 `execute()` 签名的类型标注与默认值**构建**模型
    （``_infer_from_execute``）。

    .. rubric:: 设计动机

    声明与执行写在同一处，元信息不必二次维护。名字一律**推断**（文件名 /
    目录名 / 类名），任何显式 ``name`` 声明（``.fya`` 字段、类属性、
    ``@flowing_tool`` 装饰器参数）仅作一致性断言；``description`` /
    ``parameters`` 仍按 ``.fya`` 显式声明 > 类属性 > docstring /
    ``execute()`` 签名推断 的优先级链覆盖一切兜底来源。

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

            # name 省略——由类名 kebab 化推断为 "make-payment"
            # （显式写 name = "make-payment" 合法，仅作一致性断言）
            description = "发起支付"
            args_model = PayArgs          # 参数声明（B1：BaseModel 子类）

            async def execute(self, *, order_id: str, amount: float,
                              caller: Agent) -> dict:
                ...

    打标函数路径（``@flowing_tool`` 装饰，框架自动提升为等价子类）：

    .. code-block:: python

        # tools/make_payment.py
        @flowing_tool    # 可带参数断言：@flowing_tool("make-payment")
        async def make_payment(order_id: str, amount: float,
                               currency: str = "CNY") -> dict:
            \"\"\"对指定订单发起支付。\"\"\"
            ...

    ``.fya`` 路径（``callable:`` 指向**未装饰**的裸函数，产物等价）：

    .. code-block:: yaml

        # make-payment/TOOL.fya
        type: script
        description: 对指定订单发起支付。仅在用户明确确认支付意图后调用。
        callable: ./impl.py::make_payment   # {路径}::{函数名或类名}

    .. rubric:: 行为规约

    - **互斥规则**：类属性声明（``name``/``description``/``args_model``）与
      手写 ``definition`` 类属性**互斥**；同时存在时框架发警告，以显式
      ``definition`` 为准。
    - **声明通道互斥**：``callable:`` 指向的函数**不允许**带
      ``@flowing_tool`` 装饰（打标 = 自动提升通道，``callable:`` = 显式
      指针通道，二选一）；违反抛 :class:`flowing.errors.FormatError`。
    - 类属性在 ``__init__`` 前已就绪，无时序问题。
    - script 工具注册为**全局单例**（Runtime 一份，所有 Agent 共享）——
      同一份用户代码不应实例化多次。
    - 打标函数元信息提取：``name`` ← 文件名去 ``.py`` 并 snake → kebab
      规范化（``TOOL.py`` / ``tool.py`` 通用名时取目录名）——机制本体
      :func:`flowing.paths.infer_name`（规则表 :data:`TOOL_NAMING`）；
      装饰器参数若给出仅作一致性断言（不符抛 ``NameMismatchError``）；
      ``description`` ← 显式声明 > 类 docstring 首段 > ``execute()`` docstring
      首段（三级回退链，T6 裁决）；``args_model`` ← 从签名构建
      （类型标注 → 字段类型、默认值 → default，经
      :func:`flowing.params.schema_to_model` 同一桥接落成模型）。
    - **每文件至多一个打标函数**；与 Tool 子类同文件并存或含多个打标
      函数 → :class:`flowing.errors.AmbiguousToolError`。

    :raises MissingSchemaError: 既无 ``args_model`` 声明、`execute()` 参数又
      缺类型标注时（签名构建不出字段类型）。
    :raises AmbiguousToolError: 同一 ``.py`` 文件同时存在打标函数与
      `ScriptTool` 子类、或含多个打标函数时（目录定向查找阶段）。

    .. rubric:: 测试案例

    - 前置：子类同时声明 ``args_model`` 与 ``definition`` → 操作：实例化 →
      期望：产生告警日志且 ``self.definition`` 为显式声明的对象。

    .. rubric:: 调用关系（审计）

    - 调用：``flowing.tool._infer_from_execute()`` （``__init__`` 内，
      ``args_model`` 未声明时——从签名构建模型）
    - 被调：``无`` （作者基类；框架经子类实例化与 ``Tool.__call__``
      调度链使用）
    - 实例化方：用户子类直接构造 / ``flowing.tool._auto_generate_tool()``
      （打标函数路径，经 :func:`flowing_tool` 登记后由
      ``ToolRegistry.get()`` 提升）；产物经
      ``ToolRegistry.register()`` 注册全局单例

    .. seealso::

        - :func:`flowing.tool._infer_from_execute` —— 签名推断的内部实现。
        - :func:`flowing.tool._auto_generate_tool` —— 打标函数包装。
        - :func:`flowing.tool.flowing_tool` —— 打标装饰器。
        - :mod:`flowing.params` —— 声明层规则与桥接子集。
    """

    name: str
    """规范工具名（kebab-case）。**可省略**：缺省由类名 kebab 化推断；
    显式声明仅作一致性断言（不符抛
    :class:`flowing.errors.NameMismatchError`）；与显式 ``definition``
    互斥。
    """
    description: str
    """工具描述。类属性声明；缺省时按三级回退链取（T6 裁决）：
    显式 ``description`` > 类 docstring 首段 > ``execute()`` docstring 首段。
    """
    args_model: type[BaseModel] | None
    """参数声明（B1 裁决：Pydantic BaseModel 子类，声明即模型——LLM
    schema 由 ``model_json_schema()`` 派生、执行校验即模型本身）；
    ``None`` 时由 ``_infer_from_execute(self.execute)`` 从签名构建模型。
    """

    def __init__(self) -> None:
        """自动生成 ``self.definition``（同步构造，不触网、不注册）。

        .. rubric:: 行为规约

        - 顺序：显式 ``definition`` 类属性存在（且未同时声明
          ``name``/``description``/``args_model``）→ 直接使用；否则按
          ``args_model = self.args_model or _infer_from_execute(self.execute)``
          （未声明时从 ``execute`` 签名构建模型）→
          ``ToolDefinition(name=..., description=...,
          params_schema=args_model.model_json_schema()["properties"])``
          生成。
        - 后置条件：``self.definition`` 非 None；``self._has_caller`` 已
          按 `execute` 签名检测完毕。

        .. rubric:: 调用关系（审计）

        - 调用：``flowing.tool._infer_from_execute()`` （``args_model``
          为 ``None`` 时）；``flowing.tool.ToolDefinition`` 构造
        - 被调：script 工具实例化路径（用户子类构造 /
          ``flowing.tool._auto_generate_tool()``）
        """
        cls = type(self)
        if "definition" in cls.__dict__:
            if any(key in cls.__dict__ for key in ("name", "description", "args_model")):
                # 互斥规则：与 name/description/args_model 同时声明 -> 告警日志，
                # 以显式 definition 为准
                _logger.warning(
                    "ScriptTool 子类 %s 同时声明了 definition 与 "
                    "name/description/args_model——互斥规则：以显式 definition 为准",
                    cls.__name__)
            self.definition = cls.__dict__["definition"]
        else:
            # name 缺省由类名 kebab 化推断（pascal_to_kebab）；类体显式
            # 声明仅作一致性断言（不符 -> NameMismatchError）。只看本类
            # __dict__——继承来的 name 不参与断言（与 agent 侧
            # _load_agent_from_py 的口径一致）
            inferred_name = pascal_to_kebab(cls.__name__)
            explicit_name = cls.__dict__.get("name")
            if explicit_name is not None and explicit_name != inferred_name:
                raise NameMismatchError(explicit_name, inferred_name, cls.__name__)
            args_model = getattr(self, "args_model", None)
            if args_model is None:
                args_model = _infer_from_execute(self.execute)  # 从 execute 签名构建模型（B1）
            # description 三级回退链（T6）：显式声明 > 类 docstring 首段 >
            # execute() docstring 首段；皆无 -> 空串。注意用 cls.__doc__
            # 而非 inspect.getdoc(cls)——后者会继承基类 docstring
            description = getattr(cls, "description", None)
            if description is None:
                description = (_first_paragraph(cls.__doc__)
                               or _first_paragraph(self.execute.__doc__) or "")
            self.definition = ToolDefinition(
                name=inferred_name, description=description,
                params_schema=args_model.model_json_schema()["properties"])  # 声明即模型：schema 从模型派生
        self._has_caller = "caller" in inspect.signature(self.execute).parameters
        self._execution = None
        # S-33：创建时定内部校验模型——声明即模型（B1），无需再编译；
        # 显式 definition 路径下若未带模型，就地从 execute 签名构建兜底
        self._args_model = getattr(self, "args_model", None) or _infer_from_execute(self.execute)


class McpTool(Tool):
    
    """MCP 工具实例——连接 MCP 服务器并代理其暴露的工具 schema。
    Agent 侧 ``ToolEntry`` 的 ``inject`` / ``specified`` 对本类同样生效
    （绑定层与工具类型无关，见 ``ToolEntry.resolve``）。

    .. rubric:: 功能介绍

    ``type: mcp`` 的实例类。两种来源互斥：``command``（本地 stdio 进程）
    或 ``url``（远程 HTTP/SSE 端点）。默认 `ToolDefinition` 来自 MCP 服务器
    ``list_tools()`` 返回的 schema，可经 ``overrides`` 局部覆写。

    **命名规则**：MCP 声明块代理的是一组服务端工具，注册/解析时每个实际
    工具的规范名 = ``<fya 声明名>-<server 暴露工具名>``（如声明
    ``name: github``、服务端暴露 ``create-issue`` → 注册规范名
    ``github-create-issue``）——服务端工具名空间天然带声明名前缀，
    不同 MCP 来源的同名工具不撞名。Agent 侧引用（``tools:`` 条目 /
    ``add_tool``）按合成名引用（可照常 ``as`` 别名）。

    .. rubric:: 设计动机

    MCP 工具无用户代码，是纯参数化配置——每个定义独立实例（区别于 script
    单例）；来源识别规则化，歧义即报错而非猜测。

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
    ``{{ env.X }}`` 注入，不硬编码、不进消息、不落盘。

    .. rubric:: 行为规约

    - 来源识别：存在 ``command`` → stdio；存在 ``url`` → 远程；两者都有 →
      `AmbiguousMcpSourceError`；两者都无 → `MissingMcpSourceError`。
    - 同名冲突：同命名空间规范名重名注册永远抛 `ToolNameConflictError`
      ——注册名为合成名 ``<声明名>-<server 暴露名>``，撞名即声明名重复，
      须换声明名（或不同命名空间，§7a）。
    - MCP 服务器的 ``outputSchema`` **自动填入**
      `ToolDefinition.output_schema`，**仅作运行时校验**（归一化前对原料
      校验、含媒体跳过，D16）——不喂模型（保留校验价值，与 kimi 的显式
      丢弃不同）。
    - 非行为：初版不在 `execute` 内做 MCP 连接重试策略；连接失败 →
      ``status="error"`` 结果。

    .. rubric:: 调用关系（审计）

    - 调用：``无``
    - 被调：``无`` （执行经 ``Tool.__call__`` 调度链）
    - 实例化方：Agent 解析 ``.fya`` （``type: mcp``）时创建实例并注册
      （解析经 :func:`flowing.parser.parse_fya`，实例化在装配层）

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
        :raises AmbiguousMcpSourceError: ``command`` 与 ``url`` 同时给出。
        :raises MissingMcpSourceError: 两者均未给出。

        .. rubric:: 调用关系（审计）

        - 调用：``无``
        - 被调：``.fya`` 装配层（时机：Agent 装配解析 ``tools:`` 条目
          命中本类型时，经 :func:`flowing.parser.parse_fya` 产物构造）
        """
        if command is not None and url is not None:
            raise AmbiguousMcpSourceError("command 与 url 同时给出")  # 来源互斥
        if command is None and url is None:
            raise MissingMcpSourceError("command 与 url 均未给出")
        self.definition = definition
        self.command = command
        self.args = args
        self.env = env
        self.url = url
        self.headers = headers
        self.tools = tools
        self.overrides = overrides
        self._execution = None
        # S-33：创建时定内部校验模型（B1：fya 声明经 params.schema_to_model
        # 桥接——definition.params_schema 即桥接产物 schema，再建最终模型）
        self._args_model = schema_to_model("Args", self.definition.params_schema)


class CliTool(Tool):
    
    """CLI 工具实例——Jinja2 命令模板 + shell 执行。
    Agent 侧 ``ToolEntry`` 的 ``inject`` / ``specified`` 对本类同样生效
    （绑定层与工具类型无关，见 ``ToolEntry.resolve``）。

    .. rubric:: 功能介绍

    ``type: cli`` 的实例类。``args``（参数 schema）**必填**，无自动推断来源；
    命令体 ``command`` 为 Jinja2 模板，渲染上下文为 LLM 传入的 args。

    .. rubric:: 设计动机

    让「跑个命令」类能力零代码化；安全默认值：框架自动转义模板插入值，
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

    .. rubric:: 行为规约

    - ``shell`` 可选值：``sh``（默认）/ ``bash`` / ``ps`` / ``powershell`` /
      ``cmd``。
    - 不声明 ``output`` 时默认返回 ``{exit_code, stdout, stderr}``。
    - 非零退出码**不等于** ``status="error"``——exit_code 是正常输出数据；
      仅进程无法启动等框架级失败才产生 ``error``。

    :raises MissingSchemaError: 未声明 ``args`` 时（解析 ``.fya`` 阶段）。

    .. rubric:: 调用关系（审计）

    - 调用：``无``
    - 被调：``无`` （执行经 ``Tool.__call__`` 调度链）
    - 实例化方：Agent 解析 ``.fya`` （``type: cli``）时创建实例并注册
      （解析经 :func:`flowing.parser.parse_fya`，实例化在装配层）

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
        :param shell: 执行 shell，默认 ``sh``。

        .. rubric:: 调用关系（审计）

        - 调用：``无``
        - 被调：``.fya`` 装配层（时机：Agent 装配解析 ``tools:`` 条目
          命中本类型时，经 :func:`flowing.parser.parse_fya` 产物构造）
        """
        if not definition.params_schema:
            raise MissingSchemaError("cli 工具必须声明 args（无自动推断来源）")
        self.definition = definition
        self.command = command
        self.shell = shell
        self._execution = None
        # S-33：创建时定内部校验模型（B1：fya 声明经 params.schema_to_model
        # 桥接——definition.params_schema 即桥接产物 schema，再建最终模型）
        self._args_model = schema_to_model("Args", self.definition.params_schema)


class RequestTool(Tool):
    
    """HTTP/HTTPS 请求工具实例——按 ``args`` 构造请求，零代码。
    Agent 侧 ``ToolEntry`` 的 ``inject`` / ``specified`` 对本类同样生效
    （绑定层与工具类型无关，见 ``ToolEntry.resolve``）。

    .. rubric:: 功能介绍

    ``type: request`` 的实例类。``url`` 与 ``args`` **必填**；参数到请求的
    映射自动完成，可用 ``body``/``query`` 显式覆盖。

    .. rubric:: 设计动机

    与 `CliTool` 同理：把「调一个 HTTP API」降为纯声明；凭证只经
    ``{{ env.X }}`` 模板进入请求头，不进消息、不落盘。

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

    .. rubric:: 行为规约（args→请求映射）

    - URL 模板中出现的 ``{{ arg_name }}`` 识别为路径参数，自动从
      body/query 排除；
    - 非路径参数按 ``method`` 决定去向：``POST``/``PUT``/``PATCH`` → JSON
      body；``GET``/``DELETE`` → query string；``body``/``query`` 声明可
      显式覆盖；
    - ``auth`` 与 ``headers`` 同时声明时，``auth`` 生成的头优先；
    - 响应默认按 JSON 解析作为返回值；声明 ``output`` 时按 schema 提取
      字段，无关字段忽略；
    - 响应状态码不在 ``expected_status`` 内 → ``status="error"`` 结果
      （含状态码与响应摘要），不抛异常。

    :raises MissingSchemaError: 未声明 ``args`` 时。

    .. rubric:: 调用关系（审计）

    - 调用：``无``
    - 被调：``无`` （执行经 ``Tool.__call__`` 调度链）
    - 实例化方：Agent 解析 ``.fya`` （``type: request``）时创建实例并
      注册（解析经 :func:`flowing.parser.parse_fya`，实例化在装配层）

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

        .. rubric:: 调用关系（审计）

        - 调用：``无``
        - 被调：``.fya`` 装配层（时机：Agent 装配解析 ``tools:`` 条目
          命中本类型时，经 :func:`flowing.parser.parse_fya` 产物构造）
        """
        if not definition.params_schema:
            raise MissingSchemaError("request 工具必须声明 args")
        self.definition = definition
        self.url = url
        self.method = method
        self.headers = headers
        self.auth = auth
        self.query = query
        self.body = body
        self.expected_status = [200, 201] if expected_status is None else expected_status
        self.timeout = timeout
        self._execution = None
        # S-33：创建时定内部校验模型（B1：fya 声明经 params.schema_to_model
        # 桥接——definition.params_schema 即桥接产物 schema，再建最终模型）
        self._args_model = schema_to_model("Args", self.definition.params_schema)


TOOL_NAMING = NamingRules(
    suffixes=(".tool.fya", ".fya", ".py"),
    generic_names=frozenset({"TOOL.fya", "TOOL.py", "tool.fya", "tool.py"}),
)
"""Tool 资源的路径形态身份名推断规则表（:class:`flowing.paths.NamingRules`）。

紧邻 :meth:`ToolRegistry.get` 的定向查找链声明（规约与实现同源）：
``TOOL.fya`` / ``TOOL.py`` / ``tool.fya`` / ``tool.py`` 通用文件名命中时
身份名取**目录名**；其余按后缀剥离取文件名（``.tool.fya`` 先于
``.fya``），结果经 snake→kebab 规范化。使用方：
:func:`flowing.parser.normalize_entries`（``naming=TOOL_NAMING``）与
name 断言的推断侧（`ToolRegistry.get` 行为规约）。

.. seealso:: :data:`flowing.runtime.AGENT_NAMING`、
:data:`flowing.plugins.skills.SKILL_NAMING`
"""


class ToolRegistry:
    """Runtime 全局工具注册表——``ns::name`` 全限定键 → Tool 实例。

    .. rubric:: 功能介绍

    每个 Runtime 持有一个实例（``runtime.tool_registry``）。注册时间线：
    **无启动扫描**（「无默认扫描目录」基调）——核心工具（``finish`` /
    ``subagent-invoke``）随 ``Runtime.__init__`` 注册；插件工具在阶段一
    ``install()`` 注册；文件形态工具由 :meth:`get` **引用触发**
    惰性解析并注册；MCP/CLI/Request 在 Agent 解析 ``.fya`` 时创建实例并
    注册。

    .. rubric:: 设计动机

    **重名约束按 ``ns::name`` 全限定键判定**（T8 口径修正）：同一命名
    空间内重名 → 后注册者抛 ``ToolNameConflictError``；**不同命名空间的
    同名工具允许共存**。需要同一 MCP 服务器不同配置时用不同命名空间或
    规范名，需要相同实例时复用已有注册。裸名引用的注册表视图依次查
    ``default::``、``builtin::``（``default`` 优先 = 插件覆盖原生行为的
    通道）；自定义命名空间的资源只能以 ``ns::name`` 全限定名引用
    （见 ``flowing.runtime`` 模块 docstring §7a）。别名冲突不存在——
    别名是 `ToolEntry` 层（Agent 本地）的概念。

    .. rubric:: 使用示例

    .. code-block:: python

        runtime.tool_registry.register(MakePayment())          # script 单例
        tool = runtime.tool_registry.get("make-payment")       # 按规范名取

    .. rubric:: 行为规约

    - 不变量：任意时刻一个 ``ns::name`` 全限定键至多映射一个 Tool 实例
      （不同命名空间同名允许共存）。
    - 非行为：不提供别名查找（别名在 `ToolEntry` 层）；不提供 unregister
      （初版无此需求，销毁随 Runtime 生命周期）。

    .. rubric:: 调用关系（审计）

    - 调用：``无``
    - 被调：``flowing.runtime.Runtime.register_tool()`` （注册写入）；
      ``flowing.tool.ToolEntry.llm_definition()`` 与
      ``before_tool_call`` handler 审批路径（按规范名读取）
    - 实例化方：``flowing.runtime.Runtime.__init__()`` （构造时初始化
      全部空存储，本表为其一）

    .. seealso::

        - :attr:`flowing.runtime.Runtime.tool_registry` —— 挂载点。
        - :class:`flowing.tool.ToolEntry` —— 按 ``name_ori`` 查本表。
    """

    _tools: dict[str, Tool]
    """``ns::规范名`` → Tool 实例（命名空间规则见 ``flowing.runtime`` 模块
    docstring §7a）。内部 API，不属稳定契约。
    """

    def __init__(self) -> None:
        # R-02 占位补齐（spec 骨架无显式构造段）：空注册表。
        self._tools = {}

    def register(self, tool: Tool, *, name: str | None = None,
                 namespace: str | None = None) -> None:
        """注册工具实例。

        :param tool: 工具实例；script 类型应注册全局单例。
        :param name: 规范名覆写；``None`` 时取 ``tool.definition.name``。
        :param namespace: 命名空间；``None`` → ``"default"``。注册表 key 为
            ``ns::name``——核心内置工具归 ``builtin::``；裸名引用的注册表
            视图依次查 ``default::``、``builtin::``（``default`` 优先 =
            插件覆盖原生行为的通道），自定义命名空间只能以 ``ns::name``
            全限定名引用（见 ``flowing.runtime`` 模块 docstring §7a）。
        :raises ToolNameConflictError: ``ns::name`` 全键已存在（不同命名
            空间的同名工具允许共存）。

        .. rubric:: 测试案例

        - 前置：已注册 ``"default::github"``（某 MCP 配置）→ 操作：以同名
          同命名空间注册另一 MCP 配置 → 期望：抛 `ToolNameConflictError`，
          原条目不变。
        - 前置：内置 ``builtin::web-search`` 已存在 → 操作：插件注册同名
          工具（缺省 ``default::``）→ 期望：不报错，裸名 ``web-search``
          解析到插件版本（覆盖通道），``builtin::web-search`` 仍可显式
          引用。

        .. rubric:: 调用关系（审计）

        - 调用：``无``
        - 被调：``flowing.runtime.Runtime.register_tool()`` （应用层注册
          入口，每次注册）；``flowing.plugins.skills.SkillPlugin`` 阶段一
          （经 ``runtime.register_tool`` 注册 ``SkillLoadTool``）
        """
        ns = namespace or "default"
        bare = name if name is not None else tool.definition.name
        key = f"{ns}::{bare}"
        if key in self._tools:
            raise ToolNameConflictError(f"规范名重名注册：{key}")  # 全键重名永远不允许
        self._tools[key] = tool
        tool.registry_key = key   # 回写全键（Entry 装配对文件派生工具落账 name_ori 的依据）

    def get(self, name_or_path: str, *,
            source_dir: Path | None = None) -> Tool:
        """工具的**唯一解析入口**：注册表快路径 + 文件链慢路径合一。

        .. rubric:: 功能介绍

        形态判别委托 :func:`flowing.paths.classify_ref`（词法唯一来源），
        三分语义：

        - **限定名**（含 ``::``，如 ``myplugin::web-search``）：**只查注册表**
          精确键，不走文件查找链（命名空间无法反向映射到文件）；
        - **裸名**（如 ``payment``）：``source_dir`` 提供时**先走定向文件
          查找链**（相对 ``source_dir``——文件覆盖注册表）：命中后按所在
          目录派生键（``@/`` 下相对、根外绝对、文件夹式取上层目录，仅作
          内部身份标识）**短路复用**已注册实例，未注册才实例化并注册；
          ``source_dir`` 缺省时跳过文件链。之后查注册表裸名视图——
          ``default::`` 优先于 ``builtin::``（插件覆盖原生行为的通道）；
        - **路径形态**：``@/`` 经 ``project_root`` 定位（无需
          ``source_dir``）；``./`` / ``../`` 需 ``source_dir``，缺省时
          报错（``resolve_path`` 现有口径）。跳过注册表，定位后走候选链；
          命中后同样注册（命名空间派生规则同上）。

        **定向查找链**（按规范名 ``<name>``，``<name_snake>`` 为其 snake_case
        形式——转换经 :func:`flowing.paths.kebab_to_snake`；首个存在者生效，
        探测循环委托 :func:`flowing.paths.probe_candidates`）::

            目录内（<name>/ 存在时）：
                TOOL.fya > <name>.tool.fya > <name>.fya
              > TOOL.py > tool.py > <name_snake>.py
            目录外：
                <name>.tool.fya > <name>.fya > <name_snake>.py

        链上顺序只是**确定性裁决规则**——不推荐同一链路真的同时存在多个候选
        文件（属组织异味：读者需回溯优先级才能确定生效者）。

        .. rubric:: 设计动机（单入口合并）

        「查注册表」与「解析并注册」曾是两个方法（``get`` /
        ``get_or_resolve``）；合并为单入口（用户裁决）——调用方不再
        关心快慢路径，解析只有一个权威（与 ``Runtime.get_agent_class``
        同构）。两处语义变化：

        - **fail-fast 口径**：裸名未注册时，``source_dir`` 提供则先走
          文件查找链、均不命中才报「注册表与查找链均不命中」；
          ``source_dir`` 缺省则只查注册表、不命中即报错，不做文件探测；
          纯存在性检查用 ``__contains__``（仅认全限定键，P3-07）；
        - **热路径口径**：``ToolEntry.llm_definition()``（每轮上下文
          组装）与 ``before_tool_call`` 审批路径调本方法时**必命中
          注册表快路径**——Entry 在装配期已解析落账（文件命中的落账
          派生限定键，注册表命中的落账裸名），文件解析是声明期行为，
          运行时不触发文件 IO。

        .. rubric:: 行为规约

        - 形参承载规范名 / 限定名 / 路径，**非别名**——别名到规范名的
          换算在 Agent 绑定层（``ToolEntry``）。
        - **继续向下仅裸名语境**：裸名查找时目录存在但无合法入口 → 继续
          链上下一项；**显式路径**语境下目录无候选 → 直接报错（定点引用
          的目录为空几乎必为笔误）。
        - ``.fya`` 与同名 ``.py`` 并存 → 告警 + ``.fya`` 优先（与 Agent
          的「``.fya`` 优先于手写子类」同措辞）。
        - ``.py`` 命中后：恰好一个 ``@flowing_tool`` 打标函数 →
          ``_auto_generate_tool`` 提升；或恰好一个 `ScriptTool` 子类 →
          实例化；两者并存 / 多个打标函数 →
          :class:`flowing.errors.AmbiguousToolError`；皆无 →
          :class:`flowing.errors.FormatError`。
        - ``name`` 断言：命中对象的显式 ``name`` 声明（``.fya`` 字段 /
          类属性 / 装饰器参数）必须与 ``<name>`` 一致，不符抛
          :class:`flowing.errors.NameMismatchError`；``<name>`` 的推断
          本体为 :func:`flowing.paths.infer_name`（规则表
          :data:`TOOL_NAMING`，紧邻本链声明）。
        - 非行为：不做 glob 展开（``tools:`` 条目的 glob 在装配层
          展开后逐条进本方法）。装配层遵循「显式优先、glob 跳过同规范名」
          （与 skills/subagents 同构）；仅不同资源得到同 alias 时才报
          ``EntryNameConflictError``。

        :param name_or_path: 规范名（裸名）、限定名（``ns::name``）或
          路径形态字符串。
        :param source_dir: 裸名文件链的查找根与 ``./``/``../`` 的相对
          基准。缺省（``None``）时：裸名只查注册表（``default::`` /
          ``builtin::``），相对路径报错。声明期调用点（``.fya`` 装配、
          ``Agent.add_tool``）义务性传入引用方 Agent 的
          ``source_file`` 所在目录——推荐经
          :meth:`flowing.agent.Agent.get_tool` 自动携带。
        :return: 已注册的 Tool 实例。
        :raises flowing.errors.ToolNotFoundError: 注册表与查找链均不
          命中。
        :raises flowing.errors.FormatError: 显式路径目录无候选、``.py``
          无任何合法定义、或 ``callable:`` 指向已装饰函数。

        .. rubric:: 测试案例

        - 前置：``tools: [payment]`` 且 ``payment/TOOL.fya`` 存在，
          ``source_dir`` 提供 → 期望：文件链命中 → 按所在目录派生键
          注册后返回；再次同名调用（同 ``source_dir``）经派生键短路
          复用现有实例（不重复实例化）。
        - 前置：裸名 + ``source_dir=None`` → 期望：只查
          ``default::``/``builtin::``，不命中即 ``ToolNotFoundError``，
          不做文件探测。
        - 前置：``- ./tools/payment`` 指向空目录 → 期望：
          ``FormatError``（显式路径不继续向下）。

        .. rubric:: 调用关系（审计）

        - 调用：:func:`flowing.paths.classify_ref`（形态判别，入口）；
          ``flowing.runtime.Runtime.resolve_path()``（路径形态定位）；
          :func:`flowing.paths.probe_candidates`（候选链探测）；
          ``flowing.tool._auto_generate_tool()``（打标函数提升）；
          ``self.register()``（命中后落账）
        - 被调：``flowing.agent.Agent.add_tool()``（``name`` 未注册
          的边缘情况，每次添加条目）；``.fya`` ``tools:`` 条目解析（声明
          期，每条目一次）；``flowing.tool.ToolEntry.llm_definition()``
          第 1 步（每次上下文组装，必命中快路径）；``before_tool_call``
          handler 审批路径（每次工具调用，经 ``agent.runtime.tool_registry``，
          必命中快路径）

        .. seealso::

            - :func:`flowing.tool.flowing_tool` —— 打标通道。
            - :meth:`flowing.runtime.Runtime.get_agent_class` ——
              同构的 Agent 解析管线。
        """
        if classify_ref(name_or_path) != "path":
            # 快路径:限定名精确键 / 裸名 default:: > builtin:: 裸名视图
            keys = ([name_or_path] if "::" in name_or_path
                    else [f"default::{name_or_path}", f"builtin::{name_or_path}"])
            # R-02 占位：裸名 + source_dir 的定向文件查找链属阶段 3（文件
            # 编译链），本期跳过文件链、只查注册表快路径。
            for key in keys:
                if key in self._tools:
                    return self._tools[key]
        # R-02 占位：慢路径（路径形态定位 + 候选链探测 + 实例化注册）属
        # 阶段 3 文件编译链，本期不实现——不命中统一报 ToolNotFoundError。
        raise ToolNotFoundError(f"工具未注册且查找链不命中:{name_or_path}")

    def get_tool_class(self, name_or_path: str, *,
                       source_dir: Path | None = None) -> type[Tool]:
        """取已解析工具的**类对象**（与 ``Runtime.get_agent_class`` 对称）。

        .. rubric:: 功能介绍

        薄委托 ``type(self.get(...))``——不另开解析路径，单一解析权威
        仍是 :meth:`get`；script 打标工具返回 ``_auto_generate_tool``
        提升出的 ``ScriptTool`` 子类。

        .. rubric:: 设计动机

        三资源对齐：Agent 侧解析产物是类（``Runtime.get_agent_class``），
        Tool 侧注册产物是单例实例；编译发射、测试断言（``issubclass``）、
        子类化扩展等场景要的是类而非实例。薄委托保证「类语义」与
        「实例语义」永不漂移。

        .. rubric:: 行为规约

        - 解析 / 注册 / 缓存语义全部继承 :meth:`get`（含限定名只查
          注册表、命名空间派生、fail-fast 口径）。
        - 非行为：不绕过注册表直接加载文件；不实例化新对象（取的是
          已注册单例的类）。

        :param name_or_path: 同 :meth:`get`。
        :param source_dir: 同 :meth:`get`。
        :return: 已注册单例的类（``type(实例)``）。
        :raises flowing.errors.ToolNotFoundError: 同 :meth:`get`。

        .. rubric:: 测试案例

        - 前置：``default::make-payment`` 已注册 → 期望：
          ``get_tool_class("make-payment") is type(registry.get("make-payment"))``。

        .. rubric:: 调用关系（审计）

        - 调用：``self.get()``（每次调用，唯一解析路径）
        - 被调：编译/测试/子类化场景（用户代码）

        .. seealso:: :meth:`get` —— 唯一解析入口；
            :meth:`flowing.runtime.Runtime.get_agent_class` —— 对称的
            Agent 侧入口。
        """
        return type(self.get(name_or_path, source_dir=source_dir))

    def __contains__(self, name: str) -> bool:
        """全限定键（``命名空间::规范名``）是否已注册。

        .. rubric:: 功能介绍

        ``x in registry`` 运算符的落点——O(1) 哈希查找的**低层容器协议**。
        只认全限定键：``"builtin::read" in registry`` 为真；**裸名永不命中**
        （``"read" in registry`` 恒为假，即便 ``builtin::read`` 在场）。

        .. rubric:: 设计动机（P3-07 裁决：仅全限定）

        裸名解析（``default::`` 优先、``builtin::`` 兜底的优先级链）是
        :meth:`get` 的**专属职责**——若 ``in`` 也做裸名展开，同一对象上将
        存在两套语义重叠的查询通道，且 ``in`` 的解析结果不可见（只回布尔值），
        排查更绕。保持 ``in`` 廉价、无歧义、零解析逻辑。

        .. rubric:: 使用示例

        .. code-block:: python

            "builtin::read" in runtime.tools   # True（全限定键）
            "read" in runtime.tools            # False——裸名请用 get()：
            runtime.tools.get("read")          # 走命名空间优先级解析链

        .. rubric:: 调用关系（审计）

        - 调用：``无``
        - 被调：``无`` （全库未见框架内调用点）
        """
        return name in self._tools

    def __iter__(self) -> Iterator[Tool]:
        """遍历全部已注册实例（顺序不保证）。

        .. rubric:: 调用关系（审计）

        - 调用：``无``
        - 被调：``无`` （全库未见框架内调用点）
        """
        return iter(self._tools.values())


_FLOWING_TOOL_MARKS: set[Callable[..., Any]] = set()
"""进程级打标表（``@flowing_tool`` 登记处）。

发现机制是函数对象上的 ``__flowing_tool_name__`` 标记属性（``.py`` 命中后
模块扫描逐对象检查）；本表仅作打标行为的登记——import 期不需要 Runtime
存在，多 Runtime 安全。内部 API，不属稳定契约。
"""


def flowing_tool(fn: Callable[..., Any] | None = None, *,
                 name: str | None = None) -> Any:
    """``@flowing_tool`` 打标装饰器——裸函数 → 工具类的自动提升标记。

    .. rubric:: 功能介绍

    script 工具的两条定义通道之一（另一条是手写 `ScriptTool` 子类）。
    装饰器**只打标不注册**：在函数上记录标记（进程级打标表），实例化与
    注册推迟到工具被引用时由
    :meth:`flowing.tool.ToolRegistry.get` 完成——import 期
    不需要 Runtime 存在，多 Runtime 安全。

    两种用法：``@flowing_tool``（名字由文件名/目录名推断）或
    ``@flowing_tool("make-payment")``（参数仅作**一致性断言**——必须与
    推断名一致，不符抛 :class:`flowing.errors.NameMismatchError`）。

    .. rubric:: 设计动机

    取消「裸函数隐式自动提升」：定义处的显式打标让「这个函数是工具」成为
    作者意图而非框架猜测；断言式命名与 Agent / Skill 的 ``name`` 语义统一。

    .. rubric:: 行为规约

    - **每个 ``.py`` 文件至多一个打标函数**；与 Tool 子类同文件并存或
      含多个打标函数 → :class:`flowing.errors.AmbiguousToolError`。
    - **通道互斥**：被 ``TOOL.fya`` 的 ``callable: {路径}::{函数名}``
      指向的函数不允许打标（显式指针通道与自动提升通道二选一），违反
      抛 :class:`flowing.errors.FormatError`。
    - 名字推断：文件名去 ``.py`` 并 snake → kebab 规范化；
      ``TOOL.py`` / ``tool.py`` 通用名时取目录名。
    - 非行为：不实例化 Tool、不触碰任何 Runtime / 注册表；返回原函数。

    :param fn: 被装饰函数（无参用法由 Python 装饰器协议传入）。
    :param name: 一致性断言参数；``None`` 时纯推断。

    .. rubric:: 测试案例

    - 前置：``make_payment.py`` 含 ``@flowing_tool`` 函数 → 操作：
      ``get("make-payment")`` → 期望：自动提升并注册。
    - 前置：``@flowing_tool("other-name")`` 与文件名不符 → 期望：
      ``NameMismatchError``。

    .. rubric:: 调用关系（审计）

    - 调用：``无``（仅打标与登记打标表）
    - 被调：用户工具定义文件（import 期）；产物由
      ``flowing.tool.ToolRegistry.get`` 消费（时机：引用触发
      的惰性解析）

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
    """从 `execute()` 签名**构建** Pydantic 参数模型。内部 API，不属稳定契约。

    .. rubric:: 行为规约

    - 来源（B1：产物是模型，不再是 ParamSpec dict）：参数类型标注 →
      字段类型（``str/int/float/bool/list/dict`` 等直接映射，复杂标注交
      Pydantic）；``Field`` 语义的默认值 → 字段 default（有默认 →
      可选，无 → 必填）；``caller`` 参数跳过（框架注入，不是 LLM
      参数）；``*args``/``**kwargs`` 形态跳过（不进 schema）。
    - 构建经 ``pydantic.create_model``（与 fya 桥接
      :func:`flowing.params.schema_to_model` 同一建模入口）。
    - :raises MissingSchemaError: 任一业务参数缺类型标注（构建不出
      字段类型）。

    .. rubric:: 调用关系（审计）

    - 调用：``pydantic.create_model``
    - 被调：``flowing.tool.ScriptTool.__init__()`` （``args_model`` 未
      声明时）；``flowing.tool._auto_generate_tool()`` （打标函数元信息
      提取）

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
            raise MissingSchemaError(f"参数缺类型标注：{param_name}")
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

    .. rubric:: 行为规约

    - 等价于 ``type("<PascalName>", (ScriptTool,), {"execute":
      staticmethod(fn)})`` 后实例化；元信息按「文件名/docstring/类型标注」
      提取（名字推断与装饰器参数断言规则见 :func:`flowing_tool`）。
    - :raises MissingSchemaError: 参数缺类型标注。
    - :raises AmbiguousToolError: 同一 ``.py`` 同时存在打标函数与 Tool
      子类、或多个打标函数（由定向查找层抛出，不在本函数内）。

    .. rubric:: 调用关系（审计）

    - 调用：``flowing.tool._infer_from_execute()`` （元信息提取——
      签名构建模型，B1）
    - 被调：``flowing.tool.ToolRegistry.get()`` 的 script
      提升路径（时机：引用触发的惰性解析命中打标函数文件）

    .. seealso::

        - :class:`flowing.tool.ScriptTool` —— 两条定义路径的共同产物。
        - :func:`flowing_tool` —— 打标入口。
    """
    args_model = _infer_from_execute(fn)  # 元信息提取：签名构建模型（B1）
    # name ← 文件名去 .py 并 snake → kebab 规范化（TOOL.py/tool.py 通用名
    # 取目录名）；装饰器参数若给出仅作一致性断言（不符 -> NameMismatchError）
    name = infer_name(fn.__code__.co_filename, naming=TOOL_NAMING)
    declared = getattr(fn, "__flowing_tool_name__", None)
    if declared is not None and declared != name:
        raise NameMismatchError(declared, name, fn.__code__.co_filename)
    # description 三级回退链（T6）在打标函数形态下的落点：显式声明无通道
    # （装饰器无 description 形参）、无类 docstring —— 取函数 docstring 首段
    description = _first_paragraph(fn.__doc__) or ""
    cls = type(kebab_to_pascal(name), (ScriptTool,), {
        "execute": staticmethod(fn),
        "name": name,
        "description": description,
        "args_model": args_model,
    })
    return cls()
