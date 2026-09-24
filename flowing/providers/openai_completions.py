"""``flowing.providers.openai_completions`` —— OpenAI Completions 格式家族。

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

    :mod:`flowing.providers.anthropic_messages` 另一格式家族。
    :mod:`flowing.providers.openai_responses` Responses 格式家族。
    :class:`flowing.providers.Provider` 抽象契约。
"""

from __future__ import annotations

from typing import Any, ClassVar

import json
import ssl

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
    ImageBlock,
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
    ProviderDelta,
    ProviderResponse,
    Usage,
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
    - 通用透传：models.yaml 条目中的 ``extra_body`` 字段（进
      ``ModelConfig._extra``，不参与 Parsable 求值）为 dict 时原样
      合入请求体，承载厂商私有参数；非 dict 抛
      :class:`flowing.errors.InvalidRequestError`。合入顺序：基类
      固定字段 → ``extra_body`` → adapter 专有字段，同名键后者覆盖
      前者。
    - 响应映射：响应含 ``tool_calls`` → ``finish=False``，其余 →
      ``finish=True``；原始 ``finish_reason`` 保留在
      ``provider_data["stop_reason"]``。
    - Usage 归一：``input = prompt_tokens``、
      ``fresh_input = prompt_tokens - cached_tokens``、
      ``cache_read = cached_tokens``、``cache_write = 0``、
      ``output = completion_tokens``、``total_tokens = input + output``；
      原始用量字段全量保留在 ``Usage.raw``。
    - 不做厂商探测（如按 ``base_url`` 猜测能力）——厂商差异属于子类覆写。
    - 前缀缓存由 OpenAI 服务端自动处理，adapter 不发送任何缓存标记；
      ``PromptBlock.cache`` 对本格式只是字节稳定性提示，不产生请求级
      效果。
    - 多模态输入仅将 ``ImageBlock`` 编码为 ``image_url``；其他媒体类型
      在本地抛出 :class:`flowing.errors.InvalidRequestError`，避免错误地
      作为图片提交。
    - 子类可覆写 :meth:`_additional_headers` 添加厂商请求头，并覆写
      :meth:`_provider_data_from_chunk` 收集流式响应元数据。

    .. seealso::

        :class:`flowing.providers.Provider` 抽象契约。
        :class:`flowing.providers.anthropic_messages.AnthropicMessagesProvider`
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
                f"provider entry is missing base_url (adapter {self.name!r} has no official default endpoint)")
        headers = {"Content-Type": "application/json"}
        credential = self.get_credential()   # 每次发起请求前读取（凭证唯一入口）
        if credential:
            headers["Authorization"] = f"Bearer {credential}"
        headers.update(self._additional_headers())
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(120.0)) as client:
                resp = await client.post(
                    f"{str(base_url).rstrip('/')}{path}", json=body, headers=headers)
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError(f"provider request timed out: {exc}") from exc
        except ssl.SSLError as exc:
            # httpcore/anyio 某些 TLS 错误不会包装成 httpx.TransportError；
            # 仍属于响应未完成的网络层故障，应归一为可重试的 NetworkError。
            raise NetworkError(f"provider network failure: {exc}") from exc
        except httpx.TransportError as exc:
            raise NetworkError(f"provider network failure: {exc}") from exc
        if resp.status_code >= 400:
            try:
                err_body: Any = resp.json()
            except Exception:
                err_body = resp.text
            raise _HttpResponseError(resp.status_code, err_body, dict(resp.headers))
        return resp.json()

    def _additional_headers(self) -> dict[str, str]:
        """返回可合并到普通 POST 与 SSE 请求的厂商请求头。

        基类默认空映射，不改变既有 adapter 的请求头；子类返回值在
        ``Authorization`` 等基础头之后合并。
        """
        return {}

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
        extra_body = model._extra.get("extra_body")
        if extra_body is not None:
            if not isinstance(extra_body, dict):
                raise InvalidRequestError(
                    f"models.yaml extra_body must be a mapping, "
                    f"got {type(extra_body).__name__}")
            body.update(extra_body)
        return body

    def _map_tool(self, definition) -> dict:
        """白名单组装：只取 name/description/parameters 三已知字段。"""
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
        """映射 user 内容；图片生成 ``image_url``，不支持媒体本地报错。"""
        text, media = self._map_content_blocks(msg)
        if not media:
            return text
        parts: list[dict[str, Any]] = [{"type": "text", "text": text}]
        for m in media:
            parts.append(self._image_part(m))
        return parts

    def _image_part(self, media: MediaBlock) -> dict[str, Any]:
        """把静态图像编码为 OpenAI ``image_url`` part；拒绝其他媒体类型。"""
        if not isinstance(media, ImageBlock):
            raise InvalidRequestError(
                f"openai_completions does not support {media.type!r} media input")
        mime = media.mime_type or "application/octet-stream"
        return {"type": "image_url", "image_url": {
            "url": f"data:{mime};base64,{media.data}"}}

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
                 "text": "[The tool results above include the following media content]"},
                *[self._image_part(m) for m in media],
            ]}
            return [tool_msg, follow_up]
        if msg.kind is MessageKind.SYSTEM:
            text, _ = self._map_content_blocks(msg)
            return [{"role": "system", "content": text}]
        # USER / EVENT / PEER / SUBAGENT → user
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
                    f"tool_call arguments are not valid JSON: {exc}") from exc
            blocks.append(ToolCallBlock(
                id=tc["id"], name=tc["function"]["name"], args=args))
        msg = Message(kind=MessageKind.PROVIDER, content=blocks)
        raw_usage = resp.get("usage")
        if raw_usage:   # usage 附着契约：唯一权威落点是 message.usage
            msg.usage = self._usage_from_raw(raw_usage)
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

    def _usage_from_raw(self, raw_usage: dict) -> "Usage | None":
        """原始 usage dict → 归一 :class:`Usage`（流 / 非流共用，口径一致）。

        cache 读取按厂商方言取值：OpenAI 系 ``prompt_tokens_details.cached_tokens``，
        无则回退 DeepSeek 的 ``prompt_cache_hit_tokens`` 与 Kimi 顶层
        ``cached_tokens``。统计口径与非流式原实现一致：``input`` 为含
        cache 读的毛输入，``fresh_input = input - cache_read``。
        """
        if not raw_usage:
            return None
        details = raw_usage.get("prompt_tokens_details") or {}
        cached = details.get("cached_tokens")
        if cached is None:
            cached = raw_usage.get("prompt_cache_hit_tokens")   # DeepSeek 顶层字段
        if cached is None:
            cached = raw_usage.get("cached_tokens")   # Kimi 顶层字段
        cached = cached or 0
        prompt = raw_usage.get("prompt_tokens", 0)
        completion = raw_usage.get("completion_tokens", 0)
        return Usage(
            input=prompt,
            fresh_input=prompt - cached,   # OpenAI 系：prompt_tokens 含 cache 读
            output=completion,
            cache_read=cached,
            cache_write=0,
            reasoning=(raw_usage.get("completion_tokens_details") or {}).get(
                "reasoning_tokens", 0),
            total_tokens=raw_usage.get("total_tokens", prompt + completion),
            raw=dict(raw_usage),
        )

    # ── 流式（真 SSE）：正文 / 思考 / 工具调用逐 delta ───────────────────

    def _stream_headers(self) -> dict[str, str]:
        """流式请求头（多出 ``Accept: text/event-stream``）。"""
        headers = {"Content-Type": "application/json",
                   "Accept": "text/event-stream"}
        credential = self.get_credential()   # 凭证唯一入口，每次读取
        if credential:
            headers["Authorization"] = f"Bearer {credential}"
        headers.update(self._additional_headers())
        return headers

    def _provider_data_from_chunk(self, chunk: dict[str, Any]) -> dict[str, Any]:
        """提取单个 JSON SSE chunk 的厂商元数据；基类默认返回空映射。

        ``generate_stream`` 在解析每个有效 JSON chunk 后、检查 choices
        前调用，因此无 choices 的 usage / metadata chunk 也可被子类读取。
        """
        return {}

    def _parse_tool_args(self, raw: str) -> dict:
        """流式累积的工具参数 → 对象；JSON 容错（坏转义/残缺 → 尽力修复）。

        与 pi-ai ``parseStreamingJson`` 同思路：先严格解析，失败则做浅层
        修复（剥离尾随逗号 / 补闭合），仍失败回退 ``{}`` 不抛异常——流式
        下工具参数可能被 finish 截断，不应让回合崩溃。非流式路径
        (``_map_response``) 保持严格（参数残缺属上游缺陷，明确报错）。
        """
        s = raw.strip()
        if not s:
            return {}
        try:
            parsed = json.loads(s)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            pass
        repaired = s
        if not repaired.endswith("}"):
            # 补闭合：数一下未配对的开括号/方括号（跳过字符串字面量内）
            depth = 0
            in_str = False
            esc = False
            for ch in repaired:
                if in_str:
                    if esc:
                        esc = False
                    elif ch == "\\":
                        esc = True
                    elif ch == '"':
                        in_str = False
                    continue
                if ch == '"':
                    in_str = True
                elif ch in "{[":
                    depth += 1
                elif ch in "}]":
                    depth -= 1
            repaired += "}" * max(depth, 0)
        # 容错：坏转义（如裸换行在字符串里）走 partial 解析前先剥掉非法控制符
        try:
            parsed = json.loads(repaired)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            pass
        # 终极兜底：不抛，返回 {}（宁可空参数也不崩回合）
        return {}

    async def generate_stream(
        self, context: Context, model: ModelConfig
    ) -> Any:
        """真 SSE 流式覆写：逐 delta 产出正文 / 思考 / 工具调用。

        替代基类“一次 generate() 包成单条 text delta”的回退——OpenAI /
        DeepSeek 家族的 chat/completions ``stream=true`` 响应在此逐 chunk
        解析。chunk 形态（``data: <json>``，``[DONE]`` 收尾）：

        - 思考：``choices[0].delta.<方言>``（DeepSeek ``reasoning_content``），
          按 :attr:`_REASONING_KEYS` 顺序首个非空者胜出，逐段产出
          ``kind="thinking"`` delta；记住方言供出站同 key 回写。
        - 正文：``delta.content`` 非空 → ``kind="text"`` delta。
        - 工具：``delta.tool_calls[]`` 按流式 ``index``（缺省回退 ``id``）
          累积，首现的 ``id``/``name`` 生效、``function.arguments`` 逐段
          拼接；流结束后整段解析为参数对象，以携带完整
          :class:`ToolCallBlock` 的 ``block`` delta 产出。
        - 用量 / finish：请求带 ``stream_options.include_usage``；任一 chunk
          顶层 ``usage`` 覆盖末帧用量（可能出现在空 ``choices`` 的独立
          chunk 或 DeepSeek 的 finish chunk）；``finish_reason`` 经末帧
          ``provider_data["stop_reason"]`` 透出（completed 结局的
          finish_reason 来源，见 :meth:`flowing.agent.Agent.provider_gen`）。

        块 content_index 按“首现顺序”动态分配（pi 同款），与文本分块
        单一化一致——同一逻辑块的所有 delta 共享同一 index，agent 累积时
        依此归位。非 2xx / chunk 内 ``error`` 按 :meth:`_classify_error`
        归类上抛；超时 / 传输层失败同 :meth:`generate`。
        """
        import httpx

        base_url = self.config.get("base_url") or self.default_base_url
        if not base_url:
            raise FlowingError(
                f"provider entry is missing base_url (adapter {self.name!r} has no official default endpoint)")
        url = f"{str(base_url).rstrip('/')}/chat/completions"
        body = self._build_request(context, model)
        body["stream"] = True
        body["stream_options"] = {"include_usage": True}

        cidx = {"next": 0}
        think_idx = {"v": None}     # 思考块 content_index
        text_idx = {"v": None}      # 正文块 content_index
        tools: dict[object, dict] = {}   # stream index/id -> {idx,id,name,args}
        finish_reason = None
        last_usage: dict | None = None
        stream_provider_data: dict[str, Any] = {}

        async def _alloc() -> int:
            i = cidx["next"]
            cidx["next"] += 1
            return i

        async def _finalize_tools():
            # 流结束后按分配序把工具块作为 block delta 产出（参数整段解析）
            for key in sorted(tools, key=lambda k: tools[k]["idx"]):
                slot = tools[key]
                if slot["id"] is None or slot["name"] is None:
                    continue   # 残缺工具调用（缺 id/name）不产块
                block = ToolCallBlock(
                    id=slot["id"], name=slot["name"],
                    args=self._parse_tool_args(slot["args"]))
                yield ProviderDelta(kind="tool_use", text="",
                                    content_index=slot["idx"], block=block)

        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(120.0)) as client:
                async with client.stream("POST", url, json=body,
                                         headers=self._stream_headers()) as resp:
                    if resp.status_code >= 400:
                        try:
                            err_bytes = await resp.aread()
                        except Exception:
                            err_bytes = b""
                        try:
                            err_body = json.loads(err_bytes or b"{}")
                        except Exception:
                            err_body = (err_bytes or b"").decode("utf-8", "replace")
                        raise self._classify_error(
                            _HttpResponseError(resp.status_code, err_body,
                                               dict(resp.headers)))
                    async for raw_line in resp.aiter_lines():
                        line = raw_line.strip()
                        if not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if data == "[DONE]":
                            break
                        try:
                            chunk = json.loads(data)
                        except json.JSONDecodeError:
                            continue
                        if chunk.get("error"):
                            raise self._classify_error(_HttpResponseError(
                                chunk.get("status", 400),
                                {"error": chunk["error"]}, {}))
                        stream_provider_data.update(
                            self._provider_data_from_chunk(chunk))
                        if chunk.get("usage") is not None:
                            last_usage = chunk["usage"]
                        choices = chunk.get("choices") or []
                        if not choices:
                            continue   # 独立 usage chunk（choices 为空）
                        choice = choices[0]
                        if choice.get("finish_reason") is not None:
                            finish_reason = choice["finish_reason"]
                        delta = choice.get("delta") or {}
                        # 思考（方言首现胜出并记住，供出站回写）
                        for key in self._REASONING_KEYS:
                            rv = delta.get(key)
                            if not rv:
                                continue
                            self._reasoning_dialect = key
                            if think_idx["v"] is None:
                                think_idx["v"] = await _alloc()
                            text = rv if isinstance(rv, str) else json.dumps(
                                rv, ensure_ascii=False)
                            yield ProviderDelta(kind="thinking", text=text,
                                                content_index=think_idx["v"])
                            break
                        content = delta.get("content")
                        if content:
                            if text_idx["v"] is None:
                                text_idx["v"] = await _alloc()
                            yield ProviderDelta(kind="text", text=content,
                                                content_index=text_idx["v"])
                        for tc in delta.get("tool_calls") or []:
                            sidx = tc.get("index")
                            key = sidx if sidx is not None else tc.get("id")
                            if key is None:
                                continue
                            slot = tools.setdefault(
                                key, {"idx": None, "id": None, "name": None, "args": ""})
                            if slot["idx"] is None:
                                slot["idx"] = await _alloc()
                            if slot["id"] is None and tc.get("id"):
                                slot["id"] = tc["id"]
                            fn = tc.get("function") or {}
                            if slot["name"] is None and fn.get("name"):
                                slot["name"] = fn["name"]
                            slot["args"] += fn.get("arguments") or ""
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError(f"provider request timed out: {exc}") from exc
        except ssl.SSLError as exc:
            # 见 _post：部分 TLS 错误会从 httpcore 直接透出。
            raise NetworkError(f"provider network failure: {exc}") from exc
        except httpx.TransportError as exc:
            raise NetworkError(f"provider network failure: {exc}") from exc

        # 流结束：先产工具块（整段参数），再产末帧（usage + finish_reason）
        async for d in _finalize_tools():
            yield d
        stream_provider_data["stop_reason"] = finish_reason
        yield ProviderDelta(
            kind="text", text="", content_index=cidx["next"],   # 未使用 index：末帧只载 usage/finish，不污染已累积块
            usage=self._usage_from_raw(last_usage) if last_usage else None,
            provider_data=stream_provider_data,
        )
