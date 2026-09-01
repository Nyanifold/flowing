"""``flowing.providers.anthropic`` —— Anthropic Messages 格式家族。

.. rubric:: 功能介绍

框架基类 :class:`AnthropicMessagesProvider` 收拢 Anthropic Messages API
的请求/响应映射（system 数组、content blocks、tool_use / tool_result、
手动前缀缓存 ``cache_control``）；内置厂商 adapter（Anthropic 官方端点、
AWS Bedrock）各为一个子类。设计要点见 :mod:`flowing.providers` 包
docstring。

本格式家族的工具结果映射约定：TOOL/EVENT 消息的 ``content`` 块直接
映射进 user 消息内的 ``tool_result`` 块——消息级 ``tool_call_id`` →
``tool_use_id``，``tool_status="error"`` → ``is_error: true``；媒体块
原生内嵌于 ``tool_result.content`` （image / document），无需转移；
``StructBlock`` 恒投影为 ``json.dumps(ensure_ascii=False)`` 文本。
同一连续段的多条 TOOL 消息归并为一条 user 消息的多 ``tool_result``
块（Anthropic 对并行 tool_use 的配对要求），遇到非 TOOL 消息即断段。

响应侧事实：``thinking`` 块的 ``signature`` 为不透明签名字符串，多轮
回放必须原样带回；``tool_use`` 的 ``input`` 为已解析 JSON 对象。

.. seealso::

    :mod:`flowing.providers.openai` 另一格式家族。
    :class:`flowing.providers.Provider` 抽象契约。
"""

from __future__ import annotations

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
    content blocks、tool_use / tool_result 的格式映射与 Usage 映射。
    子类只覆写差异：默认端点、凭证来源、厂商特有字段。

    本类不直接实例化使用——实例化发生在具体厂商子类的懒创建链上
    （一个 providers.yaml 条目一个实例，见 :class:`Provider`）。

    .. rubric:: 使用示例

    .. code-block:: python

        from flowing.providers import AnthropicMessagesProvider, register_provider

        @register_provider
        class MyAnthropicProvider(AnthropicMessagesProvider):
            name = "my-anthropic"
            default_base_url = "https://my-proxy.example.com"

    .. rubric:: 行为要点

    - 手动前缀缓存：仅 ``cache="static"`` 的 PromptSegment 设置
      ``cache_control: {"type": "ephemeral"}``；``cache="dynamic"`` 与
      ``cache="session"`` 均不标记（不标记 = 不缓存）。
    - 响应映射：``stop_reason == "tool_use"`` → ``finish=False``，其余
      → ``finish=True``；原始 ``stop_reason`` 保留在
      ``provider_data["stop_reason"]``。
    - Usage 映射：``fresh_input`` / ``output`` 直取原生 ``input_tokens``
      / ``output_tokens`` （Anthropic 原生 input 不含 cache）；
      ``input = fresh_input + cache_read + cache_write`` 按恒等式组装；
      ``cache_creation_input_tokens`` / ``cache_read_input_tokens`` 升入
      一等字段，同时原样保留在 ``Usage.raw``。
    - 并行工具结果的归并：同一连续段的多条 TOOL 消息聚合为一条 user
      消息的多 ``tool_result`` 块（Anthropic 要求同一 assistant 回合的
      并行 tool_use 的全部结果收进紧随的一条 user 消息，逐条各出会被
      API 拒绝）；配对锚按消息顺序保持；遇到非 TOOL 消息即断段，孤立
      单条 TOOL 消息独立映射。

    .. seealso::

        :class:`flowing.providers.openai.OpenAICompletionsProvider`
            对照的格式家族。
        :class:`flowing.context.PromptSegment` ``cache`` 三值语义。
    """

    api_format: ClassVar[str] = "anthropic_messages"
    known_model_fields: ClassVar[frozenset[str]] = frozenset({"thinking_budget"})
    default_base_url: ClassVar[str | None] = None
    """子类覆写：厂商官方端点；条目配 ``base_url`` 时以条目为准。"""

    anthropic_version: ClassVar[str] = "2023-06-01"
    """``anthropic-version`` 请求头值。"""

    # ── 网络点（唯一）──────────────────────────────────────────────────

    async def _post(self, path: str, body: dict) -> dict:
        """发起一次 POST 并返回解析后的 JSON（唯一网络点，子类/测试可覆写）。

        行为边界与 openai 家族同构：非 2xx → :class:`_HttpResponseError`；
        超时 → :class:`ProviderTimeoutError`；传输层失败 →
        :class:`NetworkError`。
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
                # cache="dynamic" 不标记；cache="session" 不映射断点
                # （会话冻结场景的缓存断点语义超出本 adapter 支持范围）
                {"type": "text", "text": seg.content,
                 **({"cache_control": {"type": "ephemeral"}}
                    if seg.cache == "static" else {})}
                for seg in context.system_prompt
            ],
            "messages": self._map_messages(context.messages),
            "max_tokens": model.max_output_tokens or 8192,   # Anthropic 必填
        }
        if context.tools:
            body["tools"] = [self._map_tool(d) for d in context.tools]
        if model.thinking_budget:
            body["thinking"] = {"type": "enabled",
                                "budget_tokens": model.thinking_budget}
        return body

    def _map_tool(self, definition) -> dict:
        """白名单组装：只取 name/description/parameters 三已知字段。"""
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

    def _map_messages(self, messages) -> list[dict[str, Any]]:
        """消息序列 → Anthropic messages 数组（连续 TOOL 段归并为一条 user 消息）。

        Anthropic 要求同一 assistant 回合的并行 tool_use 的全部
        tool_result 收进紧随的一条 user 消息；框架的并行工具批会产生
        连续多条 TOOL 消息，逐条各出 user 消息会被 API 拒绝。本方法把
        同一连续段的 TOOL 消息按序聚合成一条 user 消息的多
        ``tool_result`` 块（配对锚 ``tool_use_id`` 与块的对应按消息
        顺序保持，不打乱）；遇到非 TOOL 消息即断段，孤立单条 TOOL
        消息独立映射。
        """
        out: list[dict[str, Any]] = []
        tool_results: list[dict[str, Any]] = []
        for msg in messages:
            if msg.kind is MessageKind.TOOL:
                tool_results.append(self._map_tool_result(msg))
                continue
            if tool_results:
                out.append({"role": "user", "content": tool_results})
                tool_results = []
            out.extend(self._map_message(msg))
        if tool_results:
            out.append({"role": "user", "content": tool_results})
        return out

    def _map_tool_result(self, msg: Message) -> dict[str, Any]:
        """单条 TOOL 消息 → 一个 ``tool_result`` 块（归并段与孤立形态共用）。

        消息级 ``tool_call_id`` → ``tool_use_id``；``tool_status="error"``
        → ``is_error: true``；空 content 兜底（返回 Task 路径的 pending
        收据等；async gen 路径的 pending 收据带内容——首 yield + 「后台
        任务 ID」块——按正常 tool_result 映射）。
        """
        return {
            "type": "tool_result",
            "tool_use_id": msg.tool_call_id,
            "content": self._map_plain_blocks(msg) or [
                {"type": "text", "text": ""}],
            **({"is_error": True} if msg.tool_status == "error" else {}),
        }

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
            # 孤立单条 TOOL 消息：一条 user 消息含单 tool_result 块；
            # 连续段的归并在 _map_messages 完成（共用 _map_tool_result）
            return [{"role": "user", "content": [self._map_tool_result(msg)]}]
        return [{"role": "user", "content": self._map_plain_blocks(msg)}]

    # ── 响应映射 ─────────────────────────────────────────────────────────

    def _map_response(self, resp: dict) -> ProviderResponse:
        """录制/真实响应 dict → ``ProviderResponse`` （usage 附着到消息）。"""
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
        """非流式单次生成（映射 / 错误归类见各 ``_*`` 方法）。

        .. rubric:: 行为要点

        - 把组装好的上下文映射为 Anthropic Messages 请求体并 POST 到
          ``/v1/messages``；非 2xx 响应经错误归类映射为
          :mod:`flowing.errors` 中的明确类型后上抛（不重试、不兜底）。
        - 2xx 响应映射为 :class:`ProviderResponse`，用量附着到
          ``message.usage``。

        .. seealso:: 同 :meth:`flowing.providers.Provider.generate` 契约。
        """
        body = self._build_request(context, model)
        try:
            resp = await self._post("/v1/messages", body)
        except _HttpResponseError as exc:
            raise self._classify_error(exc) from exc
        return self._map_response(resp)

    # generate_stream 不覆写：走基类默认回退（同 openai 家族注释）。


@register_provider
class AnthropicProvider(AnthropicMessagesProvider):
    """Anthropic 官方端点内置 adapter（``name="anthropic"``）。

    .. rubric:: 功能介绍

    Anthropic 官方 API 的实现；随框架发布、import 期经
    :func:`register_provider` 进程级注册。providers.yaml 条目把
    ``adapter`` 字段设为 ``"anthropic"`` 即可选用。

    .. rubric:: 使用示例

    .. code-block:: yaml

        # providers.yaml
        anthropic:
          adapter: anthropic
          api_key: "{{env.ANTHROPIC_API_KEY}}"

    .. rubric:: 行为要点

    - 默认端点 ``https://api.anthropic.com``；条目配 ``base_url`` 时以
      条目为准（代理场景）。
    - 凭证取条目 ``api_key``；手动前缀缓存按基类规约设置
      （``cache="static"`` 段标记 ``cache_control``）。

    .. seealso::

        :class:`AnthropicMessagesProvider` 格式实现来源。
    """

    name: ClassVar[str] = "anthropic"
    default_base_url: ClassVar[str | None] = "https://api.anthropic.com"


@register_provider
class BedrockProvider(AnthropicMessagesProvider):
    """AWS Bedrock 上的 Anthropic 模型 adapter（``name="bedrock"``）。

    .. rubric:: 功能介绍

    经 AWS Bedrock 调用 Anthropic Messages 格式的实现；消息格式复用
    基类，差异集中在凭证来源。随框架发布、import 期经
    :func:`register_provider` 进程级注册。providers.yaml 条目把
    ``adapter`` 字段设为 ``"bedrock"`` 即可选用。

    .. rubric:: 使用示例

    .. code-block:: yaml

        # providers.yaml
        bedrock:
          adapter: bedrock
          base_url: https://bedrock.example.com   # 本 adapter 无官方默认端点，必填
          aws_session_token: "{{env.AWS_SESSION_TOKEN}}"   # adapter 自读字段

    .. rubric:: 行为要点

    - 本 adapter 无官方默认端点：条目必须提供 ``base_url``。
    - 凭证优先级：config 显式 ``aws_session_token`` > 基类 ``api_key``。
    - 凭证同样遵守「不进消息 / ``_provided`` / 落盘」的安全边界。
    - 当前传输层复用基类的普通 HTTP 形态（未实现 AWS SigV4 签名）。

    .. seealso::

        :meth:`flowing.providers.Provider.get_credential` 凭证覆写点。
        :class:`AnthropicMessagesProvider` 格式实现来源。
    """

    name: ClassVar[str] = "bedrock"

    def get_credential(self) -> str | None:
        """凭证覆写点：config 显式 ``aws_session_token`` 优先，否则回退基类 ``api_key``。

        .. rubric:: 行为要点

        - 优先级：``aws_session_token`` > 基类 ``api_key``。
        - 当前不实现完整 AWS credential chain（环境变量 / 实例元数据）
          与 SigV4 签名传输——``_post`` 复用基类普通 HTTP 形态。
        - 安全边界：返回值禁止写入消息、``_provided``、日志与任何落盘
          文件。
        """
        return self.config.get("aws_session_token") or super().get_credential()
