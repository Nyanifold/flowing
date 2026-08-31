"""``flowing.providers.openai`` —— OpenAI Completions 格式家族。

.. rubric:: 功能介绍

框架基类 :class:`OpenAICompletionsProvider` 收拢 OpenAI 兼容 wire
format（chat/completions）的请求/响应映射；内置厂商 adapter（DeepSeek
/ Kimi / Groq / OpenRouter）各为一个子类，只覆写厂商差异（默认端点、
凭证来源、厂商特有字段）。设计要点（显式继承树、禁止 compat flags）
见 :mod:`flowing.providers` 包 docstring。

本格式家族的工具结果映射约定：TOOL 消息的 ``content`` 块直接映射——
消息级 ``tool_call_id`` → tool 消息的 ``tool_call_id`` 位；
``StructBlock`` 恒投影为 ``json.dumps(ensure_ascii=False)`` 文本；
Chat Completions 的 tool 消息为文本-only——消息中的媒体块转移到紧随
tool 消息的合成 user 消息（固定措辞提示）。

响应侧事实：思考内容无官方字段，线上存在四种方言
（``reasoning_content`` / ``reasoning_details`` / ``reasoning`` /
``reasoning_text``），本基类在响应中记住端点实际使用的方言，回放
（多轮历史中的思考块）时以同一 key 写回；``tool_calls`` 的
``function.arguments`` 是 JSON 字符串，解析失败抛
:class:`flowing.errors.InvalidRequestError`。

.. seealso::

    :mod:`flowing.providers.anthropic` 另一格式家族。
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
    """传输层非 2xx 响应的内部载体（归类前的原始事实；不属异常层次）。

    ``_post`` （唯一网络点）在非 2xx 时抛出；``generate()`` 捕获后经
    ``_classify_error`` 归类为 :mod:`flowing.errors` 类型。mock
    transport 测试以抛出本异常模拟各状态码。
    """

    def __init__(self, status_code: int, body: Any = None,
                 headers: dict[str, str] | None = None) -> None:
        super().__init__(f"HTTP {status_code}")
        self.status_code = status_code
        self.body = body
        self.headers = headers or {}




class OpenAICompletionsProvider(Provider):
    """OpenAI Completions 格式的框架基类（``api_format="openai_completions"``）。

    .. rubric:: 功能介绍

    所有 OpenAI 兼容端点 adapter 的基类：实现请求/响应格式映射——把
    :class:`flowing.context.Context` 的三个字段映射为 chat/completions
    请求体（system 消息、messages 数组、tools 声明），把响应映射为
    :class:`ProviderResponse`，并把原始用量归一为 :class:`Usage`
    （``prompt_tokens`` → ``input``，
    ``fresh_input = prompt_tokens - cached_tokens``）。子类只覆写差异：
    默认端点（``default_base_url``）、
    凭证来源、厂商特有字段。

    本类不直接实例化使用——实例化发生在具体厂商子类的懒创建链上
    （一个 providers.yaml 条目一个实例，见 :class:`Provider`）。

    .. rubric:: 使用示例

    .. code-block:: python

        from flowing.providers import OpenAICompletionsProvider, register_provider

        @register_provider
        class MyEndpointProvider(OpenAICompletionsProvider):
            name = "my-endpoint"
            default_base_url = "https://llm.example.com/v1"

    .. rubric:: 行为要点

    - 请求映射：``Context.system_prompt`` 逐段成为 system 消息；
      ``Context.messages`` 逐条按 kind 映射 role；``Context.tools`` 经
      白名单组装为 function 声明；``model.max_output_tokens`` 非
      ``None`` 时写入 ``max_tokens``。
    - 响应映射：响应含 ``tool_calls`` → ``finish=False``，其余 →
      ``finish=True``；原始 ``finish_reason`` 保留在
      ``provider_data["stop_reason"]``。
    - Usage 归一：``input = prompt_tokens``、
      ``fresh_input = prompt_tokens - cached_tokens``、
      ``cache_read = cached_tokens``、``cache_write = 0``、
      ``output = completion_tokens``、``total_tokens = input + output``；
      原始用量字段全量保留在 ``Usage.raw``。
    - 不做厂商探测（如按 base_url 猜测能力）——厂商差异属于子类覆写。
    - 前缀缓存由 OpenAI 服务端自动处理，adapter 不发送任何缓存标记；
      ``PromptBlock.cache`` 对本格式只是字节稳定性提示，不产生请求级
      效果。

    .. seealso::

        :class:`flowing.providers.Provider` 抽象契约。
        :class:`flowing.providers.anthropic.AnthropicMessagesProvider`
            另一格式家族基类。
    """

    api_format: ClassVar[str] = "openai_completions"
    known_model_fields: ClassVar[frozenset[str]] = frozenset()
    default_base_url: ClassVar[str | None] = None
    """子类覆写：厂商官方端点；条目配 ``base_url`` 时以条目为准（代理场景）。"""

    _reasoning_dialect: str | None
    """入站扫描记住的思考方言 key（``reasoning_content`` / ``reasoning`` /
    ``reasoning_text`` / ``reasoning_details``），历史回放时同方言回写。"""

    # ── 网络点（唯一）──────────────────────────────────────────────────

    async def _post(self, path: str, body: dict) -> dict:
        """发起一次 POST 并返回解析后的 JSON（唯一网络点，子类/测试可覆写）。

        行为边界：非 2xx → :class:`_HttpResponseError` （归类在调用方）；
        超时 → :class:`ProviderTimeoutError`；传输层失败 →
        :class:`NetworkError`。每次调用新建 ``httpx.AsyncClient``，
        不跨请求复用连接池。
        """
        import httpx

        base_url = self.config.get("base_url") or self.default_base_url
        if not base_url:
            raise FlowingError(
                f"provider 条目缺少 base_url（adapter {self.name!r} 无官方默认端点）")
        headers = {"Content-Type": "application/json"}
        credential = self.get_credential()   # 每次发起请求前读取（凭证唯一入口）
        if credential:
            headers["Authorization"] = f"Bearer {credential}"
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

    # ── 请求映射（Context → chat/completions 请求体）────────────────────

    def _build_request(self, context: Context, model: ModelConfig) -> dict:
        """把 ``Context`` 三字段与模型规格映射为 chat/completions 请求体。"""
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": seg.content}
            for seg in context.system_prompt
        ]
        for msg in context.messages:
            messages.extend(self._map_message(msg))
        body: dict[str, Any] = {"model": model.model, "messages": messages}
        if context.tools:
            body["tools"] = [self._map_tool(d) for d in context.tools]
        if model.max_output_tokens is not None:
            body["max_tokens"] = model.max_output_tokens
        return body

    def _map_tool(self, definition) -> dict:
        """白名单组装：只取 name/description/parameters 三已知字段。
        """
        params = definition.params_schema or {}
        return {
            "type": "function",
            "function": {
                "name": definition.name,
                "description": definition.description,
                "parameters": {
                    "type": "object",
                    "properties": params,
                    "required": [k for k, v in params.items() if "default" not in v],
                },
            },
        }

    def _map_content_blocks(self, msg: Message) -> tuple[str, list[MediaBlock]]:
        """纯内容块 → 拼接文本 + 媒体块列表（TOOL 消息文本-only 的预处理）。"""
        parts: list[str] = []
        media: list[MediaBlock] = []
        for block in msg.content:
            if isinstance(block, MediaBlock):
                media.append(block)
            elif isinstance(block, StructBlock):
                # StructBlock 恒投影为 json.dumps 文本（所有 adapter 统一）
                parts.append(json.dumps(block.data, ensure_ascii=False))
            elif isinstance(block, TextBlock):
                parts.append(block.text)
        return "".join(parts), media

    def _map_user_content(self, msg: Message) -> Any:
        """user 系消息的内容位：有媒体 → parts 形态（image_url），否则纯文本。"""
        text, media = self._map_content_blocks(msg)
        if not media:
            return text
        parts: list[dict[str, Any]] = [{"type": "text", "text": text}]
        for m in media:
            mime = m.mime_type or "application/octet-stream"
            parts.append({"type": "image_url", "image_url": {
                "url": f"data:{mime};base64,{m.data}"}})
        return parts

    def _map_message(self, msg: Message) -> list[dict[str, Any]]:
        """单条消息 → chat/completions messages 数组元素（一对多：媒体转移）。"""
        if msg.kind is MessageKind.PROVIDER:
            out: dict[str, Any] = {"role": "assistant"}
            text_parts: list[str] = []
            tool_calls: list[dict[str, Any]] = []
            thinking_texts: list[str] = []
            for block in msg.content:
                if isinstance(block, ToolCallBlock):
                    tool_calls.append({
                        "id": block.id, "type": "function",
                        "function": {"name": block.name,
                                     "arguments": json.dumps(block.args, ensure_ascii=False)},
                    })
                elif isinstance(block, ThinkingBlock):
                    thinking_texts.append(block.thinking)
                elif isinstance(block, StructBlock):
                    text_parts.append(json.dumps(block.data, ensure_ascii=False))
                elif isinstance(block, TextBlock):
                    text_parts.append(block.text)
            out["content"] = "".join(text_parts) or None
            if tool_calls:
                out["tool_calls"] = tool_calls
            if thinking_texts and self._reasoning_dialect:
                # 出站同方言回写：端点实际说的方言 key 原样写回
                out[self._reasoning_dialect] = "".join(thinking_texts)
            return [out]
        if msg.kind is MessageKind.TOOL:
            # Chat Completions 的 tool 消息为文本-only → 媒体转移：媒体块
            # 攒入紧随 tool 消息的合成 user 消息（固定措辞提示）
            text, media = self._map_content_blocks(msg)
            tool_msg = {"role": "tool", "tool_call_id": msg.tool_call_id,
                        "content": text}
            if not media:
                return [tool_msg]
            follow_up = {"role": "user", "content": [
                {"type": "text",
                 "text": "[上述工具结果包含以下媒体内容]"},
                *[{"type": "image_url", "image_url": {
                    "url": f"data:{m.mime_type or 'application/octet-stream'};base64,{m.data}"}}
                  for m in media],
            ]}
            return [tool_msg, follow_up]
        if msg.kind is MessageKind.SYSTEM:
            text, _ = self._map_content_blocks(msg)
            return [{"role": "system", "content": text}]
        # USER / EVENT / PEER / PLUGIN / SUBAGENT → user
        return [{"role": "user", "content": self._map_user_content(msg)}]

    # ── 响应映射（chat/completions 响应 → ProviderResponse）──────────────

    _REASONING_KEYS: ClassVar[tuple[str, ...]] = (
        "reasoning_content", "reasoning_details", "reasoning", "reasoning_text")
    """思考无官方字段的线上方言四种——入站扫描顺序（先见者胜出并记住）。"""

    def _map_response(self, resp: dict) -> ProviderResponse:
        """录制/真实响应 dict → ``ProviderResponse`` （usage 附着到消息）。"""
        choice = resp["choices"][0]
        raw_msg = choice.get("message") or {}
        blocks: list = []
        # 思考方言入站扫描（记住端点实际说的方言，回放时同 key 写回）
        for key in self._REASONING_KEYS:
            value = raw_msg.get(key)
            if value:
                self._reasoning_dialect = key
                text = value if isinstance(value, str) else json.dumps(
                    value, ensure_ascii=False)
                blocks.append(ThinkingBlock(thinking=text))
                break
        if raw_msg.get("content"):
            blocks.append(TextBlock(text=raw_msg["content"]))
        has_tool_call = False
        for tc in raw_msg.get("tool_calls") or []:
            has_tool_call = True
            try:
                args = json.loads(tc["function"].get("arguments") or "{}")
            except json.JSONDecodeError as exc:
                raise InvalidRequestError(
                    f"tool_call arguments 非法 JSON：{exc}") from exc
            blocks.append(ToolCallBlock(
                id=tc["id"], name=tc["function"]["name"], args=args))
        msg = Message(kind=MessageKind.PROVIDER, content=blocks)
        raw_usage = resp.get("usage")
        if raw_usage:   # usage 附着契约：唯一权威落点是 message.usage
            cached = (raw_usage.get("prompt_tokens_details") or {}).get(
                "cached_tokens", 0)
            prompt = raw_usage.get("prompt_tokens", 0)
            completion = raw_usage.get("completion_tokens", 0)
            msg.usage = Usage(
                input=prompt,
                fresh_input=prompt - cached,   # OpenAI 系：prompt_tokens 含 cache 读
                output=completion,
                cache_read=cached,
                cache_write=0,
                reasoning=(raw_usage.get("completion_tokens_details") or {}).get(
                    "reasoning_tokens", 0),
                total_tokens=prompt + completion,   # 恒等式回填
                raw=dict(raw_usage),
            )
        finish_reason = choice.get("finish_reason")
        return ProviderResponse(
            message=msg,
            model=resp.get("model", ""),
            finish=not has_tool_call,   # 默认准则：有 tool_call → False
            provider_data={"stop_reason": finish_reason},
        )

    # ── 错误归类（_HttpResponseError → flowing.errors 类型）──────────────

    def _classify_error(self, exc: "_HttpResponseError") -> ProviderError:
        """HTTP 状态码 → 异常分类（分类表见包 docstring；不重试不兜底）。"""
        status = exc.status_code
        body = exc.body if isinstance(exc.body, dict) else {}
        message = (body.get("error") or {}).get("message") or str(exc.body or exc)
        retry_after = exc.headers.get("retry-after")
        kwargs: dict[str, Any] = {
            "status_code": status,
            "retry_after": float(retry_after) if retry_after else None,
        }
        if status in (401, 403):
            return AuthenticationError(message, **kwargs)
        if status == 429:
            return RateLimitedError(message, **kwargs)
        if status == 413:
            return RequestTooLargeError(message, **kwargs)
        if status == 400:
            # 上下文溢出 / 内容策略是 400 的特化：按已知错误文案模式识别
            lowered = message.lower()
            if "context length" in lowered or "context_length" in lowered \
                    or "maximum context" in lowered:
                return ContextLengthError(message, **kwargs)
            if "content_policy" in lowered or "content policy" in lowered:
                return ContentPolicyError(message, **kwargs)
            return InvalidRequestError(message, **kwargs)
        if status == 422:
            return InvalidRequestError(message, **kwargs)
        if status >= 500:
            return ServerError(message, **kwargs)
        return ProviderError(message, **kwargs)

    # ── 契约方法 ─────────────────────────────────────────────────────────

    def __init__(self, config) -> None:
        super().__init__(config)
        self._reasoning_dialect = None   # 入站扫描前未知；不建连接（零启动成本）

    async def generate(
        self, context: Context, model: ModelConfig
    ) -> ProviderResponse:
        """非流式单次生成（映射 / 错误归类见各 ``_*`` 方法）。

        .. rubric:: 行为要点

        - 把组装好的上下文映射为 chat/completions 请求体并 POST 到
          ``/chat/completions``；非 2xx 响应经错误归类映射为
          :mod:`flowing.errors` 中的明确类型后上抛（不重试、不兜底）。
        - 2xx 响应映射为 :class:`ProviderResponse`，用量附着到
          ``message.usage``。

        .. seealso:: 同 :meth:`flowing.providers.Provider.generate` 契约。
        """
        body = self._build_request(context, model)
        try:
            resp = await self._post("/chat/completions", body)
        except _HttpResponseError as exc:
            raise self._classify_error(exc) from exc
        return self._map_response(resp)

    # generate_stream 不覆写：走基类默认回退（generate() 结果包成
    # delta——契约合法，见 Provider.generate_stream「默认实现行为」）；
    # 真 SSE 流式不在本基类提供，不影响 Turn 循环可见语义。


@register_provider
class DeepSeekProvider(OpenAICompletionsProvider):
    """DeepSeek 内置 adapter（``name="deepseek"``）。

    .. rubric:: 功能介绍

    DeepSeek 官方端点的 OpenAI 兼容实现；随框架发布、import 期经
    :func:`register_provider` 进程级注册。providers.yaml 条目把
    ``adapter`` 字段设为 ``"deepseek"`` 即可选用。

    .. rubric:: 使用示例

    .. code-block:: yaml

        # providers.yaml
        deepseek-personal:
          adapter: deepseek
          api_key: "{{env.DEEPSEEK_API_KEY}}"

    .. rubric:: 行为要点

    - 默认端点 ``https://api.deepseek.com``；条目配 ``base_url`` 时以
      条目为准（代理场景）。
    - 凭证取条目 ``api_key``；DeepSeek 的前缀缓存由服务端自动处理
      （要求前缀字节稳定），adapter 不发送缓存标记；缓存命中统计
      （如 ``prompt_cache_hit_tokens``）保留在 ``Usage.raw``。

    .. seealso::

        :class:`OpenAICompletionsProvider` 格式实现来源。
    """

    name: ClassVar[str] = "deepseek"
    known_model_fields: ClassVar[frozenset[str]] = frozenset({"thinking_budget"})
    default_base_url: ClassVar[str | None] = "https://api.deepseek.com"


@register_provider
class KimiProvider(OpenAICompletionsProvider):
    """Kimi（Moonshot）内置 adapter（``name="kimi"``）。

    .. rubric:: 功能介绍

    Kimi 官方端点的 OpenAI 兼容实现；随框架发布、import 期经
    :func:`register_provider` 进程级注册。providers.yaml 条目把
    ``adapter`` 字段设为 ``"kimi"`` 即可选用。

    .. rubric:: 使用示例

    .. code-block:: yaml

        # providers.yaml
        kimi:
          adapter: kimi
          api_key: "{{env.MOONSHOT_API_KEY}}"

    .. rubric:: 行为要点

    - 默认端点 ``https://api.moonshot.cn/v1``；条目配 ``base_url`` 时
      以条目为准（代理场景）。
    - 格式映射与 Usage 归一继承自 :class:`OpenAICompletionsProvider`。

    .. seealso::

        :class:`OpenAICompletionsProvider` 格式实现来源。
    """

    name: ClassVar[str] = "kimi"
    default_base_url: ClassVar[str | None] = "https://api.moonshot.cn/v1"


@register_provider
class GroqProvider(OpenAICompletionsProvider):
    """Groq 内置 adapter（``name="groq"``）。

    .. rubric:: 功能介绍

    Groq 端点的 OpenAI 兼容实现；随框架发布、import 期经
    :func:`register_provider` 进程级注册。providers.yaml 条目把
    ``adapter`` 字段设为 ``"groq"`` 即可选用。

    .. rubric:: 使用示例

    .. code-block:: yaml

        # providers.yaml
        groq:
          adapter: groq
          api_key: "{{env.GROQ_API_KEY}}"

    .. rubric:: 行为要点

    - 默认端点 ``https://api.groq.com/openai/v1``。
    - 不做能力校验：模型是否存在于 Groq 由 API 调用时报错。

    .. seealso::

        :class:`OpenAICompletionsProvider` 格式实现来源。
    """

    name: ClassVar[str] = "groq"
    default_base_url: ClassVar[str | None] = "https://api.groq.com/openai/v1"


@register_provider
class OpenRouterProvider(OpenAICompletionsProvider):
    """OpenRouter 内置 adapter（``name="openrouter"``）。

    .. rubric:: 功能介绍

    OpenRouter 聚合端点的 OpenAI 兼容实现；随框架发布、import 期经
    :func:`register_provider` 进程级注册。providers.yaml 条目把
    ``adapter`` 字段设为 ``"openrouter"`` 即可选用。

    .. rubric:: 使用示例

    .. code-block:: yaml

        # providers.yaml
        openrouter:
          adapter: openrouter
          api_key: "{{env.OPENROUTER_API_KEY}}"

    .. rubric:: 行为要点

    - 默认端点 ``https://openrouter.ai/api/v1``。
    - 请求侧模型 ID 与响应侧模型 ID 可以不同（如 ``model="auto"`` 时
      实际由 OpenRouter 路由到具体模型）：``ProviderResponse.model``
      填实际响应的模型 ID；路由信息（如 provider 路由选择）保留在
      ``provider_data``。
    - 模型路由是 OpenRouter 服务端行为，本 adapter 不做本地路由 /
      fallback 链。

    .. seealso::

        :class:`OpenAICompletionsProvider` 格式实现来源。
        :attr:`flowing.providers.ProviderResponse.model`
            响应侧模型 ID 语义。
    """

    name: ClassVar[str] = "openrouter"
    default_base_url: ClassVar[str | None] = "https://openrouter.ai/api/v1"
