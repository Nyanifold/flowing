"""Anthropic Messages 格式家族（``flowing.providers.anthropic``）。

框架基类 :class:`AnthropicMessagesProvider` 收拢 Anthropic Messages API
的请求/响应映射（system 数组、content blocks、tool_use/tool_result、
手动前缀缓存 ``cache_control``）；内置厂商 adapter（Anthropic 官方端点、
AWS Bedrock）各为一个子类。设计动机见 :mod:`flowing.providers` 包
docstring。

工具结果与媒体映射（详见 :mod:`flowing.providers` 包 docstring「工具
结果映射」rubric）：TOOL/EVENT 消息的 ``content`` 块直接映射进 user
消息内的 ``tool_result`` 块——消息级 ``tool_call_id`` →
``tool_use_id``，``tool_status="error"`` → ``is_error: true``；媒体块
原生内嵌于 ``tool_result.content``（image/document），无需转移；
``StructBlock`` 恒投影为 ``json.dumps(ensure_ascii=False)`` 文本。

响应侧事实：``thinking`` 块的 ``signature`` 为不透明签名字符串，
多轮回放必须原样带回；``tool_use`` 的 ``input`` 为已解析 JSON 对象，
流式以 ``input_json_delta`` 字符串分片传输、按 content block ``index``
路由（并行 tool_use 的分片可跨块交错）；流式参数拼装策略（字符串累积
完成后解析 / partial-json 容错解析 + 截断拒执行）由本 adapter 选定并
写明，属实现细节。
"""

from __future__ import annotations   # S-43 裁决③：注解延迟求值

from typing import Any, ClassVar

import json

from flowing.context import Context
from flowing.errors import (
    AuthenticationError,
    ContentPolicyError,
    ContextLengthError,
    FlowingError,
    InvalidRequestError,
    NetworkError,
    ProviderError,
    ProviderTimeoutError,
    RateLimitedError,
    RequestTooLargeError,
    ServerError,
)
from flowing.message import (
    MediaBlock,
    Message,
    MessageKind,
    StructBlock,
    TextBlock,
    ThinkingBlock,
    ToolCallBlock,
)
from flowing.model import ModelConfig
from flowing.providers.provider import (
    Provider,
    ProviderResponse,
    Usage,
    register_provider,
)


class _HttpResponseError(Exception):
    """传输层非 2xx 响应的内部载体（与 openai 家族同构；归类前原始事实）。"""

    def __init__(self, status_code: int, body: Any = None,
                 headers: dict[str, str] | None = None) -> None:
        super().__init__(f"HTTP {status_code}")
        self.status_code = status_code
        self.body = body
        self.headers = headers or {}




class AnthropicMessagesProvider(Provider):
    """Anthropic Messages 格式的框架基类（``api_format="anthropic_messages"``）。

    .. rubric:: 功能介绍

    Anthropic Messages API 家族 adapter 的基类：实现 system 数组、
    content blocks、tool_use/tool_result 的格式映射与 Usage 映射。

    .. rubric:: 设计动机

    与 :class:`OpenAICompletionsProvider` 并列的第二个格式家族。本格式
    的前缀缓存是**手动**的（``cache_control: {type: "ephemeral"}``，
    不标记 = 不缓存），因此 ``PromptBlock.cache == "static"`` 对
    本基类是设置 ``cache_control`` 的**必要信息**，不是可选优化。

    .. rubric:: 使用示例

    .. code-block:: python

        @register_provider
        class AnthropicProvider(AnthropicMessagesProvider):
            name = "anthropic"

    .. rubric:: 行为规约

    - 期待行为：``cache="static"`` 的 PromptSegment 设置
      ``cache_control``；``cache="dynamic"`` 不标记（动态内容不进入
      缓存断点）；``cache="session"`` 按会话冻结语义处理。
      ``stop_reason == "tool_use"`` → ``finish=False``，其余 →
      ``finish=True``，原始值保留在 ``provider_data``。
    - Usage 映射：``fresh_input`` / ``output`` 直取原生
      ``input_tokens`` / ``output_tokens``（Anthropic 原生 input 不含
      cache）；``input = fresh_input + cache_read + cache_write``
      按恒等式归一组装；``cache_creation_input_tokens`` /
      ``cache_read_input_tokens`` 升入一等字段，同时原样保留在
      ``Usage.raw``。
    - 非行为：不替用户决定缓存断点数量与位置之外的策略；压缩/清理
      策略（如 ``cache_edits``）不进入框架核心设计。

    .. rubric:: 测试案例

    - 前置：Context 含一个 ``cache="static"`` segment。操作：
      ``generate()``。期望：请求体对应 block 带 ``cache_control``；
      ``cache="dynamic"`` segment 不带。

    .. rubric:: 调用关系（审计）

    - 被调：具体子类继承（``AnthropicProvider`` / ``BedrockProvider``，
      import 期经 ``register_provider`` 注册）
    - 实例化方：无（框架基类不直接实例化；实例化经具体子类的懒创建
      链，见 :class:`Provider`）

    .. seealso::

        :class:`OpenAICompletionsProvider` 对照的格式家族。
        :class:`flowing.context.PromptBlock` ``cache`` 三值语义。
    """

    api_format: ClassVar[str] = "anthropic_messages"
    known_model_fields: ClassVar[frozenset[str]] = frozenset({"thinking_budget"})
    default_base_url: ClassVar[str | None] = None
    """子类覆写：厂商官方端点；条目配 ``base_url`` 时以条目为准。"""

    anthropic_version: ClassVar[str] = "2023-06-01"
    """``anthropic-version`` 请求头值。"""

    # ── 网络点（唯一）──────────────────────────────────────────────────

    async def _post(self, path: str, body: dict) -> dict:
        """发起一次 POST 并返回解析后的 JSON（**唯一网络点**，子类/测试可覆写）。

        行为边界与 openai 家族同构：非 2xx → :class:`_HttpResponseError`；
        超时 → ``ProviderTimeoutError``；传输层失败 → ``NetworkError``。
        """
        import httpx

        base_url = self.config.get("base_url") or self.default_base_url
        if not base_url:
            raise FlowingError(
                f"provider 条目缺少 base_url（adapter {self.name!r} 无官方默认端点）")
        headers = {
            "Content-Type": "application/json",
            "anthropic-version": self.anthropic_version,
        }
        credential = self.get_credential()   # 每次发起请求前读取（凭证唯一入口）
        if credential:
            headers["x-api-key"] = credential
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(120.0)) as client:
                resp = await client.post(
                    f"{str(base_url).rstrip('/')}{path}", json=body, headers=headers)
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError(f"provider 请求超时：{exc}") from exc
        except httpx.TransportError as exc:
            raise NetworkError(f"provider 网络层失败：{exc}") from exc
        if resp.status_code >= 400:
            try:
                err_body: Any = resp.json()
            except Exception:
                err_body = resp.text
            raise _HttpResponseError(resp.status_code, err_body, dict(resp.headers))
        return resp.json()

    # ── 请求映射 ─────────────────────────────────────────────────────────

    def _build_request(self, context: Context, model: ModelConfig) -> dict:
        """把 ``Context`` 三字段与模型规格映射为 Anthropic Messages 请求体。"""
        body: dict[str, Any] = {
            "model": model.model,
            "system": [
                # 手动前缀缓存：cache="static" 设 cache_control 必要信息；
                # cache="dynamic" 不标记；cache="session" 初版不映射断点
                # （会话冻结语义的断点策略属后续细化，就地注释说明）
                {"type": "text", "text": seg.content,
                 **({"cache_control": {"type": "ephemeral"}}
                    if seg.cache == "static" else {})}
                for seg in context.system_prompt
            ],
            "messages": [m for msg in context.messages
                         for m in self._map_message(msg)],
            "max_tokens": model.max_output_tokens or 8192,   # Anthropic 必填
        }
        if context.tools:
            body["tools"] = [self._map_tool(d) for d in context.tools]
        if model.thinking_budget:
            body["thinking"] = {"type": "enabled",
                                "budget_tokens": model.thinking_budget}
        return body

    def _map_tool(self, definition) -> dict:
        """白名单组装（D21）：只取 name/description/parameters 三已知字段。"""
        params = definition.params_schema or {}
        return {
            "name": definition.name,
            "description": definition.description,
            "input_schema": {
                "type": "object",
                "properties": params,
                "required": [k for k, v in params.items() if "default" not in v],
            },
        }

    def _map_plain_blocks(self, msg: Message) -> list[dict[str, Any]]:
        """纯内容块 → Anthropic content 块（媒体原生内嵌，无需转移）。"""
        out: list[dict[str, Any]] = []
        for block in msg.content:
            if isinstance(block, StructBlock):
                # StructBlock 恒投影为 json.dumps 文本（所有 adapter 统一）
                out.append({"type": "text",
                            "text": json.dumps(block.data, ensure_ascii=False)})
            elif isinstance(block, MediaBlock):
                out.append({
                    "type": "image",
                    "source": {"type": "base64",
                               "media_type": block.mime_type or "application/octet-stream",
                               "data": block.data},
                })
            elif isinstance(block, TextBlock):
                out.append({"type": "text", "text": block.text})
        return out

    def _map_message(self, msg: Message) -> list[dict[str, Any]]:
        """单条消息 → Anthropic messages 数组元素。"""
        if msg.kind is MessageKind.PROVIDER:
            content: list[dict[str, Any]] = []
            for block in msg.content:
                if isinstance(block, ToolCallBlock):
                    content.append({"type": "tool_use", "id": block.id,
                                    "name": block.name, "input": block.args})
                elif isinstance(block, ThinkingBlock):
                    # thinking 块的 signature 为不透明签名字符串，回放原样带回
                    content.append({"type": "thinking", "thinking": block.thinking,
                                    "signature": block.signature or ""})
                elif isinstance(block, StructBlock):
                    content.append({"type": "text", "text": json.dumps(
                        block.data, ensure_ascii=False)})
                elif isinstance(block, TextBlock):
                    content.append({"type": "text", "text": block.text})
            return [{"role": "assistant", "content": content}]
        if msg.kind is MessageKind.TOOL:
            # TOOL 消息块直接映射进 user 消息内的 tool_result 块：
            # tool_call_id → tool_use_id；tool_status="error" → is_error
            return [{"role": "user", "content": [{
                "type": "tool_result",
                "tool_use_id": msg.tool_call_id,
                "content": self._map_plain_blocks(msg) or [
                    {"type": "text", "text": ""}],   # 空 content 兜底（pending 收据等）
                **({"is_error": True} if msg.tool_status == "error" else {}),
            }]}]
        return [{"role": "user", "content": self._map_plain_blocks(msg)}]

    # ── 响应映射 ─────────────────────────────────────────────────────────

    def _map_response(self, resp: dict) -> ProviderResponse:
        """录制/真实响应 dict → ``ProviderResponse``（usage 附着到消息）。"""
        blocks: list = []
        for raw in resp.get("content") or []:
            btype = raw.get("type")
            if btype == "text":
                blocks.append(TextBlock(text=raw.get("text", "")))
            elif btype == "thinking":
                blocks.append(ThinkingBlock(thinking=raw.get("thinking", ""),
                                            signature=raw.get("signature")))
            elif btype == "tool_use":
                blocks.append(ToolCallBlock(id=raw["id"], name=raw["name"],
                                            args=raw.get("input") or {}))
        msg = Message(kind=MessageKind.PROVIDER, content=blocks)
        raw_usage = resp.get("usage")
        if raw_usage:   # Anthropic 原生 input_tokens 不含 cache，按恒等式归一组装
            fresh = raw_usage.get("input_tokens", 0)
            cache_read = raw_usage.get("cache_read_input_tokens", 0)
            cache_write = raw_usage.get("cache_creation_input_tokens", 0)
            output = raw_usage.get("output_tokens", 0)
            msg.usage = Usage(
                input=fresh + cache_read + cache_write,
                fresh_input=fresh,
                output=output,
                cache_read=cache_read,
                cache_write=cache_write,
                reasoning=0,
                total_tokens=fresh + cache_read + cache_write + output,
                raw=dict(raw_usage),
            )
        stop_reason = resp.get("stop_reason")
        has_tool_call = any(b.type == "tool_call" for b in blocks)
        return ProviderResponse(
            message=msg,
            model=resp.get("model", ""),
            finish=not has_tool_call and stop_reason != "tool_use",
            provider_data={"stop_reason": stop_reason},
        )

    # ── 错误归类 ─────────────────────────────────────────────────────────

    def _classify_error(self, exc: "_HttpResponseError") -> ProviderError:
        """HTTP 状态码 → 异常分类（分类表见包 docstring；与 openai 家族同口径）。"""
        status = exc.status_code
        body = exc.body if isinstance(exc.body, dict) else {}
        message = (body.get("error") or {}).get("message") or str(exc.body or exc)
        kwargs: dict[str, Any] = {"status_code": status}
        if status in (401, 403):
            return AuthenticationError(message, **kwargs)
        if status == 429:
            return RateLimitedError(message, **kwargs)
        if status == 413:
            return RequestTooLargeError(message, **kwargs)
        if status == 400:
            lowered = message.lower()
            if "context" in lowered and ("length" in lowered or "window" in lowered
                                         or "too long" in lowered):
                return ContextLengthError(message, **kwargs)
            if "content_policy" in lowered or "content policy" in lowered:
                return ContentPolicyError(message, **kwargs)
            return InvalidRequestError(message, **kwargs)
        if status >= 500:
            return ServerError(message, **kwargs)
        return ProviderError(message, **kwargs)

    # ── 契约方法 ─────────────────────────────────────────────────────────

    async def generate(
        self, context: Context, model: ModelConfig
    ) -> ProviderResponse:
        """非流式单次生成（映射/错误归类见各 ``_*`` 方法）。

        .. rubric:: 调用关系（审计）

        - 调用：``_build_request`` / ``_post`` / ``_map_response`` /
          ``_classify_error``（每次调用）
        - 被调：同 :meth:`Provider.generate` 契约
        """
        body = self._build_request(context, model)
        try:
            resp = await self._post("/v1/messages", body)
        except _HttpResponseError as exc:
            raise self._classify_error(exc) from exc
        return self._map_response(resp)

    # generate_stream 不覆写：初版走基类默认回退（同 openai 家族注释）。


@register_provider
class AnthropicProvider(AnthropicMessagesProvider):
    """Anthropic 官方端点内置 adapter（``name="anthropic"``）。

    .. rubric:: 功能介绍

    Anthropic 官方 API 的实现；随框架发布、进程级注册。

    .. rubric:: 设计动机

    手动前缀缓存（``cache_control``）的标杆实现：``PromptBlock.cache``
    三值语义主要为本 adapter 服务（静态块标记缓存、动态块不标记）。

    .. rubric:: 使用示例

    .. code-block:: yaml

        anthropic:
          adapter: anthropic
          api_key: "{{env.ANTHROPIC_API_KEY}}"

    .. rubric:: 行为规约

    - 期待行为：按基类规约设置 ``cache_control``；凭证取 ``api_key``。
    - 非行为：不管理 ``cachedContent`` 式显式缓存对象生命周期（那是
      Gemini 式机制，不属于本格式家族）。

    .. rubric:: 调用关系（审计）

    - 被调：``register_provider``（进程级注册，import 期，随框架发布）
    - 实例化方：``flowing.runtime.Runtime`` 懒创建链（providers.yaml
      条目 ``adapter: anthropic`` 首次 ``get`` 时，一条目一实例）

    .. seealso::

        :class:`AnthropicMessagesProvider` 格式实现来源。
    """

    name: ClassVar[str] = "anthropic"
    default_base_url: ClassVar[str | None] = "https://api.anthropic.com"


@register_provider
class BedrockProvider(AnthropicMessagesProvider):
    """AWS Bedrock 上的 Anthropic 模型 adapter（``name="bedrock"``）。

    .. rubric:: 功能介绍

    经 AWS Bedrock 调用 Anthropic Messages 格式的实现；差异集中在
    凭证与传输层（AWS 签名），消息格式复用基类。

    .. rubric:: 设计动机

    「同格式、不同凭证来源」是覆写 :meth:`Provider.get_credential` 的
    典型场景——AWS 凭证链（环境变量 / 实例元数据 / session
    token）属于子类覆写点，不进框架核心。初版仅约定该覆写点存在；
    完整 AWS credential chain 初版不实现。

    .. rubric:: 使用示例

    .. code-block:: yaml

        bedrock:
          adapter: bedrock
          aws_region: us-east-1        # adapter 自读字段

    .. rubric:: 行为规约

    - 期待行为：凭证优先级为 config 显式字段 > AWS 默认链；
      region 等字段从 :class:`ProviderConfig` 自读。
    - 安全边界：AWS 凭证同样遵守「不进消息 / ``_provided`` / 落盘」。

    .. rubric:: 调用关系（审计）

    - 被调：``register_provider``（进程级注册，import 期，随框架发布）
    - 实例化方：``flowing.runtime.Runtime`` 懒创建链（providers.yaml
      条目 ``adapter: bedrock`` 首次 ``get`` 时，一条目一实例）

    .. seealso::

        :meth:`Provider.get_credential` 凭证覆写点。
    """

    name: ClassVar[str] = "bedrock"

    def get_credential(self) -> str | None:
        """凭证覆写点约定（初版仅落实本覆写点，AWS 凭证链不实现）。

        优先级：config 显式 ``aws_session_token`` > 基类 ``api_key``。
        完整 AWS credential chain（环境变量 / 实例元数据）与 SigV4 签名
        传输属后续版本；当前 ``_post`` 继承基类 HTTP 形态。
        """
        return self.config.get("aws_session_token") or super().get_credential()
