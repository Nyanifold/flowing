"""``flowing.model`` —— 模型规格结构体与模型配置文件加载。

.. rubric:: 功能介绍

本模块承载 Agent 与“用哪个模型说话”之间的契约：模型规格结构体
:class:`ModelConfig` 与两个具名加载器——``models.yaml`` 的
:func:`load_models` 与 ``model-tags.yaml`` 的 :func:`load_model_tags`
（后者只产标签 → 条目名映射，``ModelConfig`` 经两产物 join 得出）。
Provider 侧契约（adapter 继承树 / ``ProviderResponse`` / token 用量
记录 :class:`flowing.providers.Usage` / 注册与懒实例化）在
:mod:`flowing.providers` 包。

Agent 对模型只做“持有 + 机械传递”：持有 ``self.model: ModelConfig``，
每次 ``provider_gen()`` 前现场 ``resolve()`` 求值，再连同
:class:`flowing.context.Context` 一起传给 ``Provider.generate()``。
字段含义（``thinking_budget`` 等）只有 Provider adapter 解释，框架
核心不解释任何模型字段。

模型 ↔ Provider 是 1:1 单值绑定：``ModelConfig.provider`` 是绑定关系
（单个 provider 条目名），不是候选列表；``models.yaml`` 每条目只声明
一个具体模型；``model-tags.yaml`` 每标签只映射一个模型条目名。没有
内置重试、没有 fallback、没有能力校验。

.. rubric:: 配置文件 schema

``models.yaml`` （默认 ``$FLOWING_CONFIG_HOME/models.yaml``，可团队
共享，经 ``FLOWING_MODELS_PATH`` 重定向）::

    sonnet:                     # 模型条目名
      provider: anthropic       # 必填，单值（1:1 绑定）
      model: claude-sonnet-4-6  # 必填，传给 API 的模型 ID
      thinking_budget: 32000    # 可选元信息，字段值可为 Parsable
      # 字段分流：固定内建字段集（见 load_models）升为 ModelConfig
      # 实例属性，其余字段静默进 ModelConfig._extra，可经属性或下标读取

``model-tags.yaml`` （默认 ``$FLOWING_CONFIG_HOME/model-tags.yaml``）::

    tags:
      fast: deepseek-v4         # 一个标签 = 一个模型条目名（单值）
      high: sonnet
      default: fast

标签优先级从高到低：``runtime.set_model_tags(path)`` （代码级，``@/``
前缀指向项目根）、``FLOWING_MODEL_TAGS`` 环境变量指向的文件、默认
``$FLOWING_CONFIG_HOME/model-tags.yaml``。未定义标签回退 ``default``；
``default`` 也未定义 → 报错（不静默回退）。

.. rubric:: 使用示例

.. code-block:: python

    from pathlib import Path
    from flowing.model import load_model_tags, load_models

    tags = load_model_tags(Path("model-tags.yaml"))   # 标签 → 条目名
    models = load_models(Path("models.yaml"))         # 条目名 → ModelConfig
    cfg = models[tags["fast"]]                        # 标签 → ModelConfig

.. rubric:: 行为要点

- 两阶段解析：provider 条目（加载时 ``{{env.VAR}}`` 纯字符串替换）与
  模型配置（运行时 Parsable 求值）的解析表、``{{env.VAR}}`` 替换规则
  全文见 :mod:`flowing.providers` 包 docstring。要点：providers.yaml
  中的 ``{{env.VAR}}`` 缺失在加载期替换为空串并告警，不中断加载
  （缺失凭证的后果在首次调用时经 ``on_provider_error`` 暴露）；
  ``ModelConfig`` 字段中的 ``{{env.VAR}}`` 是运行时 Parsable 求值，其
  失败按普通求值异常处理。
- 最小行为：用户只写 ``model_tag: fast`` + 三个配置文件时：标签 →
  单个模型条目 → 加载凭证 → 调用一次；成功返回，失败异常上抛由
  ``on_provider_error`` 决定。没有内置重试、没有 fallback、没有能力
  校验。

.. rubric:: 环境变量表（本模块相关）

- ``FLOWING_CONFIG_HOME`` —— 用户级配置目录（默认 ``~/.flowing``，
  与 provider 侧共享）。
- ``FLOWING_MODELS_PATH`` —— 重定向 ``models.yaml``。
- ``FLOWING_MODEL_TAGS`` —— 最优先的标签映射文件（级联保留，非完全
  重定向）。

.. seealso::

    :mod:`flowing.providers`
        Provider 侧契约：adapter 继承树、providers.yaml、注册与懒实例化、
        异常分类与安全边界。
    :class:`flowing.agent.Agent`
        ``self.model`` / ``self.model_tag`` 的持有者，``provider_gen()``
        双模式与当场解析的调用方。
    :mod:`flowing.parsable`
        ``ModelConfig`` 字段级求值依赖的 Parsable 机制。
    :class:`flowing.runtime.Runtime`
        ``set_model_tags()`` 的宿主。
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from ruamel.yaml import YAML  # yaml 需保留注释，选 ruamel.yaml 而非 PyYAML

from flowing.parsable import LITERAL, Parsable

if TYPE_CHECKING:
    from flowing.agent import Agent


class ModelConfig:
    """模型规格结构体：一个模型 = 全部行为参数的自包含载体。

    .. rubric:: 功能介绍

    ``ModelConfig`` 是运行时层的模型规格——模型 ID 只是其中一个字段，
    ``thinking_budget`` / ``context_window`` / ``max_output_tokens`` 等
    全部模型能力元信息都收拢在本结构体上。拿到一个 ``ModelConfig`` 实例，
    就拿到了该模型的全部行为参数。Agent 实例属性 ``self.model`` 永远是
    ``ModelConfig``，可解析性由自带的 :meth:`resolve` 方法体现。

    “换模型”在 Flowing 中始终是“换一份完整规格”：``provider`` 字段是
    绑定关系（单个 provider 条目名），换提供商 = 换模型（或动态构造新
    结构体）；不存在“只换 ID、其他参数沿用旧模型”的中间态。

    字段分流：:func:`load_models` 把固定内建字段集（``model`` /
    ``provider`` / ``thinking_budget`` / ``context_window`` /
    ``max_output_tokens``）提升为实例属性；models.yaml 条目中的其余字段
    静默收纳进 :attr:`_extra`，可通过同名属性或 ``cfg["字段名"]`` 读取，
    框架核心不解释、不丢弃。

    .. rubric:: 使用示例

    声明式入口（``.fya`` 只允许 ``model_tag``，不允许内联模型结构体）:

    .. code-block:: yaml

        # assistant.fya
        ---
        name: assistant
        model_tag: fast            # 标签名（见 model-tags.yaml；缺省 "default"）
        system_prompt: |
          你是一个助手。

    .. code-block:: yaml

        # models.yaml —— 一个模型条目 = 一个具体模型
        sonnet:
          provider: anthropic
          model: claude-sonnet-4-6
          thinking_budget: "{{ config.thinking_budget }}"   # 字段级 Parsable

    命令式（运行期两条修改路径，可共存）:

    .. code-block:: python

        agent.model_tag = "high"        # 路径一：改标签 → 重新解析，
                                        # 只能指向配置已定义模型
        agent.model = ModelConfig(      # 路径二：直接换整份规格，
            model="my-finetune",        # 可以是文件/配置中从未定义的模型
            provider="openrouter",
            max_output_tokens=8192,
        )

    .. rubric:: 行为要点

    - 实例创建后字段可被 Composable / 钩子读取；修改 ``agent.model`` 或
      ``agent.model_tag`` 后，同一逻辑 Turn 内下一次 ``provider_gen()``
      立即生效（内层循环每轮回到顶部重新 ``resolve()``）。
    - 本类不做任何 provider 探测、能力校验或合法性检查；不兼容的模型到
      API 调用时才报错。
    - 不缓存 :meth:`resolve` 结果——每次 ``provider_gen()`` 前现场求值。
    - ``extra=None`` 归一化为空 dict；扩展字段可经同名属性或下标读取，
      ``_extra`` 内部存储形态不属于稳定契约（跨版本不保证）。
    - 不变量：``self.model`` 永远是 ``ModelConfig``；``provider`` 字段解析
      后必须能在 provider 候选清单中查到条目，否则 ``provider_gen()`` 时
      报错。

    .. seealso::

        :class:`flowing.parsable.Parsable`
            字段级可解析值的五种形式与惰性求值。
        :class:`flowing.agent.Agent`
            ``self.model`` / ``self.model_tag`` 的持有者。
    """

    model: str | Parsable
    """传给 API 的模型 ID；可为 Parsable（运行时求值）。解析后应为非空
    字符串；本框架不校验其是否被 provider 认识（无能力校验）。"""
    provider: str | Parsable
    """绑定的 provider 条目名（1:1 单值绑定，非候选列表）；「模型定义中
    即包含其提供商」。解析后必须能在 Runtime 的 provider 候选清单中查到
    条目，否则 ``provider_gen()`` 时报错；禁止列表写法。"""
    thinking_budget: int | Parsable | None
    """思考强度（token 预算）。``None`` = 未声明，adapter 自行决定。
    取值合法性（正整数等）由 adapter 解释，核心不检查。"""
    context_window: int | Parsable | None
    """上下文窗口大小。主要消费方是 Composable（如 token 预算在
    ``before_provider_gen`` 中读取）与上下文占用估计。``None`` 语义同
    ``thinking_budget``。"""
    max_output_tokens: int | Parsable | None
    """单次响应最大输出 token 数。``None`` 语义同上。"""
    _extra: dict[str, Any]
    """models.yaml 条目中不在固定内建字段集内的其余字段的收纳处。扩展可给
    模型条目附加自定义元数据而不被框架丢弃；Composable 可读，框架核心
    不解释。加载期填充后运行期只读约定；内容不属于稳定契约。"""

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

        :func:`load_models` （models.yaml 加载器）与“命令式直接改结构体”路径
        （``agent.model = ModelConfig(...)``）共用此入口。关键字参数强制调用方
        明确每个元信息字段。

        .. rubric:: 使用示例

        .. code-block:: python

            from flowing.parsable import Parsable

            cfg = ModelConfig(
                model="deepseek-chat",
                provider="deepseek-personal",
                thinking_budget=Parsable("{{ config.thinking_budget }}"),
                context_window=64000,
            )

        .. rubric:: 行为要点

        - ``model`` / ``provider`` 是必填位置参数（缺一不可）。
        - ``extra=None`` 时 ``self._extra == {}``；本方法不做任何 Parsable
          求值（求值全部推迟到 :meth:`resolve`）。
        - 不校验 ``provider`` 条目是否存在、不校验 ``model`` 是否被 adapter
          认识——校验发生在 provider_gen 路径上。
        - 同名字段同时出现在显式参数与 ``extra`` 中时，显式参数优先（加载器
          负责在装载前分流，构造器不重复检查）。

        :param model: 传给 API 的模型 ID（可为 Parsable）。
        :param provider: 绑定的 provider 条目名（可为 Parsable）。
        :param thinking_budget: 思考强度（token 预算），缺省 ``None``。
        :param context_window: 上下文窗口大小，缺省 ``None``。
        :param max_output_tokens: 单次响应最大输出 token 数，缺省 ``None``。
        :param extra: 不在固定内建字段集内的其余字段，缺省 ``None`` （归一化为
            空 dict）。

        .. seealso:: :meth:`resolve` 字段级求值入口。
        """
        self.model = model
        self.provider = provider
        self.thinking_budget = thinking_budget
        self.context_window = context_window
        self.max_output_tokens = max_output_tokens
        self._extra = extra or {}  # extra=None 归一化为空 dict（见 docstring 后置条件）

    def resolve(self, agent: Agent) -> ModelConfig:
        """字段级 Parsable 求值，返回求值后的模型规格。

        对含 Parsable 的字段逐一求值（Jinja2，渲染上下文为 ``agent`` 的实例
        属性，可引用 env / config / 运行时状态），产出一份字段均为静态值的
        ``ModelConfig``。``Agent.provider_gen()`` 在每次调用 Provider 之前
        调用本方法——“同 Turn 内改模型立即生效”的契约由“每次 provider_gen
        前重新 resolve”保证。

        .. rubric:: 使用示例

        .. code-block:: python

            resolved = agent.model.resolve(agent)
            budget = resolved.thinking_budget     # 已是 int
            window = resolved.context_window      # token 预算 Composable 读它

        .. rubric:: 行为要点

        - 逐字段求值：Parsable 字段按 Parsable 语义求值（``EXPRESSION`` 返回
          表达式原生值，``TEMPLATE`` 返回渲染字符串，``LITERAL`` / ``RAW``
          原样返回），静态字段原样保留；返回新实例，不修改 ``self``。
        - 幂等性：全部字段均为静态值时允许原样返回 ``self`` （幂等短路）；
          含 Parsable 时必须返回新实例。
        - 不触发 provider 查找、不触碰网络、不缓存结果。
        - ``_extra`` 中的值不参与求值，原样拷贝到新实例。
        - 未定义为正式字段的名称经属性访问或 ``cfg["字段名"]`` 读取
          ``_extra``；下标读取正式字段时返回对应属性。下标键按完整字符串
          匹配，不拆分点号（如 ``cfg["reasoning.effort"]``）。
    - Parsable 求值失败（语法错误等）异常直接上抛，由 ``_run_turn`` 的
          错误路径（``on_provider_error``）处理；模板引用未定义变量按 Jinja2
          默认行为处理（不抛）。

        :param agent: 渲染上下文（``flowing.agent.Agent`` 实例）。
        :return: 求值后的 ``ModelConfig`` （含 Parsable 时为新实例；全静态时
            可能返回 ``self``）。
        """

        def _resolve_field(value: Any) -> Any:
            # 逐字段求值：Parsable 字段求值，静态字段原样保留
            if isinstance(value, Parsable):
                return value.resolve(agent)  # 渲染失败异常直接上抛（经 on_provider_error 错误路径）
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

    def __getattr__(self, name: str) -> Any:
        """显式属性不存在时，从 ``_extra`` 读取同名模型扩展字段。

        :raises AttributeError: 扩展字段中也不存在该名称。
        """
        extra = vars(self).get("_extra", {})
        try:
            return extra[name]
        except KeyError:
            raise AttributeError(
                f"{type(self).__name__!s} has no attribute {name!r}") from None

    def __getitem__(self, key: str) -> Any:
        """按字段名读取正式属性或 ``_extra`` 中的模型扩展字段。

        正式模型字段优先；其余字符串作为 ``_extra`` 的完整键查找，
        包含点号的键不会被拆分。

        :param key: 正式字段名或 ``_extra`` 中的完整字符串键。
        :raises KeyError: 正式字段与扩展字段中均不存在该键。
        """
        if key in _KNOWN_MODEL_FIELDS:
            return getattr(self, key)
        try:
            return vars(self).get("_extra", {})[key]
        except KeyError:
            raise KeyError(key) from None



def load_models(path: Path) -> dict[str, ModelConfig]:
    """读 ``models.yaml`` 构建模型条目名 → ``ModelConfig`` 映射。

    字段分流在此发生：固定内建字段集（``model`` / ``provider`` /
    ``thinking_budget`` / ``context_window`` / ``max_output_tokens``）提升
    为 ``ModelConfig`` 实例属性；条目中的其余字段进 ``_extra``，不参与
    求值、不进 LLM 可见面。字符串字段值按 Parsable 判定：模板形态
    （``FILE_REF`` / ``EXPRESSION`` / ``TEMPLATE`` / ``RAW``）包装为
    Parsable，纯字面量保持静态原值。

    与 :func:`load_model_tags` 同样保持纯“路径 → 映射”，不碰环境变量与
    默认路径。``Runtime`` 启动期默认加载共用本入口（产物与
    :func:`load_model_tags` 的产物 join 出“标签 → ``ModelConfig``”的最终
    解析）。

    :param path: models.yaml 的已解析路径。
    :return: 模型条目名 → ``ModelConfig``。

    .. rubric:: 行为要点

    - 每个条目必须含 ``model`` / ``provider`` 两个必填字段（缺失时
      ``ModelConfig`` 构造报 ``TypeError``）。
    - 文件不存在 → 原生 ``FileNotFoundError`` （不包装）。

    .. seealso:: :class:`ModelConfig` —— ``_extra`` 与分流语义；
        :func:`load_model_tags` —— 标签映射文件的对应解析器。
    """
    data = YAML(typ="rt").load(Path(path).read_text(encoding="utf-8")) or {}
    models: dict[str, ModelConfig] = {}
    for name, entry in dict(data).items():
        # 字段分流：固定内建字段集升属性，其余一律进 _extra
        fields: dict[str, Any] = {}
        extra: dict[str, Any] = {}
        for key, value in dict(entry).items():
            (fields if key in _KNOWN_MODEL_FIELDS else extra)[key] = value
        models[name] = ModelConfig(
            extra=extra,
            **{key: _maybe_wrap_parsable(value) for key, value in fields.items()},
        )
    return models


# 固定内建字段集：加载时提升为 ModelConfig 实例属性的字段名（其余进 _extra）
_KNOWN_MODEL_FIELDS: frozenset[str] = frozenset({
    "model", "provider", "thinking_budget", "context_window", "max_output_tokens",
})


def _maybe_wrap_parsable(value: Any) -> Any:
    """把可解析形态的字符串字段值包装为 Parsable；纯字面量保持静态原样。

    仅字符串且推断为非 ``LITERAL`` （``FILE_REF`` / ``EXPRESSION`` /
    ``TEMPLATE`` / ``RAW``）的值才包装——纯字面量保持静态原值，使全静态
    条目同样命中 ``ModelConfig.resolve`` 的幂等短路。内部 API，不属稳定
    契约。
    """
    if isinstance(value, str):
        p = Parsable(value)
        if p.type is not LITERAL:
            return p
    return value


def load_model_tags(path: Path) -> dict[str, str]:
    """读 ``model-tags.yaml`` 构建标签 → 模型条目名映射。

    标签映射文件解析器：产物是“标签 → 模型条目名”的纯字符串映射（单值
    化——一个标签只映射一个模型条目名）。不产出 ``ModelConfig``——“标签 →
    条目名 → ``ModelConfig``”的最后一跳是与 :func:`load_models` 产物的
    join，由 ``Runtime`` 启动期默认加载与 ``runtime.set_model_tags(path)``
    登记后的现场求值完成。

    与 :func:`load_models` 同样保持纯“路径 → 映射”，不碰环境变量与默认
    路径。

    :param path: model-tags.yaml 的已解析路径。
    :return: 模型标签 → 模型条目名。

    .. rubric:: 行为要点

    - 文件顶层必须含 ``tags`` 映射键；缺失时抛 ``KeyError`` （fail-fast，
      不静默回退）。
    - 文件不存在 → 原生 ``FileNotFoundError`` （不包装）。

    .. seealso:: :func:`load_models` —— 模型条目文件的对应解析器。
    """
    data = YAML(typ="rt").load(Path(path).read_text(encoding="utf-8")) or {}
    # 文件 schema 为顶层 tags: 映射（见模块 docstring「配置文件 schema」）；
    # 缺 tags 键 → KeyError（fail fast，不静默回退）
    return {str(tag): str(entry) for tag, entry in dict(data["tags"]).items()}
