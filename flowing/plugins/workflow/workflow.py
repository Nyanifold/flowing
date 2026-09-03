"""``flowing.plugins.workflow`` —— 工作流编排扩展。

.. rubric:: 功能介绍

本扩展提供「规则性编排」能力：编排逻辑（分支、循环、并行、串行）由
Python 代码显式写出，与 goal mode（LLM 按目标自主决定路径）是两种正交
的编排方式。Workflow 适用于流程已知、要确定性、可重复的场景——审批流、
验证-修复循环、固定步骤流水线。使用时继承 :class:`Workflow` 实现
``run()`` （编排主体），把编排要用的子 Agent、工具、消息能力接入运行
时，然后经代码直接驱动或 ``run-workflow`` 工具（LLM 入口）拉起。

边界（本扩展不提供）：

- 不是 Agent：没有 Turn 循环、不调用 LLM、不产生消息树节点（但可以
  创建并驱动 Agent）。
- 不是声明式 DSL：workflow 定义是 Python 文件（类形态或函数形态，见
  :func:`resolve_workflow`），不解析 mermaid / 图声明。

.. rubric:: 注册面清单

- 启用方式：``runtime.use(WorkflowPlugin())`` 把 ``run-workflow`` 工具
  注册进全局工具注册表，之后 Agent 经 ``add_tool("run-workflow")``
  绑定该工具，即可让 LLM 按路径拉起任意 workflow 定义。本扩展没有
  实例级 ``use_workflow(self)``：``Workflow`` 基类与
  :func:`resolve_workflow` import 即用，不需要对 Agent 做任何启用动作。
  重复安装同名插件（再次 ``runtime.use(WorkflowPlugin())``）抛
  ``ValueError``。
- 注册的资源：安装时向 ``runtime.tool_registry`` 注册
  :class:`RunWorkflowTool` （:class:`flowing.tool.ScriptTool` 子类，
  规范名 ``run-workflow``，落在 ``default::`` 命名空间）。不注册
  provide 值、不声明 Agent 状态键、不挂 prompt 块。
- 声明的钩子点：无（本扩展不声明新的钩子点）。Workflow 实例有自己的
  钩子注册表（``self.hooks``，构造时创建），使用框架预填的核心钩子点
  ``before_tool_call`` / ``after_tool_call``——与任何 Agent 的钩子完全
  独立，Agent 侧挂的 handler 不会在 workflow 的工具调用上触发。
- 挂载的钩子：无（插件本身不挂任何 handler）。
- 未启用时的行为：不安装插件则 ``run-workflow`` 工具不存在，LLM 无法
  经工具拉起 workflow；但 ``Workflow`` 基类与 ``resolve_workflow`` 照常
  可用（代码直接驱动路径不受影响），未启用不产生任何开销。

.. rubric:: 对象图中的位置与生命周期

Workflow 实例同时占据两个位置：

- 节点树（``runtime._nodes`` 共享 ID 空间）：构造时注册，``node_id``
  取 ``workflow-*`` 前缀。可作根节点（``caller=None``，``_parent_id``
  指向 Runtime——代码直接拉起的顶层编排），也可作 Agent 的子节点
  （``_parent_id = caller.node_id``——LLM 经 ``run-workflow`` 工具
  拉起）。本 workflow 创建的 Agent 的 ``parent_id`` 指向本 workflow 的
  ``node_id``：生命周期上 Workflow 是它们的亲节点，``destroy()`` 时
  级联归档（从池与名录移除、session 文件保留）。
- provide/inject 链：本 workflow ``provide()`` 的值对它创建的全部
  Agent 及后代可见；``inject()`` 沿 ``_parent_id`` 链上溯（workflow →
  caller → Runtime）。注意边界：workflow 调用工具 / 创建子 Agent 时
  参数由编排代码直接填，不走 inject 式填充。

运行状态不持久化：每次运行拉起一个新对象，崩溃后不承诺续跑（``_nodes``
中的 workflow 节点记录随 Runtime 退出即失效）。

.. rubric:: 驱动 Agent 与工具

Workflow 驱动子 Agent 的唯一入口是 :meth:`Workflow.create_agent`
（字符串类型名，委托 ``runtime.create_agent``），创建后直接
``message()`` / ``query()``。没有 ``invoke_subagent``——那是 Agent 侧
「LLM 唤起 + SubagentEntry 解析 + 钩子 + 池生命周期」的包装，workflow
的编排代码自己就是包装层；workflow 路径也不经过
``before/after_subagent_invoke`` 钩子（创建管线只有 ``before_create`` /
``after_create``）。并行无需专门 API——``asyncio.gather`` 即原语。

Workflow 的工具调用是 :meth:`Workflow.tool_call` （规范名 + 零散参数），
走 Workflow 自己的钩子（``self.hooks`` 的 ``before_tool_call`` /
``after_tool_call``），与任何 Agent 的钩子完全独立——Agent 侧挂的审批 /
安全 / 审计 handler 不会在 workflow 的工具调用上触发。

.. rubric:: 反向驱动 caller 与异步防死锁

Workflow 可以反向驱动发起它的 Agent（``await self.caller.query(...)``
把消息发进 caller 的会话并等待其回合产物）——这是刻意保留的能力，代价
是异步防死锁规则：workflow 经 ``run-workflow`` 工具拉起时一定异步
（后台任务 + 立即返回收据，见 :class:`RunWorkflowTool`）。死锁成因：
caller 的工作循环串行，当前逻辑 Turn（正在执行 ``run-workflow`` 工具）
完成前不消费新消息；若工具同步等待 workflow 完成，双方互相等待。同步
执行只允许「workflow 不反向调用 caller」的场景，由调用方自行保证。

.. rubric:: 使用示例

.. code-block:: python

    from flowing.plugins.workflow import Workflow, WorkflowPlugin

    runtime.use(WorkflowPlugin())          # 阶段一：注册 run-workflow 工具

    class VerifyFixWorkflow(Workflow):
        \"\"\"运行检查器，修复失败的内容，重复直到通过或两轮无进展。\"\"\"

        async def run(self, prompt: str | None = None, max_rounds: int = 3) -> dict:
            no_progress = 0
            for round_no in range(max_rounds):
                verifier = await self.create_agent("verifier-agent")
                result = await verifier.query("运行 tsc --noEmit 并列出所有错误")
                if result.status == "completed" and "error" not in result.final_text:
                    return {"status": "passed", "rounds": round_no + 1}
                fixer = await self.create_agent("fixer-agent")
                fix_result = await fixer.query("修复以上错误")
                await fixer.destroy()
                if "无进展" in fix_result.final_text:
                    no_progress += 1
                    if no_progress >= 2:
                        return {"status": "stalled"}
            return {"status": "failed", "rounds": max_rounds}

    # 代码直接驱动（根节点：caller=None）
    wf = VerifyFixWorkflow(caller=None, runtime=runtime)
    result = await wf.run(prompt="检查并修复")

.. seealso::

    - :mod:`flowing.runtime` —— ``create_agent`` / ``_nodes`` /
      provide-inject 链。
    - :mod:`flowing.agent` —— ``query()`` / ``side_query()`` /
      ``destroy()`` / ``TurnResult``。
    - :mod:`flowing.tool` —— ``Tool`` / ``ToolCall`` / ``ToolResult`` /
      ``ToolRegistry``。
    - :mod:`flowing.plugins.skills` —— 同构的「插件提供能力」扩展形态。
    - :mod:`flowing.errors` —— ``ToolNotFoundError`` / ``Intercepted``。
"""

from abc import ABC, abstractmethod
from typing import Any
from uuid import uuid4

from flowing.agent import Agent
from flowing.errors import Intercepted
from flowing.hooks import HookRegistry
from flowing.params import InjectionKey
from flowing.provide import inject_from
from flowing.runtime import Runtime
from flowing.tool import ToolCall, ToolResult, normalize_output



class Workflow(ABC):
    """Workflow 抽象基类——用户继承并实现 ``run()`` 的一次编排载体。

    .. rubric:: 功能介绍

    继承本类并实现 :meth:`run` （编排主体），然后以
    ``MyWorkflow(caller, runtime)`` 构造实例并 ``await instance.run(...)``
    驱动，或经 ``run-workflow`` 工具（LLM 入口）按定义文件路径拉起。
    构造时实例在节点树与 provide 链上就位（见模块 docstring「对象图中的
    位置与生命周期」）。实例拥有与 Agent 同构但完全独立的装备：``hooks``
    （自己的 ``HookRegistry``，供工具调用拦截与观察）、``provide`` /
    ``inject`` （provide 链上的一环）、``create_agent`` / ``tool_call``
    （驱动子 Agent 与工具的入口）、``caller`` （发起方，可反向驱动）。

    .. rubric:: 使用示例

    .. code-block:: python

        from flowing.plugins.workflow import Workflow

        class VerifyFixWorkflow(Workflow):
            \"\"\"运行检查器，修复失败的内容，重复直到通过或两轮无进展。\"\"\"

            async def run(self, prompt: str | None = None, max_rounds: int = 3) -> dict:
                no_progress = 0
                for round_no in range(max_rounds):
                    verifier = await self.create_agent("verifier-agent")
                    result = await verifier.query("运行 tsc --noEmit 并列出所有错误")
                    if result.status == "completed" and "error" not in result.final_text:
                        return {"status": "passed", "rounds": round_no + 1}
                    fixer = await self.create_agent("fixer-agent")
                    fix_result = await fixer.query("修复以上错误")
                    await fixer.destroy()
                    if "无进展" in fix_result.final_text:
                        no_progress += 1
                        if no_progress >= 2:
                            return {"status": "stalled"}
                return {"status": "failed", "rounds": max_rounds}

        # 构造并驱动（caller 为 None 时是根节点；代码直接驱动路径）
        wf = VerifyFixWorkflow(caller=None, runtime=runtime)
        result = await wf.run(prompt="检查并修复")

    .. rubric:: 行为要点

    - 实例只能经「``Workflow`` 子类构造（``caller`` + ``runtime``）」创建；
      ``__init__`` 是同步方法（节点注册是结构操作）。每次运行拉起一个新
      对象：workflow 无跨运行状态（运行状态不持久化，崩溃不续跑）。
    - ``node_id`` 构造时分配（``workflow-*`` 前缀）并注册进
      ``runtime._nodes``，生命周期内不变；``_parent_id`` 为
      ``caller.node_id`` （``caller=None`` 时为 Runtime 节点 id）。
    - ``caller=None`` （根节点 workflow）时，一切 ``self.caller.*`` 调用
      都是对 ``None`` 取属性，抛 ``AttributeError``——框架不预设检查，
      编排代码自行保证。
    - ``provide`` 的值对本 workflow 创建的全部 Agent 及后代可见，对其它
      workflow 的子树不可见。
    - Workflow 不进消息队列、不消费消息、没有 Turn 循环、不调用 LLM。

    .. seealso::

        - :class:`flowing.plugins.workflow.RunWorkflowTool` —— LLM 触发
          入口（异步规则）。
        - :func:`flowing.plugins.workflow.resolve_workflow` —— 按路径
          解析 workflow 定义。
        - :class:`flowing.runtime.ProvideNode` —— provide/inject 协议。
    """

    caller: Agent | None
    """发起本 workflow 的 Agent；根节点 workflow（``caller=None``）为
    ``None``。可经 ``caller.query(...)`` 反向驱动发起方（见模块 docstring
    「反向驱动 caller 与异步防死锁」），或调用 caller 的其它公开方法。
    """
    runtime: Runtime
    """所属 Runtime（对象图根）。创建子 Agent、查工具注册表都经它；与
    ``caller.runtime`` 恒为同一实例（``caller`` 非 None 时）。
    """
    node_id: str
    """节点树 ID（``workflow-*`` 前缀），构造时分配并注册进
    ``runtime._nodes``；本 workflow 创建的 Agent 的 ``parent_id`` 指向它。
    """
    hooks: HookRegistry
    """Workflow 自己的钩子注册表（实例级，构造时创建）。与任何 Agent 的
    ``hooks`` 完全独立——Agent 侧挂的 handler 不会在 workflow 的工具
    调用上触发，反之亦然。``tool_call()`` 使用 ``before_tool_call`` /
    ``after_tool_call`` 两个核心钩子点。
    """
    _provided: dict[str, Any]
    """Workflow 级 provide 表（provide 链上的一环）。内部 API，不属稳定
    契约。
    """
    _parent_id: str
    """provide 链上溯目标（``caller.node_id`` 或 Runtime 节点 id）。
    内部 API，不属稳定契约。
    """
    _agents: dict[str, Agent]
    """本 workflow 创建且未销毁的子 Agent 表（node_id → 实例），
    ``destroy()`` 级联的依据。内部 API，不属稳定契约。
    """

    def __init__(self, caller: Agent | None, runtime: Runtime) -> None:
        """构造 workflow 实例并就位于节点树与 provide 链（同步方法）。

        .. rubric:: 行为要点

        - 构造时分配 ``node_id`` （``workflow-*`` 前缀）、创建独立的
          ``hooks``、初始化内部 provide 表与子 Agent 表、设定
          ``_parent_id`` （``caller.node_id`` 或 Runtime 节点 id）并注册
          进 ``runtime._nodes``。
        - 本方法不启动任何任务（``run()`` 由调用方驱动），不解析任何
          workflow 定义文件（实例化的是已解析的类）。
        - ``caller`` 为 None 时本实例是根节点 workflow；非 None 时
          ``runtime`` 应与 ``caller.runtime`` 一致（不一致属编程错误，
          框架不做结构强制）。
        - 子类覆写本方法时必须调用 ``super().__init__(caller, runtime)``
          并保持同步（节点注册是结构操作）。

        :param caller: 发起本 workflow 的 Agent；根节点 workflow 传
            ``None``。
        :param runtime: 所属 Runtime（与 ``caller.runtime`` 同一实例）。

        .. seealso:: :meth:`run`、:meth:`destroy`
        """
        self.caller = caller
        self.runtime = runtime
        self.node_id = f"workflow-{uuid4()}"  # node_id 取 workflow-* 前缀（与 ToolCall id 同族的 <来源类型名>-<uuid4> 形态）
        self.hooks = HookRegistry()  # 实例级独立钩子注册表（与任何 Agent 的 hooks 完全独立）
        self._provided = {}
        self._agents = {}
        self._parent_id = caller.node_id if caller is not None else runtime.node_id  # 根节点 workflow 指向 Runtime
        runtime._nodes[self.node_id] = self  # 创建即注册（_nodes: dict[str, ProvideNode]——Workflow 可作亲节点/根节点）

    @abstractmethod
    async def run(self, prompt: str | None = None, **kwargs: Any) -> dict[str, Any] | None:
        """编排逻辑主体——子类必须实现。

        .. rubric:: 功能介绍

        全部编排逻辑（创建/复用/销毁子 Agent、调用工具、反向驱动 caller、
        并行 gather、分支循环）都写在这里。参数由调用方传入，可含自然语言
        ``prompt`` （通常作为子 Agent 的任务描述转发）。

        .. rubric:: 行为要点

        - 返回约定：返回 ``dict`` 表示本次运行的结果；返回 ``None`` 表示
          只有编排副作用、没有结果。代码直接调用时，返回值就是
          ``await`` 的返回值。
        - 工具拉起时，本方法的返回值不进入收据与完成消息（收据与完成
          消息由 :class:`RunWorkflowTool` 固定生成，见其 docstring）；
          workflow 若要向 caller 交付结果，用 ``caller.enqueue_message(...)``
          自行投递。
        - 框架不限制运行时长、节点数、并发数；不捕获本方法抛出的异常——
          异常终止本次运行并向触发方传播（工具拉起路径上体现为后台任务
          失败，框架不做自动重试）。
        - 重复调用同一实例的 ``run()`` 属编程错误（每次运行应拉起新对象）；
          运行中 Runtime shutdown → 本方法内创建的子 Agent 随
          ``destroy()`` 级联归档，未完成的 ``await`` 收到取消。

        :param prompt: 可选的自然语言任务描述，通常转发给子 Agent 作为
            任务提示词。
        :param kwargs: 其余运行参数，由调用方传入（经 ``run-workflow``
            工具拉起时即 LLM 传的参数，见 :class:`RunWorkflowTool`）。
        :return: workflow 结果（``dict``）或 ``None`` （纯编排副作用）。

        .. seealso:: :meth:`create_agent`、:meth:`tool_call`
        """
        ...

    async def create_agent(self, agent_type: str, **kwargs: Any) -> Agent:
        """创建子 Agent——Workflow 驱动 Agent 的唯一入口。

        .. rubric:: 功能介绍

        委托 ``runtime.create_agent(agent_type, parent_id=self.node_id, **kwargs)``，
        走完整创建管线：``__init__`` → ``before_create`` →
        ``setup`` → PENDING 检查 → 池元数据写盘 → ``_nodes`` 注册 →
        ``after_create`` → 工作循环启动。创建即入 agent 池；本 workflow
        把实例记入内部子 Agent 表，供 ``destroy()`` 级联归档。

        .. rubric:: 使用示例

        .. code-block:: python

            reviewer = await self.create_agent("reviewer-agent")
            r1 = await reviewer.query("审查 auth 模块")
            r2 = await reviewer.query("审查 payment 模块")   # 同一实例，上下文延续

        .. rubric:: 行为要点

        - 返回的 Agent 已可立即 ``message()`` / ``query()``；其
          ``inject()`` 上溯链经本 workflow（本 workflow ``provide()`` 的
          值对它可见）。
        - 不经 ``SubagentEntry.resolve()``，不触发
          ``before/after_subagent_invoke`` 钩子（创建路径只有
          ``before_create`` / ``after_create``）；``kwargs`` 不做
          inject 式填充——原样传给子 Agent 的 ``setup()``。
        - ``agent_type`` 无法解析 → 创建管线的解析异常上抛；同名类型
          重复创建产生多个独立实例（无去重——命名复用靠调用方持引用，
          不靠类型名）。

        :param agent_type: 子 Agent 的类型名（字符串，与
            ``runtime.create_agent`` 同一契约）。
        :param kwargs: 传给子 Agent ``setup()`` 的参数。
        :return: 创建完成的 ``Agent`` 实例。

        .. seealso::

            - :meth:`flowing.runtime.Runtime.create_agent` —— 底层创建
              路径。
            - :meth:`flowing.agent.Agent.invoke_subagent` —— Agent 侧的
              LLM 唤起包装（Workflow 刻意不提供）。
        """
        agent = await self.runtime.create_agent(agent_type, parent_id=self.node_id, **kwargs)
        self._agents[agent.node_id] = agent  # 记入级联销毁表（destroy() 的依据）
        return agent  # 已可立即 message()；inject 上溯链经本 workflow

    async def tool_call(self, tool_name: str, **args: Any) -> ToolResult:
        """Workflow 自己的工具调用——规范名直查注册表，独立钩子。

        .. rubric:: 功能介绍

        与 ``Agent.tool_call`` 同名但签名与语义不同：接收规范名 + 零散
        参数，直接 ``runtime.tool_registry.get(tool_name)`` 查全局注册表，
        构造 ``ToolCall`` 后经本实例的 ``hooks`` 走 ``before_tool_call``
        / ``after_tool_call``，最后以 ``caller=None`` 调度工具。因为是
        编排代码的直接动作（不是 LLM 决策），本路径没有别名、``ToolEntry``
        覆写、参数聚合与 inject 填充——``args`` 就是全部入参；也不触发
        caller 或任何 Agent 的钩子（Agent 侧挂的审批 / 安全 / 审计
        handler 不会在本路径上触发）。

        .. rubric:: 使用示例

        .. code-block:: python

            async def run(self, prompt: str | None = None) -> dict:
                # workflow 自己的审计钩子（_audit_call 为子类里定义的
                # 两参 handler：(wf, call)）
                self.hooks.before_tool_call(self._audit_call, by="wf-audit")
                diff = await self.tool_call("read-file", path="src/auth.py")
                return {"diff": diff.output}

        .. rubric:: 行为要点

        - 调用时序：``tool_registry.get(tool_name)`` 命中 → 构造
          ``ToolCall(id=f"workflow-{uuid4()}", name=tool_name, args=args)``
          （id 由框架生成，仅作追踪）→ dispatch ``before_tool_call``
          （value 为 ``ToolCall``；handler 可改写 ``args``、可设置
          ``shortcut`` 直接给出结果、可 ``raise Intercepted`` 阻断——
          阻断时返回 ``ToolResult.blocked(...)``，工具本体不执行）→
          调度工具 → dispatch ``after_tool_call`` （value 为
          ``ToolResult``，可改写；``raise Intercepted`` 时返回 ``blocked``）
          → 收尾归一（return 前幂等再跑一次 ``normalize_output``，封
          shortcut 与 after 改写两条缝）→ 返回最终 ``ToolResult``。
        - 返回值 ``status`` 四值语义与 Agent 路径一致（``completed`` /
          ``pending`` / ``blocked`` / ``error``）；``output`` 恒为归一化
          五形态之一（与 ``Agent.tool_call`` 同一不变量：「出 tool_call
          的 ``ToolResult.output`` 恒已归一」）。
        - 不查别名、不查 ``_tool_entries``、不做参数聚合；``tool_name``
          未注册 → :class:`flowing.errors.ToolNotFoundError`。
        - 工具声明了 ``caller`` 参数时收到 ``None``——依赖 caller 的工具
          在 workflow 路径上不可用（工具作者责任，框架不预设检查）。

        :param tool_name: 工具规范名（注册表键）。
        :param args: 工具参数，原样作为 ``ToolCall.args``。
        :return: 工具执行结果。
        :raises flowing.errors.ToolNotFoundError: ``tool_name`` 未注册时。

        .. seealso::

            - :meth:`flowing.agent.Agent.tool_call` —— Agent 侧同名方法
              （``ToolCall`` 入参 + 别名查找 + 参数聚合）。
            - :class:`flowing.tool.ToolRegistry`、:class:`flowing.tool.ToolResult`
        """
        tool = self.runtime.tool_registry.get(tool_name)  # 规范名直查；未注册 -> ToolNotFoundError

        # id 由框架生成（编程路径 id 形如 <来源类型名>-<uuid4>，仅作追踪）
        call = ToolCall(id=f"workflow-{uuid4()}", name=tool_name, args=args)  # -> flowing.tool.ToolCall
        try:
            call = await self.hooks.before_tool_call.dispatch(  # Workflow 自己的钩子，与 Agent 侧完全独立
                self, call,
            )  # handler 可改写 args / shortcut 短路
        except Intercepted as exc:
            # 硬阻断：工具本体不执行，after_tool_call 不触发（与 Agent.tool_call 同一出口）
            return ToolResult.blocked(reason=str(exc))
        if call.shortcut is not None:
            result = call.shortcut  # 协商短路：handler 提供的 ToolResult 直接作为本次结果（产物未经 __call__ 归一，由收尾归一兜底）
        else:
            result = await tool(call.args, caller=None)  # ToolCall→dict 解包点 = call.args 直取（无 _normalize 等价物）；caller 恒 None
        try:
            result = await self.hooks.after_tool_call.dispatch(self, result)  # value 为 ToolResult，可改写（含改写 result.output 为原料——由收尾归一兜底）
        except Intercepted as exc:
            result = ToolResult.blocked(reason=str(exc))   # after_tool_call 拦截 → blocked 结果返回（与 Agent.tool_call 同一塑形）
        result.output = await normalize_output(result.output)  # 收尾归一：return 前幂等再跑一次；与 Agent.tool_call 同一不变量——出 tool_call 的 output 恒为五形态之一
        return result  # status 四值语义与 Agent 路径一致

    def provide(self, key: str | InjectionKey[Any], value: Any) -> None:
        """声明 provide 值——对本 workflow 创建的全部 Agent 及后代可见。

        .. rubric:: 行为要点

        - 写入本 workflow 的 provide 表；之后创建的子 Agent（及其子孙）
          ``inject(key)`` 沿链上溯时命中本层。
        - 同 key 重复 provide → 后者覆盖前者（与 ``Agent.provide`` 一致）；
          对已创建的子 Agent 同样生效（inject 是使用时上溯，不是创建时
          拷贝）。
        - 不影响 caller 与其他 workflow 的子树；不参与工具 / 子 Agent
          调用的参数填充（编排代码直接传参）。

        :param key: 注入键（字符串，或 ``InjectionKey``——按键名归一）。
        :param value: 注入值。

        .. seealso:: :meth:`inject`、:class:`flowing.runtime.ProvideNode`
        """
        key_name = key if isinstance(key, str) else key.name  # InjectionKey 按键名归一（str 原样 / 否则取 .name）
        self._provided[key_name] = value  # 同 key 重复 provide 后者覆盖前者；对已创建子 Agent 同样生效

    def inject(self, key: str | InjectionKey[Any]) -> Any:
        """沿 provide 链上溯取值：本 workflow → caller（含其链）→ Runtime。

        .. rubric:: 行为要点

        - 先查本 workflow 的 provide 表，未命中沿亲代链逐级上溯（根节点
          workflow 上溯一步即达 Runtime 层）；全链未命中抛
          :class:`flowing.errors.MissingProvideError`。
        - ``InjectionKey`` 跨节点退化为按 ``name`` 匹配（类型信息不跨
          节点传递）。

        :param key: 注入键（字符串，或 ``InjectionKey``——按键名归一）。
        :return: 找到的注入值。
        :raises flowing.errors.MissingProvideError: 全链无该 key 时。

        .. seealso:: :meth:`provide`、:func:`flowing.runtime.inject_from`
        """
        key_name = key if isinstance(key, str) else key.name  # InjectionKey 跨节点退化为按 name 匹配
        return inject_from(  # -> flowing.provide.inject_from（经 flowing.runtime 再导出亦有效）；全链未命中 -> MissingProvideError
            self.runtime, self, key_name,
        )

    async def destroy(self) -> None:
        """销毁本 workflow：级联归档未销毁的子 Agent，并从 ``runtime._nodes`` 注销。

        .. rubric:: 行为要点

        - 逐个 ``await runtime.archive_agent(child_id)``：归档把子 Agent
          从 ``_nodes`` / ``_agent_pool`` / ``core`` 名录移除、session
          文件保留（Workflow 一次调用即弃，子 Agent 不留可恢复的池条目）；
          随后清空内部子 Agent 表并注销本节点。重复调用为空操作（幂等）。
        - 不删除子 Agent 的 session 文件（归档留档）；不取消经
          :class:`RunWorkflowTool` 拉起的其他运行实例（每次运行是独立
          对象）。
        - 销毁时 ``run()`` 仍在执行 → 子 Agent 被级联归档，``run()``
          内的 ``await`` 陆续收到取消或销毁后的错误——编排代码应自行保证
          销毁时机（框架不预设）。

        .. seealso:: :meth:`flowing.agent.Agent.destroy`、
            :meth:`flowing.runtime.Runtime.archive_agent`、:meth:`create_agent`
        """
        for child_id in list(self._agents):   # 级联归档（Workflow 一次调用即弃：子 Agent 从池/名录移除、文件保留）
            if child_id in self.runtime._agent_pool or child_id in self.runtime._nodes:
                await self.runtime.archive_agent(child_id)   # 已归档子跳过（幂等：外层 archive 先销毁本 workflow 时）
        self._agents.clear()
        self.runtime._nodes.pop(self.node_id, None)  # 注销节点；重复调用为空操作（幂等）
