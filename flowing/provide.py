"""provide/inject 链的叶子模块：``ProvideNode`` 协议与 ``inject_from`` 统一上溯算法。

.. rubric:: 为什么独立成叶子模块（S-43 裁决③）

本模块的两个符号原本住在 ``flowing.runtime``。但 ``Agent.inject`` /
``Workflow.inject`` 都要委托 ``inject_from``，而 ``runtime.py`` 又需要
``Agent`` / ``Workflow``——共享符号住在上游模块制造了
``agent ↔ runtime`` 循环依赖。按「**共享符号下沉到叶子模块**」原则
（真正实现走这条路，不以函数体内局部 import 兜底），两者迁到本模块：
本模块只依赖 ``flowing.errors``（叶子），``Runtime`` 仅以
``TYPE_CHECKING`` 前向引用出现，依赖方向恢复单向。

``flowing.runtime`` 对两个符号做 **import + 再导出**（其 ``__all__``
保留），旧引用 ``flowing.runtime.ProvideNode`` /
``flowing.runtime.inject_from`` 保持有效；但规约的单一来源
（canonical home）是本模块。

.. seealso:: :mod:`flowing.runtime`、:mod:`flowing.params`（``InjectionKey``）
"""

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from flowing.errors import MissingProvideError

if TYPE_CHECKING:
    from flowing.runtime import Runtime

__all__ = ["ProvideNode", "inject_from"]


@runtime_checkable
class ProvideNode(Protocol):
    """provide-inject 链上的节点协议：Runtime / Workflow / Agent 三类实现。

    .. rubric:: 功能介绍

    框架核心层协议（``@runtime_checkable``）。统一「可 provide / 可 inject」的
    类型签名：Runtime（链终点）、Workflow、Agent 都实现它。节点间通过 UID 字符串
    （``_parent_id``）链接，**不持有对象引用**——因此本协议刻意不定义 ``_parent``。

    .. rubric:: 设计动机

    - 只统一类型签名，**不改变 inject 的线性查找逻辑**（见 ``inject_from``）。
    - 节点间用字符串 ID 链接，使销毁子树时 Runtime 可经 ``_nodes`` 查表定位，
      也避免对象图环引用（关闭时 Runtime 主动销毁子树，整树自然回收）。

    .. rubric:: 使用示例

    .. code-block:: python

        def wire(node: ProvideNode) -> None:
            node.provide("locale", "zh")
            assert isinstance(node, ProvideNode)   # runtime_checkable

    .. rubric:: 行为规约

    - ``provide`` 同 key 重复注册 = **覆盖更新**（后者生效；``inject``
      实时查找不缓存，更新即刻可见——spec-draft §13.4，S-37 裁决）。
    - ``inject`` 语义 = 委托 ``inject_from(runtime, self, key)`` 线性上溯；
      Runtime 自身的 ``inject`` 即链终点查找。
    - 非行为：协议不要求节点实现生命周期方法（``destroy`` 等），那些在各实现类上。

    .. rubric:: 测试案例

    - 前置：``isinstance(runtime, ProvideNode)`` / ``isinstance(agent, ProvideNode)`` /
      ``isinstance(workflow, ProvideNode)`` → 期望：均为 ``True``。

    .. rubric:: 调用关系（审计）

    - 被调：无（协议，仅作类型标注与 ``isinstance`` 检查，见本类测试案例）
    - 实例化方：无（Protocol 不实例化；实现类为 ``flowing.runtime.Runtime`` / ``flowing.agent.Agent`` / ``flowing.plugins.workflow.Workflow``）

    .. seealso:: :func:`inject_from`、
        :class:`flowing.runtime.Runtime`、:class:`flowing.agent.Agent`、
        :class:`flowing.plugins.workflow.Workflow`
    """

    node_id: str
    """共享 ID 空间中的节点标识（``runtime-0`` / ``workflow-<uuid>`` /
    ``agent-<uuid>``）；看 ID 即知类型，防跨类型碰撞，使 ``Runtime._nodes``
    可用单个 dict 索引。参见 :meth:`flowing.runtime.Runtime.get_node`。
    """
    _provided: dict[str, Any]
    """节点级 provide 存储；key **始终是 ``str``**（``InjectionKey[T]`` 仅是编译期
    类型标注，类型信息不跨节点传递）。参见 :func:`inject_from`。
    """
    runtime: "Runtime"
    """返指所属 Runtime（``get_node`` 查表宿主）。实现类的 ``inject`` 委托
    语义依赖本属性——纳入协议面使类型检查可暴露缺失（S-12 裁决）。Runtime
    自身的 ``runtime`` 自指：自指即「inject 链终点」的协议面表达。这是
    「节点间不持父对象引用」的唯一例外——返指根不构成环（Runtime 无父）。
    （``TYPE_CHECKING`` 前向引用：叶子模块不反向依赖 ``flowing.runtime``，
    S-43 裁决③。）
    """

    def provide(self, key: str, value: Any) -> None:
        """在本节点的 ``_provided`` 注册 ``key → value``。

        功能与动机：inject 链的供方入口；运行时注入值（服务实例、locale、trace
        adapter 等）沿子树对后代节点可见。

        行为边界：同 key 重复 provide = 覆盖更新（后者生效，spec-draft
        §13.4；``inject`` 实时查找不缓存即刻可见）；value 任意对象；**敏感信息（API key /
        凭证）不进 ``_provided``**（安全边界，见 ``flowing.runtime`` 模块
        docstring 与 ``flowing.model``）。

        .. rubric:: 调用关系（审计）

        - 调用：无（协议签名）
        - 被调：无（协议成员；实现见 ``flowing.runtime.Runtime.provide`` / ``flowing.agent.Agent.provide`` / ``flowing.plugins.workflow.Workflow.provide``）

        .. seealso:: :func:`inject_from`
        """
        ...

    def inject(self, key: str) -> Any:
        """沿 provide 链（子 → 父）线性上溯查找 ``key``，命中即返回。

        功能与动机：消费方入口；委托 ``inject_from(self.runtime, self, key)``。
        对 Agent/Workflow 而言生命周期父节点与 provide 源节点天然同一（``_parent_id``
        双重用途）。

        行为边界：上溯至 Runtime 终点仍未命中 → 抛 ``MissingProvideError``（运行时
        动态场景的兜底；静态依赖缺失在 ``use()`` 时经 ``_check_dependencies`` 警告、成环抛错，R9）。

        :raises flowing.errors.MissingProvideError: 链终点未命中时。

        .. rubric:: 调用关系（审计）

        - 调用：``inject_from()``（时机：每次 inject 委托，见本协议行为规约）
        - 被调：无（协议成员；实现见 ``flowing.runtime.Runtime.inject`` / ``flowing.agent.Agent.inject`` / ``flowing.plugins.workflow.Workflow.inject``）

        .. seealso:: :func:`inject_from`
        """
        ...


def inject_from(runtime: "Runtime", node: ProvideNode, key: str) -> Any:
    """inject 统一线性上溯算法（模块级函数，三类 ``ProvideNode`` 共用）。

    .. rubric:: 功能介绍

    从 ``node`` 起沿 ``_parent_id`` 链逐级查 ``_provided``，命中即返回；
    Runtime 是链终点（无 ``_parent_id``），终点未命中抛 ``MissingProvideError``。

    .. rubric:: 设计动机

    单一算法、三处委托：``Agent.inject`` / ``Workflow.inject`` →
    ``inject_from(self.runtime, self, key)``；``Runtime.inject`` →
    ``inject_from(self, self, key)``。ProvideNode 协议只统一类型签名，不复制查找逻辑。

    .. rubric:: 使用示例

    .. code-block:: python

        # 等价伪码（算法本体）
        current = node
        while current is not None:
            if key in current._provided:
                return current._provided[key]
            parent_id = getattr(current, "_parent_id", None)
            current = runtime.get_node(parent_id) if parent_id else None
        raise MissingProvideError(key)

    .. rubric:: 行为规约

    - 线性查找，先近后远：本节点命中即返回，不再上溯。
    - 中间节点 id 在 ``_nodes`` 中不存在（父已销毁）时按链断裂处理：继续上溯
      无意义，视为未命中路径，最终抛 ``MissingProvideError``。
    - 非行为：不做缓存（每次现场求值）、不做类型校验（key 是 ``str``，
      ``InjectionKey[T]`` 的类型参数不跨节点传递）。
    - **终点可达性的结构保证**（S-12 衍生裁决）：① Runtime 自注册为
      ``_nodes`` 首条目（``get_node("runtime-0")`` 可查）；② 根节点的
      ``_parent_id`` 指向 Runtime 的 ``node_id``。两者缺一，下方「命中
      Runtime 层值」的测试案例即不成立。``None`` 分支只在 ``current``
      是 Runtime 自身时触发（``getattr`` 兜底，终点无 ``_parent_id``），
      父已销毁走 ``KeyError`` 分支——两种链终止情形各占一个分支。
    - **内部 API 级别说明**：本函数是公开算法但主要面向框架与扩展作者；
      应用代码应走 ``node.inject(key)``。

    :param runtime: 链终点所在的 Runtime（提供 ``get_node`` 查表）。
    :param node: 上溯起点节点。
    :param key: provide key（字符串）。
    :return: 命中值。
    :raises flowing.errors.MissingProvideError: 上溯至终点仍未命中，携带 ``key``。

    .. rubric:: 测试案例

    - 前置：Runtime provide ``locale``、子 Agent 未 provide → 操作：
      ``inject_from(runtime, agent, "locale")`` → 期望：命中 Runtime 层值。
    - 前置：子 Agent 与 Runtime 都 provide 同 key → 期望：命中子 Agent 层（就近）。
    - 前置：全链无 key → 期望：``MissingProvideError``。

    .. rubric:: 调用关系（审计）

    - 调用：``flowing.runtime.Runtime.get_node()``（时机：沿 ``_parent_id`` 链逐级上溯查表，见本函数「等价伪码」）
    - 被调：``flowing.agent.Agent.inject``（每次 inject 委托）、``flowing.plugins.workflow.Workflow.inject``（每次 inject 委托）、``flowing.runtime.Runtime.inject``（每次 inject，等价 ``inject_from(self, self, key)``）

    .. seealso:: :class:`ProvideNode`、
        :meth:`flowing.runtime.Runtime.get_node`、
        :exc:`flowing.errors.MissingProvideError`
    """
    current: ProvideNode | None = node
    while current is not None:
        if key in current._provided:
            return current._provided[key]   # 先近后远：本节点命中即返回，不再上溯
        parent_id = getattr(current, "_parent_id", None)
        if parent_id is None:
            break   # Runtime 是链终点（无 _parent_id）
        try:
            current = runtime.get_node(parent_id)
        except KeyError:
            break   # 父已销毁：按链断裂处理，视为未命中路径
    raise MissingProvideError(key)
