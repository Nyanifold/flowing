"""Flowing Provider 层规约（``flowing.providers`` 包）。

.. rubric:: 功能介绍

本包承载「怎么和大模型 API 说话」的全部契约：Provider 抽象基类
:class:`Provider` 及内置 adapter 继承树（:mod:`flowing.providers.openai` /
:mod:`flowing.providers.anthropic` 两个格式家族）、调用产物
:class:`ProviderResponse` / :class:`ProviderDelta` / 一次调用的 token 用量
记录 :class:`Usage`、条目配置
:class:`ProviderConfig`、adapter 注册装饰器 :func:`register_provider`、
Runtime 级懒实例化表 :class:`ProviderRegistry`、内置测试替身
:class:`FakeProvider`。模型侧契约（``ModelConfig`` / models.yaml /
标签映射）在 :mod:`flowing.model`。

Agent 对模型只做「持有 + 机械传递」：持有 ``self.model: ModelConfig``，
每次 ``provider_gen()`` 前现场 ``resolve()`` 求值，再连同
:class:`flowing.context.Context` 一起传给 ``Provider.generate()``。
字段含义（``thinking_budget`` 等）只有 Provider adapter 解释，框架核心
不解释任何模型字段。

.. rubric:: 设计动机

- **显式 adapter 继承树，非配置驱动**：Provider 之间的差异是 API 格式
  级别的，不是参数级别的。每种格式一个框架基类（``api_format`` 由基类
  决定），每个具体服务商一个子类，差异写在子类覆写中。禁止 compat
  flags（``is_deepseek_compatible=True``）与运行时检测（``if
  api_format == "openai"``）——它们把格式差异降级为参数差异，破坏类型
  安全。
- **懒创建（一条目一实例）**：Runtime 初始化时仅扫描配置文件构建候选
  清单 ``条目名 → (adapter 类, config)``，首次 ``get(条目名)`` 才实例化
  并缓存。动机是零启动成本：配置 20 个条目只用 1 个时，其余 19 个的
  连接池 / HTTP 会话成本完全不发生。每个 provider 条目绑定唯一身份
  （API key）；同一 adapter 的多个 key 写成多个条目（如
  ``deepseek-personal`` / ``deepseek-team``），条目名即身份标识。
- **流式挡在 Turn 循环外**：``provider_gen()`` 以显式 ``stream`` 参数（默认
  ``True``，S-17 最终裁决——不由订阅者存在性决定）把流式封装在
  ``generate()`` / ``generate_stream()`` 两个方法之内；Turn 循环只见
  完整的 :class:`ProviderResponse`。流式只影响三处：``provider_gen()`` 内部、
  ``on_provider_delta`` 钩子（value 为 :class:`ProviderDelta`，volatile
  不落盘）、``Message.partial`` 字段。非流式路径（``stream=False``）
  同样合成**一条**全量 delta 触发钩子——订阅者永远能依赖「每次 provider_gen
  至少一条 delta」，且两种路径下 delta 数据格式完全一致（用户裁决）。
  ``side_query`` 固定 ``stream=False``，以 ``by="_side"`` 标记来源
  （delta / 响应均透写 ``by``，钩子可按之过滤）。
- **不在 ``main()`` 中声明 provider**：``main.py`` 是可发布复用的智能体
  逻辑，provider 是用户侧选择，两者分离；同一份 main 可在不同环境绑定
  不同 provider。

.. rubric:: 术语：adapter 类 vs provider 条目

本包有两层「provider」概念，务必区分：

- **adapter 类**（类型层）：:class:`Provider` 的具体子类，如
  ``DeepSeekProvider``。它回答「**怎么**和这种 API 说话」——请求/响应
  格式、字段解释、错误归类。以类属性 ``name``（如 ``"deepseek"``）经
  :func:`register_provider` 注册进**进程级**全局注册表；adapter 名 =
  **类型标识**，全局唯一。
- **provider 条目**（实例层）：``providers.yaml`` 里的一个 key，如
  ``deepseek-team``。它回答「**用哪个身份**说话」——绑定唯一
  API key / base_url。Runtime 按条目的 ``adapter`` 字段查注册表选类，
  首次 ``get(条目名)`` 才懒创建实例并缓存（一条目一实例）；条目名 =
  **身份标识**，同一 adapter 类的多个 key 写成多个条目。

映射关系：**条目名 →（adapter 名 → adapter 类）→ 实例**。因此
``ModelConfig.provider`` 引用的是条目名（身份），``providers.yaml``
条目的 ``adapter`` 字段引用的是 adapter 名（类型），``Provider.name``
是后者——两个「name」分属两层，不混用。

.. rubric:: 两阶段解析（加载时 vs 运行时）

+----------------------------+---------------------------+-----------------------------+
| 位置                       | 解析时机                  | 机制                        |
+============================+===========================+=============================+
| Provider 条目              | **加载时**一次性          | ``{{env.VAR}}`` 纯字符串    |
| （providers.yaml）         |                           | 替换（非 Jinja2/Parsable）  |
+----------------------------+---------------------------+-----------------------------+
| 模型配置（ModelConfig）    | **运行时**每次 provider_gen 前   | Parsable（Jinja2，可引用    |
|                            |                           | env/config/实例属性）       |
+----------------------------+---------------------------+-----------------------------+

环境变量引用规则（provider / 模型条目所有字段值）：``{{env.VAR}}`` →
``os.environ["VAR"]``；不以 ``{{env.`` 开头的 ``{{`` **保持原样**（不
报错、不替换）。环境变量不存在时：provider 条目**加载时**抛
:class:`flowing.errors.MissingEnvironmentVariableError`；模型配置字段
**运行时求值时**经 Parsable 求值错误路径报错。该字符串替换机制完全独立
于 Parsable 的 Jinja2 渲染——凭证引用是纯静态操作，总在加载时一次完成。

.. rubric:: 配置文件 schema：providers.yaml

默认 ``$FLOWING_CONFIG_HOME/providers.yaml``，用户私有、含密钥、文件
权限必须 chmod 600（模型侧文件 schema 见 :mod:`flowing.model`）::

    deepseek-personal:          # 条目名 = 身份标识
      adapter: deepseek         # 必填，register_provider 的注册键
      api_key: sk-...           # 可选，支持 {{env.VAR}}
      base_url: https://...     # 可选
      # 其余字段均为 adapter 自读字段，框架核心不解释

.. rubric:: 环境变量表（本包相关）

- ``FLOWING_CONFIG_HOME`` —— 用户级配置目录（默认 ``~/.flowing``，
  与模型侧共享）。
- ``FLOWING_PROVIDERS_PATH`` —— 重定向 ``providers.yaml``。
- ``FLOWING_PROVIDER_MODULES`` —— 扩展 provider 模块路径
  （``:``/``;`` 分隔），初始化时 import 以触发其中的
  :func:`register_provider`。

adapter 自动发现第二来源：扫描 ``sys.path`` 上所有 ``flowing_provider_*``
式包名（用 ``pkgutil.iter_modules()``，本地目录包与 editable 安装同样
覆盖，不要求 dist-info 元数据）并 import。

.. rubric:: Provider 异常分类（机制在核心，策略在扩展）

adapter 必须把底层错误归类为 :mod:`flowing.errors` 中的明确类型上抛，
``generate()`` 自身不做兜底捕获：

- 可重试类：``RateLimitedError`` / ``ServerError`` / ``NetworkError`` /
  ``ProviderTimeoutError``（是否退避重试由 ``use_retry()`` 决定）。
- 不可重试类：``AuthenticationError`` / ``InvalidRequestError`` /
  ``ContentPolicyError`` / ``QuotaExhaustedError``（429 配额耗尽，与瞬时
  限流的 ``RateLimitedError`` 对偶）/ ``RequestTooLargeError``（413 字节
  超限；媒体剥离重发属 handler 职责）。
- ``ContextLengthError``：不可重试，且**不经过** ``on_provider_error``
  钩子，直接上抛。
- ``MissingEnvironmentVariableError``：provider 条目加载时抛出。

.. rubric:: 工具结果映射（TOOL/EVENT 消息 → API）

TOOL/EVENT 消息的 ``content`` 是纯内容块（工具结果摊平设计，无协议
块——配对元数据在消息级 ``tool_call_id`` / ``tool_status``，见
:mod:`flowing.message`），adapter 按以下规则映射为各家 API 的工具
结果形态：

- **content 块直接映射**：``TextBlock`` → 各家文本位；
  ``MediaBlock`` 子类 → 各家媒体位；``StructBlock`` 恒投影为
  ``json.dumps(ensure_ascii=False)`` 文本（所有 adapter 统一，不做
  任何原生结构化映射）。
- **配对锚**：消息级 ``tool_call_id`` → 各家 tool_result 的 id 位
  （Anthropic ``tool_use_id``、OpenAI ``tool_call_id``、Gemini
  ``functionResponse`` 的 name/id 位）；``tool_status="error"`` →
  各家错误标记（Anthropic ``is_error``）。
- **各家媒体形态**：Anthropic ``tool_result`` 原生内嵌媒体块；
  OpenAI Chat Completions 的 tool 消息为文本-only → **媒体转移**：
  媒体块攒入紧随 tool 消息的合成 user 消息（固定措辞提示）；Gemini
  ``functionResponse`` 后跟媒体 part。
- **空 content 兜底**：返回 Task 路径的 ``pending`` 收据（``output=None``
  → ``content=[]``）等空 TOOL 消息，其 API 层兜底形态（各家对空
  tool_result 的接受度不同）属各 adapter 职责；async gen 路径的
  pending 收据带内容（首 yield + 「后台任务 ID」块，B10），按正常
  tool_result 映射（B1/B10 承诺的 LLM 可见链路）。
- **白名单组装**（D21）：adapter 从 ``llm_definition()`` 产物只取
  已知字段（name/description/parameters 等）构造 API schema；产物
  可含非直发字段（``output_schema`` 随声明携带），白名单取用下
  天然不被映射。

.. rubric:: 安全边界

API key 等凭证只存在于 ``ProviderConfig`` 与 Provider 实例内部：不进
消息、不进 ``_provided``、不落盘（``tree.jsonl`` / ``state.jsonl`` 均不
含凭证）。含密钥的配置文件必须 chmod 600。

.. seealso::

    :mod:`flowing.model`
        模型侧契约：``ModelConfig`` / models.yaml / 标签映射。
    :class:`flowing.agent.Agent`
        ``provider_gen()`` 双模式与当场解析的调用方。
    :mod:`flowing.errors`
        Provider 异常分类的统一异常层次。
    :class:`flowing.runtime.Runtime`
        ``provider_registry``（懒创建候选清单）的宿主。
"""

from flowing.providers.anthropic import (
    AnthropicMessagesProvider,
    AnthropicProvider,
    BedrockProvider,
)
from flowing.providers.openai import (
    DeepSeekProvider,
    GroqProvider,
    KimiProvider,
    OpenAICompletionsProvider,
    OpenRouterProvider,
)
from flowing.providers.provider import (
    FakeProvider,
    Provider,
    ProviderConfig,
    ProviderDelta,
    ProviderRegistry,
    ProviderResponse,
    Usage,
    load_provider_candidates,
    register_provider,
)

__all__ = [
    "AnthropicMessagesProvider",
    "AnthropicProvider",
    "BedrockProvider",
    "DeepSeekProvider",
    "FakeProvider",
    "GroqProvider",
    "KimiProvider",
    "OpenAICompletionsProvider",
    "OpenRouterProvider",
    "Provider",
    "ProviderConfig",
    "ProviderDelta",
    "ProviderRegistry",
    "ProviderResponse",
    "Usage",
    "load_provider_candidates",
    "register_provider",
]
