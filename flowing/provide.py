"""flowing.provide —— provide-inject 依赖注入链的节点协议与统一上溯查找算法。

.. rubric:: 功能介绍

本模块定义框架依赖注入（provide / inject）机制的两个基础符号：
:class:`ProvideNode` 是链上节点的协议，:func:`inject_from` 是从任一节点
沿亲代链向根查找注入值的统一算法。框架内 Runtime（链终点）、Workflow、
Agent 三类节点都实现 :class:`ProvideNode`；使用者一般经节点方法提供与
取值（``node.provide(key, value)`` / ``node.inject(key)``），不需要直接
调用 :func:`inject_from`。

.. rubric:: 行为要点

- 查找顺序：从当前节点沿亲代链逐级向根查找，先近后远，本节点命中即
  返回；查找到根（Runtime）仍未命中抛
  :exc:`flowing.errors.MissingProvideError`。
- 同 key 重复 ``provide`` 是覆盖更新：查找实时进行、不缓存，更新
  即刻对后续 ``inject`` 可见。
- 安全边界：API key、凭证等敏感信息禁止放进节点的注入存储——注入值
  沿链对后代节点可见。

.. seealso:: :class:`ProvideNode`、:func:`inject_from`、
    :mod:`flowing.runtime`、:class:`flowing.params.InjectionKey`
"""

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from flowing.errors import MissingProvideError

if TYPE_CHECKING:
    from flowing.runtime import Runtime

__all__ = ["ProvideNode", "inject_from"]


@runtime_checkable
class ProvideNode(Protocol):
    """provide-inject 链上的节点协议（``@runtime_checkable``）：Runtime、Workflow、Agent 三类节点共用。

    .. rubric:: 功能介绍

    统一「可 provide / 可 inject」的节点签名。框架内
    :class:`flowing.runtime.Runtime` （链终点）、:class:`flowing.agent.Agent`
    与 ``flowing.plugins.workflow.Workflow`` 都实现本协议。协议带
    ``@runtime_checkable``，可以用 ``isinstance(node, ProvideNode)`` 在
    运行时检查一个对象是否具备协议要求的成员（``node_id`` / ``_provided`` /
    ``runtime`` 三个属性与 ``provide`` / ``inject`` 两个方法）。

    .. rubric:: 使用示例

    .. code-block:: python

        def wire(node: ProvideNode) -> None:
            node.provide("locale", "zh")
            assert isinstance(node, ProvideNode)   # runtime_checkable

    .. rubric:: 行为要点

    - ``provide``：在本节点注册注入值；同 key 重复注册是覆盖更新
      （后者生效），对 ``inject`` 立即可见（查找实时、不缓存）。
    - ``inject``：沿亲代链向根查找注入值，语义由 :func:`inject_from`
      实现；未命中抛 :exc:`flowing.errors.MissingProvideError`。
    - 协议面不包含生命周期方法（如 ``destroy``）：那些属于各实现类。

    .. seealso:: :func:`inject_from`、
        :class:`flowing.runtime.Runtime`、:class:`flowing.agent.Agent`、
        ``flowing.plugins.workflow.Workflow``
    """

    node_id: str
    """节点在共享 ID 空间中的唯一标识。取值形如 ``runtime-0`` /
    ``workflow-<uuid>`` / ``agent-<uuid>``，前缀即节点类型；Runtime 的
    ``node_id`` 恒为 ``runtime-0``。可用
    :meth:`flowing.runtime.Runtime.get_node` 按 ID 查回节点。
    """
    _provided: dict[str, Any]
    """节点的注入值存储。键始终是字符串（``InjectionKey[T]`` 只是编译期
    类型标注，类型信息不跨节点传递）。本字段是协议成员，实现类必须
    提供；读写请经 ``provide`` / ``inject`` 方法，不要直接操作本字段。
    """
    runtime: "Runtime"
    """节点所属的 Runtime（provide-inject 链的终点）。Agent / Workflow
    用它找到链终点；Runtime 自身的 ``runtime`` 指向自己，以此表达
    「本节点就是链终点」。
    """

    def provide(self, key: str, value: Any) -> None:
        """在本节点注册一个注入值，供本节点及其后代节点经 ``inject`` 取用。

        :param key: 注入值的键（字符串）。同 key 重复调用是覆盖更新，
            后者生效。
        :param value: 要注入的任意对象。

        .. rubric:: 行为要点

        - 查找是实时的：``provide`` 更新后，后续 ``inject(key)`` 立即
          取到新值，不做缓存。
        - 安全边界：API key、凭证等敏感信息禁止放进注入存储——注入值
          沿链对后代节点可见，可能被链上任一节点读取。

        .. seealso:: :func:`inject_from`
        """
        ...

    def inject(self, key: str) -> Any:
        """沿亲代链向根查找 ``key`` 对应的注入值，找到即返回。

        :param key: 注入值的键（字符串）。
        :return: 找到的注入值。
        :raises flowing.errors.MissingProvideError: 沿链查找到终点
            （Runtime）仍未命中时。

        .. rubric:: 行为要点

        查找委托 :func:`inject_from` 实现：从本节点开始逐级向亲节点
        查找，先近后远，本节点命中即返回。运行期动态取值未命中由本
        异常兜底；启动期插件的静态依赖校验走
        :exc:`flowing.errors.DependencyError`，两者互补。

        .. seealso:: :func:`inject_from`
        """
        ...


def inject_from(runtime: "Runtime", node: ProvideNode, key: str) -> Any:
    """从 ``node`` 开始沿亲代链向根查找注入值，找到即返回。

    .. rubric:: 功能介绍

    统一的链式查找算法，供 :class:`ProvideNode` 的实现类共用：从
    ``node`` 出发逐级沿亲代链检查每个节点的注入存储，本节点命中即返回；
    Runtime 是链终点，查找到终点仍未命中则抛
    :exc:`flowing.errors.MissingProvideError`。框架内 ``Agent.inject`` /
    ``Workflow.inject`` / ``Runtime.inject`` 都委托本函数实现；应用代码
    一般直接调用 ``node.inject(key)``，不需要直接使用本函数。

    :param runtime: 链终点所在的 Runtime，提供按节点 ID 查找节点的能力。
    :param node: 查找起点节点。
    :param key: 注入值的键（字符串）。
    :return: 找到的注入值。
    :raises flowing.errors.MissingProvideError: 沿链查找到终点仍未命中时。

    .. rubric:: 行为要点

    - 先近后远：本节点命中即返回，不再上溯；同一 key 在近处与根都有
      值时，取近处的值。
    - 查找实时进行、不做缓存：``provide`` 更新后，下一次查找立即可见。
    - 亲代链断裂按未命中处理：中间节点的亲节点已销毁、无法继续上溯时，
      最终抛 :exc:`flowing.errors.MissingProvideError`；即使上方链断裂，
      本节点自身已注册的值仍可命中。

    .. seealso:: :class:`ProvideNode`、
        :exc:`flowing.errors.MissingProvideError`、
        :meth:`flowing.runtime.Runtime.get_node`
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
            break   # 亲节点已销毁：按链断裂处理，视为未命中路径
    raise MissingProvideError(key)
