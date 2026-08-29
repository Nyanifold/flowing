"""flowing.plugins.workflow —— Workflow 编排器（Workflow / RunWorkflowTool / resolve_workflow / WorkflowPlugin）。

.. rubric:: 模块定位

Workflow 是**规则性编排器**：编排逻辑（分支、循环、并行、串行）由 Python
代码显式决定，与 goal mode（LLM 按目标自主决定路径）是正交的两个编排
原语。Workflow 适用于「流程已知、要确定性、可重复」的场景（审批流、
验证-修复循环、固定步骤流水线）。

Workflow **不是**：

- **不是技能声明**——不采纳 mermaid/图声明形态；但**执行能力兼容**
  kimi 式 flow（见下文「caller 调用」）。
- **不是 Agent**——没有 Turn 循环、不调用 LLM（但可以驱动 Agent）；
  不产生消息树节点、不参与 ``Message.id`` / ``parent_id`` 链。
- **不是框架核心**——编排所需的机制（子 Agent 创建、消息、工具、
  provide/inject）核心已有；本模块只提供抽象基类 + 资源发现 +
  ``run-workflow`` 工具，核心零新增。

.. rubric:: 专属角度一：两树位置

Workflow 实例在对象图中同时占两个位置：

1. **节点树**（``runtime._nodes`` 共享 ID 空间）：``__init__`` 即注册，
   ``node_id`` 取 ``workflow-*`` 前缀。**可作根节点**（``caller=None``，
   ``_parent_id`` 指向 Runtime）——如代码直接拉起的顶层编排；**也可嵌为
   Agent 子节点**（``_parent_id = caller.node_id``，如 LLM 经
   ``run-workflow`` 工具拉起）。Workflow 创建的 Agent 的
   ``parent_id`` 指向本 workflow 的 ``node_id``——生命周期上
   Workflow 是它们的父节点，``destroy()`` 级联**归档**（一次调用即弃）。
2. **provide/inject 链**：Workflow 实现 ``ProvideNode`` 协议——
   ``self.provide(k, v)`` 的值对它创建的**全部 Agent 及后代**可见；
   ``self.inject(key)`` 沿 ``_parent_id`` 上溯（workflow → caller →
   runtime）。注意语义边界：Workflow 调用**工具 / 子 Agent 时参数由
   代码直接填充，不走 inject 式填充**——provide/inject 只作用于
   Workflow 自身与其创建的 Agent 子树之间的值继承。

.. rubric:: 专属角度二：只有 create_agent，没有 invoke_subagent

Workflow 驱动 Agent 的唯一入口是 :meth:`Workflow.create_agent`
（字符串 ``agent_type`` 类型名，委托 ``runtime.create_agent``），创建
后直接 ``message()``。**没有** ``invoke_subagent``——它是 Agent 侧
「LLM 唤起 + SubagentEntry 解析 + 钩子 + agent 池生命周期」的包装，
Workflow 的编排代码自己就是包装层，再叠一层只会混淆职责。一次性唤起
的等价写法：``child = await self.create_agent(t); r = await
child.query(p)``。Workflow 路径也不经过 ``SubagentEntry`` 与
``before/after_subagent_invoke`` 钩子（只有创建管线的
``before_create`` / ``after_create``）。

子 Agent 三模式（都在编排代码中显式表达）：

- **命名复用**：持 Python 变量引用，反复 ``message()`` 同一实例，
  上下文在实例中延续。
- **匿名即弃**：用完 ``await child.destroy()``——**记录保留**
  （session 目录与池记录保留，「实例没了，会话还在」，可现场恢复）。
  （区分：这是编排代码**主动**调用 ``Agent.destroy``；Workflow 自身
  ``destroy()`` 时级联的是**归档**——子 Agent 从池/名录移除、文件保留。）
- **副线不 commit**：``await side.side_query(msg)``（副线消息
  不落盘、不进消息树主链——「副线」是调用路径属性，
  ``Message.side`` 字段已删除），对象存活复用直到显式 ``destroy()``。

并行无需专门 API——``asyncio.gather(*(s.query(...) for s in subs))``
即原语。

.. rubric:: 专属角度三：独立钩子与规范名直查

:meth:`Workflow.tool_call` 与 ``Agent.tool_call`` **同名不同签名、不同
语义**：

- 签名：``tool_call(tool_name: str, **args)``——接收规范名 + 零散参数，
  而非 ``ToolCall`` 对象。
- 查找：**规范名直查** ``runtime.tool_registry.get(tool_name)``——无
  别名、无 ``ToolEntry`` 覆写、无 ``specified``、无 ``inject``（参数由
  编排代码直接确定）。
- 钩子：走 Workflow **自己的** ``self.hooks``（``before_tool_call`` /
  ``after_tool_call``），与任何 Agent 的钩子**完全独立**——Agent 侧挂
  的审批 / 安全 / 审计 handler 不会在 workflow 的工具调用上触发；工具
  以 ``caller=None`` 调用。

.. rubric:: 专属角度四：run() 返回约定

``run()`` 返回 ``dict | None``：返回 dict 时作为 workflow 的结果（交付
方式见 :class:`RunWorkflowTool`——fire-and-forget 入队 caller 或经订阅
观察）；返回 ``None`` 表示纯编排副作用。返回值必须是 JSON 兼容类型的
dict（初版不强制结构化产物契约——子 Agent 结果结构化是可选约定）。

.. rubric:: 专属角度五：初版不持久化

Workflow 运行状态**不持久化**：每次运行拉起一个新对象，崩溃后不承诺
续跑，``_nodes`` 中的 workflow 节点记录随 Runtime 退出即失效。设计保留
演进位（后续版本可挂状态文件 + 已完成节点缓存），初版仅约定上述边界。

.. rubric:: 专属角度六：触发入口与异步防死锁

三种触发方式**实为两种**：LLM 工具调用（``run-workflow``）、代码直接调用
（``wf = MyWorkflow(caller, runtime); await wf.run(...)``）。Cron 无独立
触发通道（S-23 裁决：cron 只有 ``message`` / ``tool_call`` 两类执行器，
无 ``kind="workflow"`` 内建执行器）；经 cron 的 ``tool_call`` 执行器调
``run-workflow`` 属普通工具调用路径，不算第三种触发方式。

**异步防死锁规则**：workflow 作为工具拉起时**一定异步**（后台任务 +
立即返回收据，见 :class:`RunWorkflowTool`）。死锁成因：Workflow 的核心
能力之一是**反向调用 caller**（``await self.caller.query(...)`` 等待
caller 的回合产物），而 caller 的工作循环串行——当前逻辑 Turn（正在执行
``run-workflow`` 工具）完成前不消费新消息；若工具同步等待 workflow，
双方互相等待即死锁。同步执行只允许「workflow 不反向调用 caller」的
场景，由调用方自行保证（框架不预设检查）。

**kimi 式兼容**：Flowing 刻意允许 caller 调用（pi / Claude Code 均不
允许），以上述异步规则为代价换取「同一会话推进」能力——kimi flow 的
「节点提示词发进同一会话」等价于 workflow 反复 ``self.caller.query(
节点提示词)``；decision 节点 = prompt 显式约束输出（如「仅输出yes或
no」）+ 从回合产物 ``TurnResult.final_text`` 解析结构化选择。

.. seealso::

    - :mod:`flowing.runtime` —— ``create_agent`` / ``_nodes`` /
      ``ProvideNode``。
    - :mod:`flowing.agent` —— ``message()`` / ``side_query()`` /
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
    """Workflow 抽象基类——由本模块（workflow 插件）提供，用户继承实现。

    .. rubric:: 功能介绍

    一次编排运行的对象载体：``__init__`` 接收 caller 与 runtime 并在节点树
    / provide 链上就位；``run()`` 承载全部编排逻辑。Workflow 拥有与 Agent
    同构但独立的三件实例装备：``hooks``（独立 ``HookRegistry``）、
    ``provide`` / ``inject``（ProvideNode 协议）、``create_agent`` /
    ``tool_call``（驱动 Agent 与工具的入口）。

    .. rubric:: 设计动机

    - **每次运行拉起一个新对象**：workflow 无跨运行状态——「可重复」是
      规则性编排器的本质，运行态只属于当次运行（初版不持久化，崩溃不
      续跑）。
    - **初始化传 caller**：刻意允许反向驱动发起它的 Agent（kimi 式兼容），
      代价是 :class:`RunWorkflowTool` 的异步防死锁规则。
    - **插件提供基类**：编排机制核心已有，核心零新增——与
      ``SkillPlugin`` 提供 ``use_skill`` 同构。

    .. rubric:: 使用示例

    .. code-block:: python

        from flowing.plugins.workflow import Workflow

        class VerifyFixWorkflow(Workflow):
            \"\"\"运行检查器，修复失败的内容，重复直到通过或两轮无进展。\"\"\"

            async def run(self, prompt: str | None = None, max_rounds: int = 3) -> dict:
                no_progress = 0
                for round_no in range(max_rounds):
                    # 命名子 agent：验证器（复用实例，上下文延续）
                    verifier = await self.create_agent("verifier-agent")
                    result = await verifier.query("运行 tsc --noEmit 并列出所有错误")

                    if result.status == "pass":
                        # fire-and-forget 通知：await 仅覆盖入队本身，
                        # 不等 caller 的回合消费该消息
                        await self.caller.enqueue_message(
                            Message(kind=MessageKind.PLUGIN,
                                    source="workflow:verify-fix",
                                    content=[TextBlock(text=f"第 {round_no + 1} 轮通过")]))
                        return {"status": "passed", "rounds": round_no + 1}

                    # 匿名子 agent：修复器（用完即弃，session 记录保留）
                    fixer = await self.create_agent("fixer-agent", errors=result.errors)
                    fix_result = await fixer.query("修复以上错误")
                    await fixer.destroy()

                    if not fix_result.changes:
                        no_progress += 1
                        if no_progress >= 2:
                            return {"status": "stalled", "reason": "两轮无进展"}
                return {"status": "failed", "reason": f"超过 {max_rounds} 轮"}

    kimi 式同一会话推进（caller 调用）：

    .. code-block:: python

        class KimiStyleFlow(Workflow):
            async def run(self, prompt: str | None = None) -> None:
                r1 = await self.caller.query(prompt or "分析代码变更，列出所有修改的文件")
                choice = (await self.caller.query(
                    "以上分析是否充分？仅输出yes或no")).final_text.strip().lower()
                if choice == "yes":
                    await self.caller.query("生成审查报告")

    .. rubric:: 行为规约

    - 前置条件：实例只能经「``Workflow`` 子类构造（``caller`` +
      ``runtime``）」创建；``__init__`` 必须同步（节点注册是结构操作）。
    - 不变量：``node_id`` 构造时分配（``workflow-*``）并注册进
      ``runtime._nodes``，生命周期内不变；``_parent_id`` 为
      ``caller.node_id``（``caller=None`` 时为 Runtime 节点 id）。
    - 边缘情况：``caller=None``（根节点 workflow）时一切
      ``self.caller.*`` 调用属编程错误（``AttributeError`` on None）——
      框架不预设检查；``provide`` 的值对**本** workflow 创建的全部 Agent
      及后代可见，对其他 workflow 的子树不可见。
    - 非行为：Workflow 不进消息队列、不消费消息、没有 Turn 循环与
      ``_run_turn``；运行状态不落盘（初版不持久化）。

    .. rubric:: 测试案例

    - 前置：``wf = MyWorkflow(caller=agent, runtime=runtime)``。操作：
      ``child = await wf.create_agent("x-agent")`` 后
      ``child.inject(k)``（``wf.provide(k, v)`` 已调用）。期望：
      ``child.inject(k) == v``；``child`` 的子 Agent 同样可见。
    - 前置：同上。操作：``await wf.destroy()``。期望：``child`` 被归档
      （从 ``_nodes`` / ``_agent_pool`` / ``core`` 名录移除）且其 session
      目录保留（归档留档）。

    .. rubric:: 调用关系（审计）

    - 调用：``create_agent()`` 委托 ``runtime.create_agent()``；
      ``tool_call()`` 经 ``runtime.tool_registry.get()`` 与 ``self.hooks``
      dispatch；``inject()`` 沿 ``_parent_id`` 上溯（各方法内，时机见
      各方法标注）
    - 被调：``run()`` 由触发方驱动——``RunWorkflowTool.execute()`` 后台
      任务（时机：每次 LLM 经 ``run-workflow`` 工具调用）；代码直调为
      用户代码路径（S-23 裁决：无 Cron 回调路径）
    - 实例化方：``flowing.plugins.workflow.RunWorkflowTool.execute()``
      （时机：每次 LLM 经 ``run-workflow`` 工具调用）；
      ``flowing.runtime.Runtime.mount()`` Workflow 根形态（时机：每次
      以 Workflow 定义文件路径 mount）；其余为用户代码直调

    .. seealso::

        - :class:`RunWorkflowTool` —— LLM 触发入口（异步规则）。
        - :func:`resolve_workflow` —— 按名解析 workflow 定义。
        - :class:`flowing.runtime.ProvideNode` —— provide/inject 协议。
    """

    caller: Agent | None
    """发起本 workflow 的 Agent；根节点 workflow 为 ``None``。可经
    ``caller.query(...)`` 反向驱动（kimi 式兼容），或调用 caller 的
    公开方法。Workflow 不持 caller 之外的父对象引用——生命周期定位与
    provide 上溯共用 ``_parent_id`` 字符串。
    """
    runtime: Runtime
    """所属 Runtime（对象图根）。创建 Agent、查工具注册表、订阅观察等都
    经它；与 ``caller.runtime`` 恒为同一实例（``caller`` 非 None 时）。
    """
    node_id: str
    """节点树 ID（``workflow-*`` 前缀），构造时分配并注册进
    ``runtime._nodes``；本 workflow 创建的 Agent 的 ``parent_id`` 指向它。
    """
    hooks: HookRegistry
    """Workflow 自己的钩子注册表（实例级，构造时创建）。与任何 Agent 的
    ``hooks`` **完全独立**——Agent 侧 handler 不会拦截 workflow 的工具
    调用，反之亦然。``tool_call()`` 使用 ``before_tool_call`` /
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
        """构造 workflow 实例并就位于节点树与 provide 链（同步）。

        .. rubric:: 行为规约

        - 期待行为：分配 ``node_id``（``workflow-*``）、创建独立
          ``hooks``、初始化 ``_provided`` / ``_agents``、设定
          ``_parent_id`` 并注册进 ``runtime._nodes``。
        - 非行为：不启动任何任务（``run()`` 由调用方驱动）；不解析任何
          workflow 定义文件（实例化的是已解析的类）。
        - 前置条件：``caller`` 为 None 时本实例是根节点 workflow；非 None
          时 ``runtime`` 应与 ``caller.runtime`` 一致（不一致属编程错误，
          初版仅约定不做结构强制）。

        .. rubric:: 调用关系（审计）

        - 调用：注册进 ``runtime._nodes``（时机：构造时同步，直接写
          节点表）
        - 被调：``flowing.plugins.workflow.RunWorkflowTool.execute()``
          实例化（时机：每次 LLM 经 ``run-workflow`` 工具调用）；
          ``flowing.runtime.Runtime.mount()`` Workflow 根形态（时机：
          每次以 Workflow 定义文件路径 mount）；代码直调为用户代码

        .. seealso:: :meth:`run`、:meth:`destroy`
        """
        self.caller = caller
        self.runtime = runtime
        self.node_id = f"workflow-{uuid4()}"  # workflow-* 前缀（与 ToolCall id 的 <来源类型名>-<uuid4> 同族，S-41 裁决②）
        self.hooks = HookRegistry()  # 实例级独立钩子注册表（与任何 Agent 的 hooks 完全独立）
        self._provided = {}
        self._agents = {}
        self._parent_id = caller.node_id if caller is not None else runtime.node_id  # 根节点 workflow 指向 Runtime
        runtime._nodes[self.node_id] = self  # 创建即注册（_nodes: dict[str, ProvideNode]，spec-draft 02 §5.2 裁决——Workflow 可作父节点/根节点）

    @abstractmethod
    async def run(self, prompt: str | None = None, **kwargs: Any) -> dict[str, Any] | None:
        """编排逻辑主体——子类必须实现。

        .. rubric:: 功能介绍

        全部编排逻辑（创建/复用/销毁子 Agent、调用工具、反向驱动 caller、
        并行 gather、分支循环）都写在这里。参数由调用方传入，可含自然语言
        ``prompt``（通常作为子 Agent 的任务描述转发）。

        .. rubric:: 行为规约

        - 返回约定：``dict`` = workflow 结果（JSON 兼容；交付方式由触发
          方决定——工具拉起时由 :class:`RunWorkflowTool` 路径异步交付，
          代码直接调用时就是 ``await`` 的返回值）；``None`` = 纯编排副作用。
        - 非行为：框架不限制运行时长 / 节点数 / 并发数（上限防护属后续
          演进，初版不预设）；不捕获子类抛出的异常——异常终止本次运行
          并向触发方传播（工具拉起路径上体现为后台任务失败，框架不做
          自动重试）。
        - 边缘情况：重复调用同一实例的 ``run()`` 属编程错误（每次运行
          拉起新对象）；运行中 Runtime shutdown → 本方法内创建的子
          Agent 随 ``destroy()`` 级联归档，未完成的 ``await`` 收到取消。

        .. rubric:: 调用关系（审计）

        - 调用：无（抽象方法；编排调用由子类实现决定）
        - 被调：``flowing.plugins.workflow.RunWorkflowTool.execute()``
          经 ``asyncio.create_task(instance.run(**args))`` 后台启动
          （时机：每次 LLM 经 ``run-workflow`` 工具调用，绝不 await）；
          代码直调为用户代码路径（S-23 裁决：无 Cron 回调路径）

        .. seealso:: :meth:`create_agent`、:meth:`tool_call`
        """
        ...

    async def create_agent(self, agent_type: str, **kwargs: Any) -> Agent:
        """创建子 Agent——Workflow 驱动 Agent 的**唯一**入口。

        .. rubric:: 功能介绍

        委托 ``runtime.create_agent(agent_type, parent_id=self.node_id,
        **kwargs)``：走标准创建管线（``__init__`` → ``before_create`` →
        ``setup`` → PENDING 检查 → 池元数据写盘 → ``_nodes`` 注册 →
        ``after_create`` → 工作循环启动），
        创建即入 agent 池；本 workflow 把实例记入 ``_agents`` 以便
        ``destroy()`` 级联归档。

        .. rubric:: 设计动机

        **没有** ``invoke_subagent``（裁决定稿）：invoke 是「LLM 唤起 +
        SubagentEntry 解析 + 钩子 + 池生命周期」的 Agent 侧包装，而
        Workflow 的编排代码自己就是包装层。创建签名统一为字符串
        ``agent_type``（类型名），不用 Agent 类作参数。

        .. rubric:: 使用示例

        .. code-block:: python

            reviewer = await self.create_agent("reviewer-agent", scope="api-layer")
            r1 = await reviewer.query("审查 auth 模块")   # → TurnResult
            r2 = await reviewer.query("审查 payment 模块")  # 同一实例，上下文延续

        .. rubric:: 行为规约

        - 期待行为：返回的 Agent 已可立即 ``message()``；其 ``inject()``
          上溯链经本 workflow（workflow 级 provide 可见）。
        - 非行为：**不**经 ``SubagentEntry.resolve()``、**不**触发
          ``before/after_subagent_invoke`` 钩子（创建路径仅有
          ``before_create`` / ``after_create``）；参数不做 inject 式填充——
          ``kwargs`` 原样传给子 Agent 的 ``setup()`` 并经其
          ``args_schema`` 校验。
        - 边缘情况：``agent_type`` 无法解析 → 创建管线的解析异常上抛；
          同名类型重复创建产生多个独立实例（无去重——命名复用靠调用方
          持引用，不靠类型名）。

        .. rubric:: 测试案例

        - 前置：``wf.provide(k, "v")``。操作：``c = await
          wf.create_agent("x")``。期望：``c.inject(k) == "v"``；
          ``c.node_id in wf._agents``；``c._parent_id == wf.node_id``。
        - 前置：无。操作：``hasattr(wf, "invoke_subagent")``。期望：
          ``False``。

        .. rubric:: 调用关系（审计）

        - 调用：``flowing.runtime.Runtime.create_agent(agent_type,
          parent_id=self.node_id, **kwargs)``（时机：每次调用，走创建
          管线第 1–8 步）
        - 被调：函数形态 workflow 编译产物的裸名改写
          （``resolve_workflow()`` 把 ``create_agent(...)`` /
          ``agent(...)`` 改写为 ``self.create_agent(...)``，时机：
          函数形态 ``run()`` 执行期）；类形态编排代码中的调用属用户代码

        .. seealso::

            - :meth:`flowing.runtime.Runtime.create_agent` —— 底层唯一
              创建路径。
            - :meth:`flowing.agent.Agent.invoke_subagent` —— Agent 侧的
              LLM 唤起包装（Workflow 刻意不提供）。
        """
        agent = await self.runtime.create_agent(agent_type, parent_id=self.node_id, **kwargs)
        self._agents[agent.node_id] = agent  # 记入级联销毁表（destroy() 的依据）
        return agent  # 已可立即 message()；inject 上溯链经本 workflow

    async def tool_call(self, tool_name: str, **args: Any) -> ToolResult:
        """Workflow 自己的工具调用——规范名直查注册表，独立钩子。

        .. rubric:: 功能介绍

        与 ``Agent.tool_call`` 同名但签名与语义不同：接收**规范名** +
        零散参数，直接 ``runtime.tool_registry.get(tool_name)`` 查全局
        注册表，构造 ``ToolCall`` 后经本实例的 ``hooks`` 走
        ``before_tool_call`` / ``after_tool_call``，最后以
        ``caller=None`` 调度工具。

        .. rubric:: 设计动机

        Workflow 的工具调用是**编排代码的直接动作**，不是 LLM 决策——
        因此没有别名 / ``ToolEntry`` 覆写 / ``specified`` / ``inject``
        （这些机制服务的都是「LLM 可见性与 Agent 级定制」，编排代码
        不需要）。独立钩子使 workflow 的工具调用不被 Agent 侧审批 handler
        拦截（审批策略是 Agent 面向 LLM 调用的防线），同时给 workflow
        作者自己的审计/日志钩子位。

        与 ``Agent.tool_call(tool_call: ToolCall)`` 同名不同签名是**有意
        分野**（C-17 裁决）：Agent 侧是 LLM 路径的正式签名（别名 +
        Agent 级绑定）；本方法是编排直查的便捷封装（规范名 + 零散参数，
        内部构造 ``ToolCall`` 仅服务钩子 value）。裸
        ``tool_call("name", **args)`` 形态同时是 workflow 裸文件的既定
        语法（M 轮裁决）。

        .. rubric:: 使用示例

        .. code-block:: python

            async def run(self, prompt=None):
                diff = await self.tool_call("read-file", path="src/auth.py")
                ...

            # workflow 自己的钩子（在 __init__ 之后、run 之前的装配代码中）：
            self.hooks.before_tool_call(self._audit_call, by="wf-audit")

        .. rubric:: 行为规约

        - 期待行为：``tool_registry.get(tool_name)`` 命中 → 构造
          ``ToolCall(id=f"workflow-{uuid4()}", name=tool_name,
          args=args)``（id 由框架生成，S-41 裁决②，仅作追踪）→ dispatch
          ``before_tool_call``（value 为 ToolCall，handler 可改写 args、
          可 ``shortcut`` 短路、可 ``raise Intercepted`` 阻断——阻断时
          返回 ``ToolResult.blocked(...)``）→ 调度工具 → dispatch
          ``after_tool_call``（value 为 ToolResult，可改写）→ **收尾归一**
          （return 前幂等再跑一次 ``normalize_output``，D19：shortcut
          产物与 ``after_tool_call`` 对 ``result.output`` 的改写都可能
          未经归一，此处兜底封两缝）→ 返回最终 ``ToolResult``。
        - **ToolCall→dict 解包点**（S-41 裁决②指名）：就是调度处的
          ``call.args`` 直取——Agent 侧的 ``_normalize()``（别名映射 +
          参数聚合）在本路径**无对应物**，``args`` 就是全部入参。
        - 非行为：不回退别名、不查任何 ``_tool_entries``、不做参数聚合
          （LLM args / 默认值 / specified / inject 四层合并不存在——
          ``args`` 就是全部入参）；不触发 caller 或任何 Agent 的钩子。
        - 边缘情况：``tool_name`` 未注册 →
          :class:`flowing.errors.ToolNotFoundError`；工具声明了
          ``caller`` 参数时收到 ``None``——依赖 caller 的工具在
          workflow 路径上不可用（工具作者责任，框架不预设检查）。
        - 后置条件：返回值的 ``status`` 四值语义与 Agent 路径完全一致
          （``completed`` / ``pending`` / ``blocked`` / ``error``）；
          ``output`` 恒为归一化五形态之一（``normalize_output`` 幂等
          收尾，D19——与 :meth:`flowing.agent.Agent.tool_call` 同一
          不变量：「出 tool_call 的 ``ToolResult.output`` 恒已归一」）。

        .. rubric:: 测试案例

        - 前置：注册表有 ``read-file``；caller Agent 的
          ``before_tool_call`` 挂了一个总是 ``raise Intercepted`` 的
          handler。操作：``await wf.tool_call("read-file", path="x")``。
          期望：工具**正常执行**（Agent 钩子未触发），wf 自己的
          ``before_tool_call`` handler 被调用。
        - 前置：``tool_name="ghost"``。操作：调用。期望：
          ``ToolNotFoundError``。

        .. rubric:: 调用关系（审计）

        - 调用：``runtime.tool_registry.get(tool_name)``（规范名直查）；
          构造 ``flowing.tool.ToolCall(name=tool_name, args=args)``；
          ``self.hooks`` 的 ``before_tool_call`` / ``after_tool_call``
          dispatch；``flowing.tool.Tool.__call__()`` 以 ``caller=None``
          调度——时机均为每次 ``tool_call()``
        - 被调：函数形态 workflow 编译产物的裸名改写（``tool_call(...)``
          → ``self.tool_call(...)``，时机：函数形态 ``run()`` 执行期）；
          类形态编排代码中的调用属用户代码

        .. seealso::

            - :meth:`flowing.agent.Agent.tool_call` —— Agent 侧同名方法
              （ToolCall 入参 + 别名查找 + 参数聚合）。
            - :class:`flowing.tool.ToolRegistry`、:class:`flowing.tool.ToolResult`
        """
        tool = self.runtime.tool_registry.get(tool_name)  # 规范名直查；未注册 -> ToolNotFoundError

        # id 由框架生成（S-41 裁决②：编程路径 id 形如 <来源类型名>-<uuid4>，仅作追踪）
        call = ToolCall(id=f"workflow-{uuid4()}", name=tool_name, args=args)  # -> flowing.tool.ToolCall
        try:
            call = await self.hooks.before_tool_call.dispatch(  # Workflow 自己的钩子，与 Agent 侧完全独立
                self, call,
            )  # handler 可改写 args / shortcut 短路
        except Intercepted as exc:
            # 硬阻断：工具本体不执行，after_tool_call 不触发（与 Agent.tool_call 同一出口）
            return ToolResult.blocked(reason=str(exc))
        if call.shortcut is not None:
            result = call.shortcut  # 协商短路：handler 提供的 ToolResult 直接作为本次结果（产物未经 __call__ 归一，由收尾归一兜底，D19）
        else:
            result = await tool(call.args, caller=None)  # ToolCall→dict 解包点 = call.args 直取（无 _normalize 等价物）；caller 恒 None
        try:
            result = await self.hooks.after_tool_call.dispatch(self, result)  # value 为 ToolResult，可改写（含改写 result.output 为原料——由收尾归一兜底，D19）
        except Intercepted as exc:
            result = ToolResult.blocked(reason=str(exc))   # after_tool_call 拦截 → blocked 结果返回（与 Agent.tool_call 同一塑形）
        result.output = await normalize_output(result.output)  # 收尾归一（D19）：return 前幂等再跑一次；与 Agent.tool_call 同一不变量——出 tool_call 的 output 恒为五形态之一
        return result  # status 四值语义与 Agent 路径一致

    def provide(self, key: str | InjectionKey[Any], value: Any) -> None:
        """声明 provide 值——对本 workflow 创建的全部 Agent 及后代可见。

        .. rubric:: 行为规约

        - 期待行为：写入 ``_provided``；之后创建的子 Agent（及其子孙）
          ``inject(key)`` 沿链上溯时命中本层。
        - 边缘情况：同 key 重复 provide → 后者覆盖前者（节点内语义，
          与 ``Agent.provide`` 一致）；对**已创建**的子 Agent 同样生效
          （inject 是使用时上溯，非创建时拷贝）。
        - 非行为：不影响 caller 与其他 workflow 的子树；不参与工具/
          子 Agent 调用的参数填充（编排代码直接传参）。

        .. rubric:: 调用关系（审计）

        - 调用：无（写入 ``_provided``；``InjectionKey`` 按键名归一：
          ``str`` 原样、否则取 ``.name``）
        - 被调：无框架内调用方（编排代码即用户代码）

        .. seealso:: :meth:`inject`、:class:`flowing.runtime.ProvideNode`
        """
        key_name = key if isinstance(key, str) else key.name  # InjectionKey 按键名归一（str 原样 / 否则取 .name）
        self._provided[key_name] = value  # 同 key 重复 provide 后者覆盖前者；对已创建子 Agent 同样生效

    def inject(self, key: str | InjectionKey[Any]) -> Any:
        """沿 provide 链上溯取值：本 workflow → caller（含其链）→ Runtime。

        .. rubric:: 行为规约

        - 期待行为：先查 ``_provided``，未命中沿 ``_parent_id`` 上溯；
          全链未命中抛 :class:`flowing.errors.MissingProvideError`。
        - 边缘情况：根节点 workflow（``caller=None``）上溯一步即达
          Runtime 层；``InjectionKey`` 跨节点退化为按 ``name`` 匹配
          （类型信息不跨节点传递）。

        :raises flowing.errors.MissingProvideError: —— 全链无该 key 时。

        .. rubric:: 调用关系（审计）

        - 调用：``flowing.runtime.inject_from()`` 以本实例与 ``runtime``
          线性上溯（时机：每次调用；``Agent.inject`` /
          ``Workflow.inject`` / ``Runtime.inject`` 三处委托同一算法；
          字段名已全库统一为 ``runtime``——原 ``_runtime``/``runtime``
          漂移已闭环，见 facts/plugins-workflow.md）
        - 被调：无框架内调用方（编排代码即用户代码）

        .. seealso:: :meth:`provide`、:func:`flowing.runtime.inject_from`
        """
        key_name = key if isinstance(key, str) else key.name  # InjectionKey 跨节点退化为按 name 匹配
        return inject_from(  # -> flowing.provide.inject_from（经 flowing.runtime 再导出亦有效）；全链未命中 -> MissingProvideError
            self.runtime, self, key_name,
        )

    async def destroy(self) -> None:
        """销毁本 workflow：级联**归档** ``_agents`` 中未销毁的子 Agent，并从
        ``runtime._nodes`` 注销（幂等）。

        .. rubric:: 行为规约

        - 期待行为：逐个 ``await runtime.archive_agent(child_id)``（**归档**——
          子 Agent 从 ``_nodes`` / ``_agent_pool`` / ``core`` 名录移除、
          session 文件保留；Workflow 一次调用即弃，子 Agent 不留可恢复
          池条目），清空 ``_agents``，注销节点；重复调用为空操作。
        - 非行为：不删除子 Agent 的 session 文件（归档留档）；不取消经
          :class:`RunWorkflowTool` 拉起的其他运行实例（每个运行是独立
          对象）。
        - 边缘情况：销毁时 ``run()`` 仍在执行 → 子 Agent 被级联归档，
          ``run()`` 内的 ``await`` 陆续收到取消/销毁后的错误——编排代码
          应自行保证销毁时机（框架不预设）。

        .. rubric:: 调用关系（审计）

        - 调用：逐个 ``await runtime.archive_agent(child_id)``（时机：销毁时
          级联归档；已归档子跳过——幂等）；从 ``runtime._nodes`` 注销
          （时机：级联完成后，同步结构操作）
        - 被调：无框架内调用方（编排代码显式 ``await wf.destroy()`` 属
          用户代码；``Runtime.archive_agent`` 对 Workflow 节点分派本方法）

        .. seealso:: :meth:`flowing.agent.Agent.destroy`、
            :meth:`flowing.runtime.Runtime.archive_agent`、:meth:`create_agent`
        """
        for child_id in list(self._agents):   # 级联归档（Workflow 一次调用即弃：子 Agent 从池/名录移除、文件保留）
            if child_id in self.runtime._agent_pool or child_id in self.runtime._nodes:
                await self.runtime.archive_agent(child_id)   # 已归档子跳过（幂等：外层 archive 先销毁本 workflow 时）
        self._agents.clear()
        self.runtime._nodes.pop(self.node_id, None)  # 注销节点；重复调用为空操作（幂等）
