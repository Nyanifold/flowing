"""``flowing.providers.openai_responses`` —— OpenAI Responses 格式家族。

.. rubric:: 功能介绍

框架基类 :class:`OpenAIResponsesProvider` 收拢 OpenAI Responses API
（``/responses``）的请求/响应映射（input item 序列、扁平 function
声明、reasoning item 回放）；内置厂商 adapter（Kimi moonshot 端点）
各为一个子类，只覆写厂商差异（默认端点、凭证来源、厂商特有字段）。
设计要点（显式继承树、禁止 compat flags）见 :mod:`flowing.providers`
包 docstring。

本格式家族的工具结果映射约定：TOOL 消息的 ``content`` 块映射为顶层
``function_call_output`` item——消息级 ``tool_call_id`` →
``call_id`` 位；纯文本结果为拼接字符串，含媒体块时为
``input_text`` / ``input_image`` 混合的 list 形态（媒体原生内嵌，
无需转移）；``StructBlock`` 恒投影为 ``json.dumps(ensure_ascii=False)``
文本。

响应侧事实：assistant 输出是顶层 item 序列（reasoning / message /
function_call），回放时 PROVIDER 消息同样摊平为顶层 item 序列；
``store=False`` 下服务端不保留会话状态，历史全量重放，reasoning 项
经 ``include=["reasoning.encrypted_content"]`` 带加密内容回传、以
``ThinkingBlock.signature`` 承载其 JSON 原样回放；assistant 文本回放
为 ``output_text`` message item，id 取请求内自增计数器
（``msg_{n}``）；``function_call`` 的 ``arguments`` 是 JSON 字符串，
解析失败抛 :class:`flowing.errors.InvalidRequestError`。

.. seealso::

    :mod:`flowing.providers.openai_completions` /
    :mod:`flowing.providers.anthropic_messages`
        另两个格式家族。
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




class OpenAIResponsesProvider(Provider):
    """OpenAI Responses 格式的框架基类（``api_format="openai_responses"``）。

    .. rubric:: 功能介绍

    所有 OpenAI Responses 端点 adapter 的基类：实现请求/响应格式
    映射——把 :class:`flowing.context.Context` 的三个字段映射为
    ``/responses`` 请求体（system 消息、input item 序列、扁平
    function 声明），把响应 ``output[]`` item 序列映射为
    :class:`ProviderResponse`，并把原始用量归一为 :class:`Usage`
    （``input_tokens`` → ``input``，
    ``fresh_input = input_tokens - cached_tokens``）。子类只覆写差异：
    默认端点（``default_base_url``）、凭证来源、厂商特有字段。

    本类不直接实例化使用——实例化发生在具体厂商子类的懒创建链上
    （一个 providers.yaml 条目一个实例，见 :class:`Provider`）。

    .. rubric:: 使用示例

    .. code-block:: python

        from flowing.providers.openai_responses import OpenAIResponsesProvider
        from flowing.providers import register_provider

        @register_provider
        class MyEndpointProvider(OpenAIResponsesProvider):
            name = "my-endpoint"
            default_base_url = "https://llm.example.com/v1"

    .. rubric:: 行为要点

    - 请求映射：``Context.system_prompt`` 逐段成为 system 消息；
      ``Context.messages`` 逐条按 kind 映射进 ``input[]``，PROVIDER
      消息摊平为顶层 item 序列（reasoning / message / function_call，
      不包一层 assistant message）；``Context.tools`` 经白名单组装为
      扁平 function 声明（非 chat/completions 的嵌套 ``function``
      字段）；``model.max_output_tokens`` 非 ``None`` 时写入
      ``max_output_tokens``。固定附带 ``store: False``（无服务端会话
      状态，历史全量重放）与
      ``include: ["reasoning.encrypted_content"]``（reasoning 项带
      加密内容回传，使 store=false 下多轮 reasoning 回放有效）。
    - 通用透传：models.yaml 条目中的 ``extra_body`` 字段（进
      ``ModelConfig._extra``，不参与 Parsable 求值）为 dict 时原样
      合入请求体，承载厂商私有参数；非 dict 抛
      :class:`flowing.errors.InvalidRequestError`。合入顺序：基类
      固定字段 → ``extra_body`` → adapter 专有字段，同名键后者覆盖
      前者。
    - 思考回放：reasoning 项的完整 JSON 存进
      ``ThinkingBlock.signature``；多轮回放时 ``json.loads`` 为 dict
      则原样追加进 ``input[]``，解析失败 / 非 dict / 无 signature
      则跳过该思考块（无 signature 无法合法回放）。
    - 响应映射：``output[]`` 保序成块（reasoning →
      ``ThinkingBlock``、message → ``TextBlock``、function_call →
      ``ToolCallBlock``，id 取 ``call_id``）；含 ``function_call`` →
      ``finish=False``，其余 → ``finish=True``；原始 ``status`` 保留在
      ``provider_data["stop_reason"]``。
    - Usage 归一：``input = input_tokens``（含 cache 读的毛输入）、
      ``fresh_input = input_tokens - cached_tokens``、
      ``cache_read = input_tokens_details.cached_tokens``、
      ``cache_write = 0``、``output = output_tokens``、
      ``reasoning = output_tokens_details.reasoning_tokens``、
      ``total_tokens = total_tokens or input + output``；原始用量字段
      全量保留在 ``Usage.raw``。
    - 不做厂商探测（如按 ``base_url`` 猜测能力）——厂商差异属于子类
      覆写。
    - 前缀缓存由服务端自动处理，adapter 不发送任何缓存标记；
      ``PromptBlock.cache`` 对本格式只是字节稳定性提示，不产生请求级
      效果。

    .. seealso::

        :class:`flowing.providers.Provider` 抽象契约。
        :class:`flowing.providers.openai_completions.OpenAICompletionsProvider`
            同厂商线的 chat/completions 格式家族基类。
        :class:`flowing.providers.anthropic_messages.AnthropicMessagesProvider`
            另一格式家族基类。
    """

    api_format: ClassVar[str] = "openai_responses"
    known_model_fields: ClassVar[frozenset[str]] = frozenset()
    default_base_url: ClassVar[str | None] = None
    """子类覆写：厂商官方端点；条目配 ``base_url`` 时以条目为准（代理场景）。"""

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

    # ── 请求映射（Context → /responses 请求体）──────────────────────────

    def _build_request(self, context: Context, model: ModelConfig) -> dict:
        """把 ``Context`` 三字段与模型规格映射为 ``/responses`` 请求体。"""
        msg_n = {"v": 0}   # assistant message item id 的请求内自增计数器（msg_{n}）
        items: list[dict[str, Any]] = [
            {"role": "system", "content": seg.content}
            for seg in context.system_prompt
        ]
        for msg in context.messages:
            items.extend(self._map_message(msg, msg_n))
        body: dict[str, Any] = {
            "model": model.model,
            "input": items,
            "store": False,   # 无服务端会话状态：历史全量重放
            "include": ["reasoning.encrypted_content"],   # reasoning 项带加密内容回传，store=false 下回放有效
        }
        if context.tools:
            body["tools"] = [self._map_tool(d) for d in context.tools]
        if model.max_output_tokens is not None:
            body["max_output_tokens"] = model.max_output_tokens
        extra_body = model._extra.get("extra_body")
        if extra_body is not None:
            if not isinstance(extra_body, dict):
                raise InvalidRequestError(
                    f"models.yaml extra_body must be a mapping, "
                    f"got {type(extra_body).__name__}")
            body.update(extra_body)
        return body

    def _map_tool(self, definition) -> dict:
        """白名单组装：只取 name/description/parameters 三已知字段（扁平 function）。"""
        params = definition.params_schema or {}
        return {
            "type": "function",
            "name": definition.name,
            "description": definition.description,
            "parameters": {
                "type": "object",
                "properties": params,
                "required": [k for k, v in params.items() if "default" not in v],
            },
            "strict": False,
        }

    def _map_content_blocks(self, msg: Message) -> tuple[str, list[MediaBlock]]:
        """纯内容块 → 拼接文本 + 媒体块列表（文本位 / 工具结果位共用预处理）。"""
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

    def _map_user_content(self, msg: Message) -> list[dict[str, Any]]:
        """user 系消息的内容位：``input_text`` / ``input_image`` 混合 parts。"""
        content: list[dict[str, Any]] = []
        for block in msg.content:
            if isinstance(block, MediaBlock):
                mime = block.mime_type or "application/octet-stream"
                content.append({
                    "type": "input_image", "detail": "auto",
                    "image_url": f"data:{mime};base64,{block.data}"})
            elif isinstance(block, StructBlock):
                # StructBlock 恒投影为 json.dumps 文本（所有 adapter 统一）
                content.append({"type": "input_text",
                                "text": json.dumps(block.data, ensure_ascii=False)})
            elif isinstance(block, TextBlock):
                content.append({"type": "input_text", "text": block.text})
        return content

    def _map_message(self, msg: Message, msg_n: dict) -> list[dict[str, Any]]:
        """单条消息 → input item 序列（PROVIDER 消息摊平为顶层 item，一对多）。"""
        if msg.kind is MessageKind.PROVIDER:
            # 摊平为顶层 item 序列：reasoning / message / function_call，
            # 不包一层 assistant message；摊平结果为空则跳过本条消息
            output: list[dict[str, Any]] = []
            for block in msg.content:
                if isinstance(block, ToolCallBlock):
                    output.append({
                        "type": "function_call",
                        "call_id": block.id,
                        "name": block.name,
                        "arguments": json.dumps(block.args, ensure_ascii=False),
                    })
                elif isinstance(block, ThinkingBlock):
                    # reasoning 项回放：signature 承载其完整 JSON，原样追加；
                    # 解析失败 / 非 dict / 无 signature → 跳过（无法合法回放）
                    if not block.signature:
                        continue
                    try:
                        item = json.loads(block.signature)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(item, dict):
                        output.append(item)
                elif isinstance(block, StructBlock) or isinstance(block, TextBlock):
                    text = (block.text if isinstance(block, TextBlock)
                            else json.dumps(block.data, ensure_ascii=False))
                    output.append({
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": text,
                                     "annotations": []}],
                        "status": "completed",
                        "id": f"msg_{msg_n['v']}",
                    })
                    msg_n["v"] += 1
            return output
        if msg.kind is MessageKind.TOOL:
            # 工具结果 → function_call_output：纯文本为拼接字符串；含媒体
            # 块为 input_text / input_image 混合 list（媒体原生内嵌）
            text, media = self._map_content_blocks(msg)
            result: Any = text
            if media:
                result = ([{"type": "input_text", "text": text}] if text else []) + [
                    {"type": "input_image", "detail": "auto",
                     "image_url": f"data:{m.mime_type or 'application/octet-stream'};base64,{m.data}"}
                    for m in media]
            return [{"type": "function_call_output",
                     "call_id": msg.tool_call_id, "output": result}]
        if msg.kind is MessageKind.SYSTEM:
            text, _ = self._map_content_blocks(msg)
            return [{"role": "system", "content": text}]
        # USER / EVENT / PEER / SUBAGENT → user
        content = self._map_user_content(msg)
        if not content:
            return []   # 空内容位不合法，跳过本条消息
        return [{"role": "user", "content": content}]

    # ── 响应映射（/responses 响应 → ProviderResponse）────────────────────

    def _map_response(self, resp: dict) -> ProviderResponse:
        """录制/真实响应 dict → ``ProviderResponse`` （usage 附着到消息）。"""
        blocks: list = []
        for item in resp.get("output") or []:
            itype = item.get("type")
            if itype == "reasoning":
                # 思考文本：summary 各段 text 以 "\n\n" 连接，无 summary 回退
                # content 各段；完整 item JSON 存 signature 供多轮原样回放
                summary = item.get("summary") or []
                thinking = "\n\n".join(s.get("text", "") for s in summary)
                if not thinking:
                    thinking = "\n\n".join(
                        c.get("text", "") for c in item.get("content") or [])
                blocks.append(ThinkingBlock(
                    thinking=thinking,
                    signature=json.dumps(item, ensure_ascii=False)))
            elif itype == "message":
                text = "".join(
                    (c.get("text") if c.get("type") == "output_text"
                     else c.get("refusal")) or ""
                    for c in item.get("content") or [])
                blocks.append(TextBlock(text=text))
            elif itype == "function_call":
                try:
                    args = json.loads(item.get("arguments") or "{}")
                except json.JSONDecodeError as exc:
                    raise InvalidRequestError(
                        f"function_call arguments are not valid JSON: {exc}") from exc
                blocks.append(ToolCallBlock(
                    id=item["call_id"], name=item["name"], args=args))
        msg = Message(kind=MessageKind.PROVIDER, content=blocks)
        raw_usage = resp.get("usage")
        if raw_usage:   # usage 附着契约：唯一权威落点是 message.usage
            msg.usage = self._usage_from_raw(raw_usage)
        has_tool_call = any(b.type == "tool_call" for b in blocks)
        return ProviderResponse(
            message=msg,
            model=resp.get("model", ""),
            finish=not has_tool_call,   # 默认准则：有 tool_call → False
            provider_data={"stop_reason": resp.get("status")},
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

    async def generate(
        self, context: Context, model: ModelConfig
    ) -> ProviderResponse:
        """非流式单次生成（映射 / 错误归类见各 ``_*`` 方法）。

        .. rubric:: 行为要点

        - 把组装好的上下文映射为 ``/responses`` 请求体并 POST 到
          ``/responses``；非 2xx 响应经错误归类映射为
          :mod:`flowing.errors` 中的明确类型后上抛（不重试、不兜底）。
        - 2xx 响应映射为 :class:`ProviderResponse`，用量附着到
          ``message.usage``。

        .. seealso:: 同 :meth:`flowing.providers.Provider.generate` 契约。
        """
        body = self._build_request(context, model)
        try:
            resp = await self._post("/responses", body)
        except _HttpResponseError as exc:
            raise self._classify_error(exc) from exc
        return self._map_response(resp)

    def _usage_from_raw(self, raw_usage: dict) -> "Usage | None":
        """原始 usage dict → 归一 :class:`Usage`（流 / 非流共用，口径一致）。

        ``input_tokens`` 含 cache 读（毛输入），``cached_tokens`` 取
        ``input_tokens_details.cached_tokens``；推理 token 取
        ``output_tokens_details.reasoning_tokens``。
        """
        if not raw_usage:
            return None
        details = raw_usage.get("input_tokens_details") or {}
        cached = details.get("cached_tokens") or 0
        prompt = raw_usage.get("input_tokens", 0)
        completion = raw_usage.get("output_tokens", 0)
        return Usage(
            input=prompt,
            fresh_input=prompt - cached,   # input_tokens 含 cache 读
            output=completion,
            cache_read=cached,
            cache_write=0,
            reasoning=(raw_usage.get("output_tokens_details") or {}).get(
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
        return headers

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

        替代基类“一次 generate() 包成单条 text delta”的回退——
        ``/responses`` ``stream=true`` 响应在此逐事件解析。事件流为
        ``event: <类型>`` + ``data: <json>`` 行对（无 ``[DONE]`` 收尾，
        流以 ``response.completed`` 为止）：

        - ``response.output_item.added``：reasoning / message /
          function_call 分别开块，content_index 按“首现顺序”动态分配
          （与 openai 家族同一约定），同一逻辑块的所有 delta 共享同一
          index。
        - 思考：``response.reasoning_summary_text.delta`` 与
          ``response.reasoning_text.delta`` → ``kind="thinking"`` delta；
          ``response.reasoning_summary_part.done`` 补 ``"\\n\\n"`` 段间
          分隔（与非流式 summary 拼接口径一致）。
        - 正文：``response.output_text.delta`` 与
          ``response.refusal.delta`` → ``kind="text"`` delta。
        - 工具：``response.function_call_arguments.delta`` 逐段拼进工具
          槽（不产 delta），``response.function_call_arguments.done`` 以
          完整串为准；``response.output_item.done`` 时以携带完整
          :class:`ToolCallBlock` 的 ``block`` delta 产出（id 取
          ``call_id``，参数整段容错解析）。
        - reasoning 块收尾（``response.output_item.done``）：以一条空
          文本 thinking delta 携带 ``signature=json.dumps(item)``
          （agent 累积时保留首见签名——多轮原样回放必需）；message 块
          收尾不额外产 delta（文本已由逐段 delta 累积完整）。
        - 用量 / 状态：``response.completed``（incomplete 同路径）记
          usage 与 status，经末帧 ``usage`` 与
          ``provider_data["stop_reason"]`` 透出；``response.failed`` 与
          ``error`` 事件按 :meth:`_classify_error` 归类上抛（status 取
          事件里的 status 字段、缺省 400）。
        """
        import httpx

        base_url = self.config.get("base_url") or self.default_base_url
        if not base_url:
            raise FlowingError(
                f"provider entry is missing base_url (adapter {self.name!r} has no official default endpoint)")
        url = f"{str(base_url).rstrip('/')}/responses"
        body = self._build_request(context, model)
        body["stream"] = True

        cidx = {"next": 0}
        cur = {"kind": None, "idx": None, "call_id": None, "name": None,
               "args": ""}   # 当前 output item 的累积槽
        status = None
        last_usage: dict | None = None

        async def _alloc() -> int:
            i = cidx["next"]
            cidx["next"] += 1
            return i

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
                            continue   # event:/空行等跳过
                        data = line[5:].strip()
                        if not data:
                            continue
                        try:
                            ev = json.loads(data)
                        except json.JSONDecodeError:
                            continue
                        etype = ev.get("type")
                        if etype == "response.output_item.added":
                            item = ev.get("item") or {}
                            itype = item.get("type")
                            if itype in ("reasoning", "message", "function_call"):
                                cur["kind"] = itype
                                cur["idx"] = await _alloc()   # 首现顺序分配
                                if itype == "function_call":
                                    cur["call_id"] = item.get("call_id")
                                    cur["name"] = item.get("name")
                                    cur["args"] = item.get("arguments") or ""
                        elif etype in ("response.reasoning_summary_text.delta",
                                       "response.reasoning_text.delta"):
                            if cur["kind"] == "reasoning" and ev.get("delta"):
                                yield ProviderDelta(
                                    kind="thinking", text=ev["delta"],
                                    content_index=cur["idx"])
                        elif etype == "response.reasoning_summary_part.done":
                            if cur["kind"] == "reasoning":
                                # 段间分隔：与非流式 summary 的 "\n\n" 拼接口径一致
                                yield ProviderDelta(
                                    kind="thinking", text="\n\n",
                                    content_index=cur["idx"])
                        elif etype in ("response.output_text.delta",
                                       "response.refusal.delta"):
                            if cur["kind"] == "message" and ev.get("delta"):
                                yield ProviderDelta(
                                    kind="text", text=ev["delta"],
                                    content_index=cur["idx"])
                        elif etype == "response.function_call_arguments.delta":
                            if cur["kind"] == "function_call":
                                cur["args"] += ev.get("delta") or ""
                        elif etype == "response.function_call_arguments.done":
                            if cur["kind"] == "function_call":
                                cur["args"] = ev.get("arguments") or cur["args"]
                        elif etype == "response.output_item.done":
                            item = ev.get("item") or {}
                            itype = item.get("type")
                            if itype == "reasoning" and cur["kind"] == "reasoning":
                                # 空文本 thinking delta 携带签名：agent 保留首见
                                # 签名进 ThinkingBlock.signature（多轮原样回放）
                                yield ProviderDelta(
                                    kind="thinking", text="",
                                    content_index=cur["idx"],
                                    signature=json.dumps(item, ensure_ascii=False))
                            elif itype == "function_call":
                                if cur["kind"] == "function_call":
                                    args = self._parse_tool_args(cur["args"])
                                    call_id = cur["call_id"]
                                    name = cur["name"]
                                else:
                                    # 防御路径（done 先于 added）：以事件 item 为准
                                    args = self._parse_tool_args(
                                        item.get("arguments") or "")
                                    call_id = item.get("call_id")
                                    name = item.get("name")
                                if call_id and name:
                                    idx = cur["idx"]
                                    if idx is None:
                                        idx = await _alloc()
                                    yield ProviderDelta(
                                        kind="tool_use", text="",
                                        content_index=idx,
                                        block=ToolCallBlock(
                                            id=call_id, name=name, args=args))
                            cur["kind"] = None
                        elif etype in ("response.completed", "response.incomplete"):
                            response = ev.get("response") or {}
                            status = response.get("status")
                            if response.get("usage") is not None:
                                last_usage = response["usage"]
                            break   # 流以 completed 为止
                        elif etype in ("response.failed", "error"):
                            err = ev.get("error")
                            if err is None:
                                err = (ev.get("response") or {}).get("error") or {
                                    "code": ev.get("code"),
                                    "message": ev.get("message"),
                                }
                            raise self._classify_error(_HttpResponseError(
                                ev.get("status", 400), {"error": err}, {}))
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError(f"provider request timed out: {exc}") from exc
        except ssl.SSLError as exc:
            # 见 _post：部分 TLS 错误会从 httpcore 直接透出。
            raise NetworkError(f"provider network failure: {exc}") from exc
        except httpx.TransportError as exc:
            raise NetworkError(f"provider network failure: {exc}") from exc

        # 末帧：usage + status。content_index 取未使用下标——末帧只载
        # usage/status，不污染已累积块
        yield ProviderDelta(
            kind="text", text="", content_index=cidx["next"],
            usage=self._usage_from_raw(last_usage) if last_usage else None,
            provider_data={"stop_reason": status},
        )
