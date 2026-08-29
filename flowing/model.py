"""Flowing 模型层规约（``flowing.model``）。

.. rubric:: 功能介绍

本模块承载 Agent 与「用哪个模型说话」之间的契约：模型规格结构体
:class:`ModelConfig` 与两个具名加载器——``models.yaml`` 的
:func:`load_models` 与 ``model-tags.yaml`` 的 :func:`load_model_tags`
（P1-23 裁决具名分工：后者只产标签→条目名映射，``ModelConfig`` 经
两产物 join 得出）。
Provider 侧契约（adapter 继承树 / ``ProviderResponse`` / 一次调用的
token 用量记录 :class:`flowing.providers.Usage` / 注册与懒实例化）在
:mod:`flowing.providers` 包。

本模块属于**框架核心层**。Agent 对模型只做「持有 + 机械传递」：持有
``self.model: ModelConfig``，每次 ``provider_gen()`` 前现场 ``resolve()`` 求值，
再连同 :class:`flowing.context.Context` 一起传给
``Provider.generate()``。字段含义（``thinking_budget`` 等）只有 Provider
adapter 解释，框架核心不解释任何模型字段。

.. rubric:: 设计动机

- **模型 ↔ Provider 1:1 绑定、单值化**：``ModelConfig.provider`` 是绑定
  关系（单个 provider 条目名），不是候选列表；``models.yaml`` 每条目只
  声明一个具体模型；``model-tags.yaml`` 每标签只映射一个模型条目名。
  fallback 体系（同模型换 key、标签候选列表、``strategy`` 字段）整体
  取消——它与「一个模型 = 完整元信息的自包含载体」直接冲突。取消后
  ``provider_gen()`` 简化为单模型、单 provider、单次调用；唯一容错是可选的
  ``use_retry()`` Composable（机制在核心，策略在扩展）。
- **声明层与运行时层分离**：``.fya`` 声明层唯一入口是 ``model_tag``
  （意图：``fast``/``high``/``default`` 等，非枚举、可自定义）；运行时
  层是 ``self.model``（规格：一份完整的 ``ModelConfig``）。
  ``model_tag`` → 标签映射 → 模型条目 → ``ModelConfig`` 的解析结果即
  ``self.model`` 的初始值。``.fya`` 内联 ``model:`` 结构体的写法已废弃。

.. rubric:: 两阶段解析与凭证引用

provider 条目（加载时 ``{{env.VAR}}`` 纯字符串替换）与模型配置（运行时
Parsable 求值）的两阶段解析表、``{{env.VAR}}`` 替换规则全文，见
:mod:`flowing.providers` 包 docstring——该规则对 models.yaml 字段值同样
适用，单一来源不重复。

.. rubric:: 配置文件 schema（模型侧）

模型侧两个文件按「共享范围不同」与 providers.yaml 拆分：凭证绝不共享
（providers.yaml，见 :mod:`flowing.providers`）；模型条目可团队共享；
标签映射允许项目推荐 + 个人覆盖。

``models.yaml``（默认 ``$FLOWING_CONFIG_HOME/models.yaml``，可团队共享，
经 ``FLOWING_MODELS_PATH`` 重定向）::

    sonnet:                     # 模型条目名
      provider: anthropic       # 必填，单值（1:1 绑定）
      model: claude-sonnet-4-6  # 必填，传给 API 的模型 ID
      thinking_budget: 32000    # 可选元信息，字段值可为 Parsable
      # adapter 经 known_model_fields 声明认识的字段 → 提升为实例属性
      # 连 adapter 都不认识的字段 → 静默进 ModelConfig._extra

``model-tags.yaml``（用户级 ``$FLOWING_CONFIG_HOME/model-tags.yaml`` 优先、
项目级 ``@/model-tags.yaml`` 兜底，逐标签覆盖合并）::

    tags:
      fast: deepseek-v4         # 一个标签 = 一个模型条目名（单值）
      high: sonnet
      default: fast

标签优先级：``runtime.set_model_tags(path)``（代码级） >
``FLOWING_MODEL_TAGS`` 环境变量文件 > 用户级 > 项目级。用户显式定义同名
标签即覆盖项目推荐，不写即自然落到项目级。未定义标签回退 ``default``；
``default`` 也未定义 → **报错**（不静默回退）。

.. rubric:: 环境变量表（本模块相关）

- ``FLOWING_CONFIG_HOME`` —— 用户级配置目录（默认 ``~/.flowing``，
  与 provider 侧共享）。
- ``FLOWING_MODELS_PATH`` —— 重定向 ``models.yaml``。
- ``FLOWING_MODEL_TAGS`` —— 最优先的标签映射文件（级联保留，非完全
  重定向）。
- ``FLOWING_TAG_MAPPING_PATH`` —— 已废弃的旧完全重定向变量，不再读取。

.. rubric:: 最小行为（不注入任何 Composable）

用户只写 ``model_tag: fast`` + 三个配置文件时：标签 → 单个模型条目 →
加载凭证 → 调用一次；成功返回，失败异常上抛由 ``on_provider_error`` 决定。
没有内置重试、没有 fallback、没有冷却追踪、没有能力校验、没有成本
追踪——「没有成本追踪」指核心不持久化、不产生副作用，而非丢弃数据：
``Usage`` 的唯一存续面是 ``Message.usage``（PROVIDER 消息携带，随树
落盘）；``TurnResult.token_usage`` 的聚合管线不变——仍由
``TurnContext.usages`` 累加器求和，但累加器持有的是消息上同一
``Usage`` 对象的引用（S-13 裁决；单源化修订：事实只存在于消息一处，
Response 不再携带）。

.. seealso::

    :mod:`flowing.providers`
        Provider 侧契约：adapter 继承树、providers.yaml、注册与懒实例化、
        异常分类与安全边界。
    :class:`flowing.agent.Agent`
        ``self.model`` / ``self.model_tag`` 的持有者，``provider_gen()`` 双模式
        与当场解析的调用方。
    :mod:`flowing.parsable`
        ``ModelConfig`` 字段级求值依赖的 Parsable 机制。
    :class:`flowing.runtime.Runtime`
        ``set_model_tags()`` 的宿主。
"""

from __future__ import annotations   # S-43 裁决③：注解延迟求值，配合 TYPE_CHECKING 破注解级循环边

from pathlib import Path
from typing import TYPE_CHECKING, Any

from ruamel.yaml import YAML  # R-10 澄清落实：yaml 需保留注释，选 ruamel.yaml 而非 PyYAML

from flowing.parsable import LITERAL, Parsable

if TYPE_CHECKING:
    from flowing.agent import Agent


class ModelConfig:
    """模型规格结构体：一个模型 = 全部行为参数的自包含载体。

    .. rubric:: 功能介绍

    ``ModelConfig`` 是运行时层的模型规格——模型 ID 只是其中一个字段，
    ``thinking_budget`` / ``context_window`` / ``max_output_tokens`` 等
    全部模型能力元信息都收拢在本结构体上，不散落在 Agent 的其他属性上。
    拿到一个 ``ModelConfig`` 实例，就拿到了该模型的全部行为参数。
    Agent 实例属性 ``self.model`` 永远是 ``ModelConfig``（不做
    ``isinstance`` 分支），可解析性由自带的 :meth:`resolve` 方法体现。

    .. rubric:: 设计动机

    - **服务商敏感而对 Agent 透明**：Agent 只持有并机械传递给
      ``provider.generate()``，字段含义只有 adapter 解释。这让「换模型」
      在 Flowing 中始终是「换一份完整规格」——不存在「只换 ID、其他
      参数沿用旧模型」的中间态。
    - **1:1 绑定**：``provider`` 字段是绑定关系（单个 provider 条目
      名），不是候选列表。换提供商 = 换模型（或动态构造新结构体）。
    - **元信息继承链（加载期完成合并）**：模型条目显式指定 > provider
      条目同名属性 > adapter 内置默认。例：adapter 内置默认 Sonnet 4.6
      ``context_window=200000``；provider 条目（免费 key）收窄为
      32000；模型条目再做项目级 token 预算调整。运行时字段即合并后的
      最终值；``None`` 表示整条链都未指定，由 adapter 在请求时自行
      决定（通常取 API 默认）。
    - **`_extra` 与 `known_model_fields`**：adapter 经
      :attr:`Provider.known_model_fields` 声明自己认识的元数据字段，
      加载期提升为实例属性（同样可含 Parsable、同样参与
      :meth:`resolve`）；连 adapter 都不认识的字段**静默保留**在
      :attr:`_extra`，Composable 可读，框架核心不解释、不丢弃。
    - **取代旧设计**：旧 ``model_info`` / ``provider_info`` 独立属性已
      删除——模型信息 = ``self.model.resolve(self)`` 的字段；provider
      信息经 ``model.provider`` 绑定查询（Provider 实例自持
      ``name`` / ``api_format``）。

    .. rubric:: 使用示例

    声明式入口（``.fya`` 只允许 ``model_tag``，不允许内联模型结构体）：

    .. code-block:: yaml

        # assistant.fya
        ---
        name: assistant
        model_tag: fast            # 支持 J2："{{ mode_config.model_tag }}"
        system_prompt: |
          你是一个助手。

    .. code-block:: yaml

        # models.yaml —— 一个模型条目 = 一个具体模型
        sonnet:
          provider: anthropic
          model: claude-sonnet-4-6
          thinking_budget: "{{ config.thinking_budget }}"   # 字段级 Parsable

    命令式（运行期两条修改路径，可共存）：

    .. code-block:: python

        agent.model_tag = "high"        # 路径一：改标签 → 重新解析，
                                        # 只能指向配置已定义模型
        agent.model = ModelConfig(      # 路径二：直接换整份规格，
            model="my-finetune",        # 可以是文件/配置中从未定义的模型
            provider="openrouter",
            max_output_tokens=8192,
        )

    .. rubric:: 行为规约

    - 实例创建后字段可被 Composable / 钩子读取；修改 ``agent.model`` 或
      ``agent.model_tag`` 后，**同一逻辑 Turn 内下一次 ``provider_gen()`` 立即
      生效**（内层循环每轮回到顶部重新 ``resolve()``）。
    - 非行为：本类不做任何 provider 探测、能力校验或合法性检查；不兼容
      的模型到 API 调用时才报错。
    - 非行为：不缓存 :meth:`resolve` 结果——每次 ``provider_gen()`` 前现场
      求值（本地不缓存原则）。
    - 边缘情况：``extra=None`` 归一化为空 dict；``_extra`` 内容不属于
      稳定契约（跨版本不保证）。
    - 不变量：``self.model`` 永远是 ``ModelConfig``；``provider`` 字段
      解析后必须能在 provider 候选清单中查到条目，否则 ``provider_gen()``
      时报错。

    .. rubric:: 测试案例

    - 前置：``models.yaml`` 条目 ``sonnet`` 未指定 ``context_window``，
      provider 条目指定 32000，adapter 默认 200000。操作：加载条目。
      期望：``ModelConfig.context_window == 32000``（provider 层覆盖
      adapter 默认）。
    - 前置：模型条目含 adapter 不认识的字段 ``team_note: "x"``。操作：
      加载。期望：``cfg._extra["team_note"] == "x"``，不告警、不丢弃。
    - 前置：``agent.model.thinking_budget`` 为 Parsable。操作：连续两次
      ``provider_gen()`` 之间修改其引用的实例属性。期望：第二次调用拿到新值。

    .. rubric:: 调用关系（审计）

    - 被调：``flowing.agent.Agent.provider_gen()``（每次 provider_gen 前经
      :meth:`resolve` 现场求值，provider_gen 时序第 2 步）；
      ``flowing.snapshot`` ``ModelInfo`` 投影（每次快照生成，按
      ``resolve()`` 语义字段级求值，见 ``flowing.snapshot.ModelInfo``）
    - 实例化方：:func:`load_models`（``model_tag`` →
      :func:`load_model_tags` 标签映射 → 模型条目解析路径，每次加载 /
      改标签重新解析）；用户命令式路径
      ``agent.model = ModelConfig(...)``（非框架内调用）

    .. seealso::

        :class:`Provider`
            ``known_model_fields`` 的声明方，字段含义的唯一解释者。
        :class:`flowing.parsable.Parsable`
            字段级可解析值的五种形式与惰性求值。
        :class:`flowing.agent.Agent`
            ``self.model`` / ``self.model_tag`` 的持有者。
    """

    model: str | Parsable
    """传给 API 的模型 ID。功能与动机：请求侧规格的核心字段，可为
    Parsable（运行时求值）。行为边界：解析后必须是非空字符串；本框架
    不校验其是否被 provider 认识（无能力校验）。
    参见 :meth:`resolve`。
    """
    provider: str | Parsable
    """绑定的 provider 条目名（1:1 单值绑定，非候选列表）。功能与动机：
    「模型定义中即包含其提供商」，换提供商 = 换整份规格。行为边界：
    解析后必须在 Runtime 的 provider 候选清单中存在，否则 provider_gen 时报
    错；禁止列表写法。参见 :class:`Provider`、:func:`register_provider`。
    """
    thinking_budget: int | Parsable | None
    """思考强度（token 预算）。``None`` = 继承链全程未指定，adapter 自行
    决定。行为边界：取值合法性（正整数等）由 adapter 解释，核心不检查。
    """
    context_window: int | Parsable | None
    """上下文窗口大小。主要消费方是 Composable（如 token 预算在
    ``before_provider_gen`` 中读取）。``None`` 语义同 :attr:`thinking_budget`。
    """
    max_output_tokens: int | Parsable | None
    """单次响应最大输出 token 数。``None`` 语义同上。
    """
    _extra: dict[str, Any]
    """adapter 不认识的字段的静默收纳处。功能与动机：扩展可给模型条目
    附加自定义元数据而不被框架丢弃；Composable 可读，框架核心不解
    释。行为边界：加载期填充后运行期只读约定；内容不属于稳定契约。
    参见 :attr:`Provider.known_model_fields`。
    """

    def __init__(
        self,
        model: str | Parsable,
        provider: str | Parsable,
        *,
        thinking_budget: int | Parsable | None = None,
        context_window: int | Parsable | None = None,
        max_output_tokens: int | Parsable | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        """构造一份完整模型规格。

        .. rubric:: 功能介绍

        显式构造 ``ModelConfig``——:func:`load_models`（models.yaml
        加载器，P1-23 裁决具名）与「命令式直接改结构体」路径
        （``agent.model = ModelConfig(...)``）共用此入口。

        .. rubric:: 设计动机

        「换模型 = 换一份完整规格」要求构造入口一次收齐全部字段；关键字
        参数强制调用方明确每个元信息字段，避免位置参数在字段扩展时静默
        错位。

        .. rubric:: 使用示例

        .. code-block:: python

            cfg = ModelConfig(
                model="deepseek-chat",
                provider="deepseek-personal",
                thinking_budget="{{ config.thinking_budget }}",  # Parsable
                context_window=64000,
            )

        .. rubric:: 行为规约

        - 前置条件：``model`` / ``provider`` 非空（解析后判定，构造期
          不强制求值）。
        - 后置条件：``extra=None`` 时 ``self._extra == {}``；本方法不做
          任何 Parsable 求值（求值全部推迟到 :meth:`resolve`）。
        - 非行为：不校验 ``provider`` 条目是否存在、不校验 ``model``
          是否被 adapter 认识——校验发生在 provider_gen 路径上。
        - 边缘情况：同名字段同时出现在显式参数与 ``extra`` 中时，显式
          参数优先（加载器负责在装载前分流，构造器不重复检查）。

        .. rubric:: 测试案例

        - 前置：无。操作：``ModelConfig("m", "p")``。期望：三个可选
          字段为 ``None``，``_extra == {}``。

        .. rubric:: 调用关系（审计）

        - 调用：无
        - 被调：:func:`load_models`（每次装载模型条目）；用户命令式
          路径（``agent.model = ModelConfig(...)``，非框架内调用）

        .. seealso::

            :meth:`resolve` 字段级求值入口。
        """
        self.model = model
        self.provider = provider
        self.thinking_budget = thinking_budget
        self.context_window = context_window
        self.max_output_tokens = max_output_tokens
        self._extra = extra or {}  # extra=None 归一化为空 dict（见 docstring 后置条件）

    def resolve(self, agent: Agent) -> ModelConfig:
        """字段级 Parsable 求值，返回求值后的模型规格。

        .. rubric:: 功能介绍

        对含 Parsable 的字段逐一求值（Jinja2，渲染上下文为 ``agent`` 的
        实例属性，可引用 env / config / 运行时状态），产出一份字段均为
        静态值的 ``ModelConfig``。``Agent.provider_gen()`` 在每次调用 Provider
        之前调用本方法。

        .. rubric:: 设计动机

        模型配置是 Agent 的运行时成员，可引用运行时状态（如
        ``{{ mode_config.thinking_budget }}``），必须每次调用时现场求值
        ——这是「两阶段解析」的运行时半段（provider 凭证在加载时一次性
        替换，见模块 docstring）。「同 Turn 内改模型立即生效」的契约也
        由「每次 provider_gen 前重新 resolve」保证。

        .. rubric:: 使用示例

        .. code-block:: python

            resolved = agent.model.resolve(agent)
            budget = resolved.thinking_budget     # 已是 int
            window = resolved.context_window      # token 预算 Composable 读它

        .. rubric:: 行为规约

        - 期待行为：逐字段求值；Parsable 字段渲染并转型为字段声明类型，
          静态字段原样保留；返回**新实例**，不修改 ``self``。
        - 幂等性：全部字段均为静态值时允许原样返回 ``self``（幂等
          短路）；含 Parsable 时必须返回新实例。
        - 非行为：不触发 provider 查找、不触碰网络、不缓存结果。
        - 边缘情况：``_extra`` 中的值**不参与**求值，原样拷贝到新实例；
          Parsable 渲染失败（语法错误、引用不存在、env 缺失）异常直接
          上抛，由 ``_run_turn`` 的错误路径（``on_provider_error``）裁决。

        :raises flowing.errors.FlowingError:
            Parsable 求值失败时（含模型字段引用的环境变量缺失——运行时
            求值时报，而非加载时报）。

        .. rubric:: 测试案例

        - 前置：``cfg.thinking_budget = Parsable("{{ agent.budget }}")``，
          ``agent.budget = 1000``。操作：``cfg.resolve(agent)``。期望：
          返回新实例，``thinking_budget == 1000``，原 ``cfg`` 不变。
        - 前置：全静态 ``cfg``。操作：``resolve()`` 两次。期望：两次
          结果字段相等（允许返回 ``self``）。
        - 前置：Parsable 引用不存在的 env。操作：``resolve()``。期望：
          抛出求值错误，``cfg`` 本身不被修改。

        .. rubric:: 调用关系（审计）

        - 调用：``flowing.parsable.Parsable.resolve()``（对每个 Parsable
          字段逐字段求值，每次本方法调用）
        - 被调：``flowing.agent.Agent.provider_gen()``（每次 Provider 调用前，
          provider_gen 时序第 2 步，本方法的唯一框架内调用点——docstring
          自述）；``flowing.snapshot`` ``ModelInfo`` 投影（每次快照，
          按本方法语义字段级求值，见 ``flowing.snapshot.ModelInfo``）

        .. seealso::

            :class:`flowing.parsable.Parsable`
                五种形式与两步渲染顺序。
            :meth:`flowing.agent.Agent.provider_gen`
                本方法的唯一框架内调用点（每次 Provider 调用前）。
        """

        def _resolve_field(value: Any) -> Any:
            # 逐字段求值：Parsable 字段渲染并转型，静态字段原样保留
            if isinstance(value, Parsable):
                return value.resolve(agent)  # 渲染失败异常直接上抛（on_provider_error 裁决）
            return value

        # 幂等短路：全部字段均为静态值时允许原样返回 self
        if not any(
            isinstance(v, Parsable)
            for v in (
                self.model,
                self.provider,
                self.thinking_budget,
                self.context_window,
                self.max_output_tokens,
            )
        ):
            return self
        return ModelConfig(
            model=_resolve_field(self.model),
            provider=_resolve_field(self.provider),
            thinking_budget=_resolve_field(self.thinking_budget),
            context_window=_resolve_field(self.context_window),
            max_output_tokens=_resolve_field(self.max_output_tokens),
            extra=dict(self._extra),  # _extra 不参与求值，原样拷贝（见边缘情况）
        )



def load_models(path: Path) -> dict[str, ModelConfig]:
    """读 ``models.yaml`` 构建模型条目名 → `ModelConfig` 映射（P1-23 裁决具名）。

    .. rubric:: 功能介绍

    models.yaml 加载器。**``known_model_fields`` 分流在此发生**（M-43
    裁决口径）：adapter 经 :attr:`Provider.known_model_fields` 声明
    「认识」的字段 → 提升为 `ModelConfig` 实例属性（值含 Parsable，
    参与 ``resolve()`` 求值）；不认识的字段 → 进 ``_extra``，**不参与
    求值**、不进 LLM 可见面。

    与 :func:`load_model_tags` / ``load_provider_candidates`` 同样保持
    纯「路径 → 映射」，不碰环境变量与默认路径。

    :param path: models.yaml 的已解析路径。
    :return: 模型条目名 → `ModelConfig`；``Runtime`` 启动期默认加载
      共用本入口（产物与 :func:`load_model_tags` 的产物 join 出
      ``标签 → ModelConfig`` 的最终解析）。

    .. rubric:: 调用关系（审计）

    - 调用：``ModelConfig`` 构造（每条目；known_model_fields 分流的
      写入侧）；yaml 读取经 ``ruamel.yaml.YAML(typ="rt")``（R-10 澄清：
      yaml 需保留注释，选 ruamel 而非 PyYAML；round-trip 模式读出的
      标量包装类型均为 str/int 子类，对下游透明）
    - 被调：``flowing.runtime.Runtime``（时机：启动期默认加载）

    .. seealso:: :class:`ModelConfig` —— ``_extra`` 与分流语义；
      :func:`load_model_tags` —— 标签映射文件的对应解析器。
    """
    data = YAML(typ="rt").load(Path(path).read_text(encoding="utf-8")) or {}
    models: dict[str, ModelConfig] = {}
    for name, entry in dict(data).items():
        # known_model_fields 分流（R-3 简化形态）：固定内建字段集
        # 升属性，其余一律进 _extra
        fields: dict[str, Any] = {}
        extra: dict[str, Any] = {}
        for key, value in dict(entry).items():
            (fields if key in _KNOWN_MODEL_FIELDS else extra)[key] = value
        models[name] = ModelConfig(
            extra=extra,
            **{key: _maybe_wrap_parsable(value) for key, value in fields.items()},
        )
    return models


# R-3 简化：固定内建字段集（替代 adapter 声明分流）
_KNOWN_MODEL_FIELDS: frozenset[str] = frozenset({
    "model", "provider", "thinking_budget", "context_window", "max_output_tokens",
})


def _maybe_wrap_parsable(value: Any) -> Any:
    """把可解析形态的字段值包装为 Parsable；LITERAL 值保持静态原样。

    口径（就地裁决，spec 未写清 loader 的包装范围）：仅字符串且推断为
    非 ``LITERAL``（``FILE_REF`` / ``EXPRESSION`` / ``TEMPLATE`` /
    ``RAW``）的值才包装——纯字面量保持静态原值，使全静态条目同样命中
    ``ModelConfig.resolve`` 的幂等短路。``.fya`` 解析层的 M-14 全量包装
    （非字符串值也包 ``LITERAL``）是 parser 层规则，与本 loader 的
    「路径 → 映射」简化口径不同。
    """
    if isinstance(value, str):
        p = Parsable(value)
        if p.type is not LITERAL:
            return p
    return value


def load_model_tags(path: Path) -> dict[str, str]:
    """读 ``model-tags.yaml`` 构建标签 → 模型条目名映射（S-03 裁决具名；
    P1-23 裁决改判读取文件）。

    .. rubric:: 功能介绍

    标签映射文件（``model-tags.yaml``）解析器：产物是 ``标签 → 模型
    条目名`` 的纯字符串映射（单值化——一个标签只映射一个模型条目名，
    见模块 docstring「配置文件 schema」节）。**不产出 ``ModelConfig``**
    ——「标签 → 条目名 → ``ModelConfig``」的最后一跳是与
    :func:`load_models` 产物的 join，由 ``Runtime`` 启动期默认加载与
    ``runtime.set_model_tags(path)`` 登记后的现场求值完成（P1-23）。

    与 :func:`load_models` 同样保持纯「路径 → 映射」，不碰环境变量与
    默认路径。

    :param path: model-tags.yaml 的已解析路径。
    :return: 模型标签 → 模型条目名；``runtime.set_model_tags(path)`` 与
      启动期默认加载共用本入口。

    .. rubric:: 调用关系（审计）

    - 调用：yaml 读取（库符号未见规约）
    - 被调：``flowing.runtime.Runtime``（时机：启动期默认加载）；
      ``flowing.runtime.Runtime.set_model_tags()``（时机：代码级
      改标签，优先级高于文件默认——见模块 docstring 标签优先级）

    .. seealso:: :func:`load_models` —— 模型条目文件的对应解析器
      （``known_model_fields`` 分流的落点）。
    """
    data = YAML(typ="rt").load(Path(path).read_text(encoding="utf-8")) or {}
    # 文件 schema 为顶层 tags: 映射（见模块 docstring「配置文件 schema」）；
    # 缺 tags 键 → KeyError（fail fast，不静默回退）
    return {str(tag): str(entry) for tag, entry in dict(data["tags"]).items()}
