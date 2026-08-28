"""承载 ``RunWorkflowTool`` 与 ``WorkflowPlugin``——Workflow 的 LLM 触发入口工具与扩展插件类。
"""

import asyncio
from typing import Any, ClassVar

from flowing.agent import Agent
from flowing.plugins import Plugin
from flowing.runtime import Runtime
from flowing.tool import Tool, ToolDefinition

from .loader import resolve_workflow
from .workflow import Workflow


class RunWorkflowTool(Tool):
    """内置 ``run-workflow`` 工具——Workflow 的 LLM 触发入口（**必异步**）。

    .. rubric:: 功能介绍

    LLM 传 workflow 文件路径 + 参数（可含自然语言 prompt），本工具按
    路径解析 workflow 定义、实例化、**后台任务启动**并立即返回收据。由
    :class:`WorkflowPlugin` 在 ``install()`` 时注册进全局工具注册表。

    .. rubric:: 设计动机

    **异步防死锁**（模块 docstring 专属角度六）：workflow 可反向
    ``caller.query()`` 等 caller 的回合产物，而 caller 工作循环串行——
    同步等待 workflow 完成即死锁。因此本工具**永远** ``asyncio.create_task``
    后台启动、立即返回；同步执行只允许「workflow 不反向调用 caller」的
    代码直调场景（调用方自行保证，框架不预设检查）。

    .. rubric:: 使用示例

    .. code-block:: python

        class RunWorkflowTool(Tool):
            definition = ToolDefinition(
                name="run-workflow",
                description="启动一个编排工作流。参数 path 为 workflow 定义文件路径（@/ 项目根相对）。",
                params_schema={"path": {"type": "string", "description": "workflow 定义文件路径"}},
                strict=False,   # 其余参数透传给 Workflow.run()
            )

            async def execute(self, *, path: str, caller: Agent, **args: Any) -> dict:
                wf_class = resolve_workflow(path)
                instance = wf_class(caller, caller.runtime)
                asyncio.create_task(instance.run(**args))   # ★ 后台启动，绝不 await
                return {"status": "started", "workflow": path}

    .. rubric:: 行为规约

    - 期待行为：``execute`` 从 ``create_task`` 返回后立即以收据返回
      （框架包装为 ``status="completed"`` 的 ToolResult）；收据保证键
      ``status="started"`` 与 ``workflow=<路径>``，实现可附加任务标识
      等观测字段。workflow 结果的**异步交付**由编排代码自行完成：
      ``caller.enqueue_message(...)``（fire-and-forget，结果作为独立
      消息进入 caller 队列，后续逻辑 Turn 被消费）或经订阅观察；框架
      初版不提供统一的结果回传通道。
    - 非行为：绝不 ``await instance.run(...)``；不持久化运行状态；不做
      并发数/节点数上限检查（初版不预设）。
    - 边缘情况：``path`` 解析失败（``resolve_workflow`` 抛错）→ 异常
      由 ``Tool.__call__`` 包装为 ``status="error"`` 的 LLM 可见结果；
      后台任务自身异常（``run()`` 抛错）不向本工具传播——任务已脱离，
      失败经任务自身路径暴露（初版仅约定「不静默吞掉：异常记录在任务
      上，可经日志观察」）。
    - 前置条件：``caller`` 非 None（LLM 入口总有 caller）；解析结果须
      为 :class:`Workflow` 子类（函数形态经编译后也满足，见
      :func:`resolve_workflow`）。

    .. rubric:: 测试案例

    - 前置：``@/verify_fix.py`` 定义 ``VerifyFixWorkflow``；caller
      绑定 ``run-workflow`` 条目。操作：LLM 调用 ``run-workflow(
      path="@/verify_fix.py", max_rounds=2)``。期望：``execute`` 在
      ``run()`` 完成**之前**返回收据 ``{"status": "started",
      "workflow": "@/verify_fix.py"}``；``VerifyFixWorkflow`` 实例的
      ``caller`` 是该 Agent。
    - 前置：``@/quick.py`` 只含无 self 的顶层 ``async def run(...)``。
      操作：LLM 调用 ``run-workflow(path="@/quick.py")``。期望：
      函数形态编译为 ``Workflow`` 子类后正常后台启动（裸名
      ``create_agent`` / ``agent`` / ``tool_call`` 在运行中改写为
      实例方法调用）。
    - 前置：workflow 的 ``run()`` 中 ``await self.caller.query("?")``。
      操作：同上调用。期望：caller 工作循环在当前逻辑 Turn 结束后消费该
      消息——无死锁。

    .. rubric:: 调用关系（审计）

    - 调用：``execute()`` 内 ``resolve_workflow(path)`` → 实例化 →
      ``asyncio.create_task(instance.run(**args))``（时机：每次工具调用）
    - 被调：``flowing.tool.Tool.__call__()`` 调度链包装 ``execute()``
      （时机：每次 LLM 经 ``run-workflow`` 的 tool_call 派发）
    - 实例化方：``flowing.plugins.workflow.WorkflowPlugin.install()``
      （时机：``runtime.use()`` 阶段一，每次安装）

    .. seealso::

        - :class:`Workflow` —— 被拉起的对象。
        - :func:`resolve_workflow` —— 名称解析。
        - :class:`flowing.tool.Tool` —— ``execute()`` 签名与返回值包装。
    """

    definition: ToolDefinition
    """类级默认声明：``name="run-workflow"``、``params`` 只含 ``path``、
    ``strict=False``——workflow 的运行参数由 ``Workflow.run()`` 签名
    决定，工具层透传（与 :class:`flowing.plugins.skills.SkillLoadTool`
    同构的「薄入口 + 内层校验」形态）。
    """

    async def execute(self, *, path: str, caller: Agent, **args: Any) -> dict[str, Any]:
        """后台启动指定 workflow 并立即返回收据（语义见类 docstring）。

        .. rubric:: 行为规约

        - 期待行为：``resolve_workflow(path)`` → 实例化 →
          ``asyncio.create_task(instance.run(**args))`` → 返回
          ``{"status": "started", "workflow": path}``；``path`` 不从
          ``args`` 透传给 ``run()``。
        - ``path`` 语义同 :func:`resolve_workflow`（``@/`` 根相对，根外
          保留绝对路径）。M-101 裁决后无 ``workflows/`` 约定目录：LLM
          给出的是**路径**而非裸名；「LLM 可触达哪些 workflow」的边界由
          应用层经 ``before_tool_call`` 钩子管控，框架不预设白名单。
        - 非行为：不等待运行完成；不把 ``run()`` 的返回值写进收据。

        :raises flowing.errors.FlowingError: —— ``resolve_workflow``
           失败时（包装为 error 结果，LLM 可见）。

        .. rubric:: 调用关系（审计）

        - 调用：``flowing.plugins.workflow.resolve_workflow(path)``；
          ``wf_class(caller, caller.runtime)``（即 ``Workflow.__init__``）；
          ``asyncio.create_task(instance.run(**args))``——时机均为每次
          工具调用，且绝不 await ``run()``
        - 被调：``flowing.tool.Tool.__call__()``（时机：每次 LLM 经
          ``run-workflow`` 的 tool_call；返回值由 ``__call__`` 包装为
          ``status="completed"`` 的 ToolResult）

        .. seealso:: :func:`resolve_workflow`、:meth:`Workflow.run`
        """
        wf_class: type[Workflow] = resolve_workflow(path)
        instance = wf_class(caller, caller.runtime)
        asyncio.create_task(instance.run(**args))  # 后台启动，绝不 await（异步防死锁，见类 docstring）
        receipt = {"status": "started", "workflow": path}  # 立即返回的收据；run() 返回值不进收据
        return receipt


class WorkflowPlugin(Plugin):
    """Workflow 扩展插件——阶段一入口：注册 ``run-workflow`` 工具。

    .. rubric:: 功能介绍

    ``runtime.use(WorkflowPlugin())`` 时框架调用 ``install(runtime)``：
    向 ``runtime.tool_registry`` 注册 :class:`RunWorkflowTool`。Workflow
    的「能力」本体是 :class:`Workflow` 基类（import 即用，不需要实例级
    启用）——本插件只负责把 LLM 触发入口接上；不需要 LLM 入口（纯代码
    路径）的应用可以不安装本插件。

    .. rubric:: 设计动机

    与 ``SkillPlugin`` 同构：插件提供「基类 + 资源发现 + 工具注册」，
    核心零新增。没有实例级 ``use_workflow(self)``——旧设计的
    ``use_workflow(agent, workflow=...)`` 挂载方式已废弃，编排代码直接
    实例化 ``Workflow`` 子类即可（不存在「Agent 默认拥有但可关闭」的
    workflow 能力，谈不上零开销问题）。

    .. rubric:: 使用示例

    .. code-block:: python

        runtime.use(WorkflowPlugin())
        # 之后 LLM 可经 run-workflow 工具按路径拉起任意位置定义的编排

    .. rubric:: 行为规约

    - 期待行为：``install()`` 同步完成注册（R1：只注册）；Agent 侧无需
      任何启用动作即可被 LLM 拉起 workflow（``run-workflow`` 条目由
      应用层按需 ``add_tool("run-workflow")`` 绑定到具体 Agent）。
    - 边缘情况：``run-workflow`` 规范名冲突 →
      :class:`flowing.errors.ToolNameConflictError`（重名永远不允许）。

    :raises flowing.errors.ToolNameConflictError: —— 工具规范名冲突时。

    .. rubric:: 调用关系（审计）

    - 调用：``install()`` 内 ``runtime.register_tool(RunWorkflowTool())``
      （时机：install 同步执行一次）
    - 被调：``flowing.runtime.Runtime.use()`` 按实参顺序调
      ``install(runtime)``（时机：``mount()`` 之前的插件安装阶段）
    - 实例化方：用户代码 ``runtime.use(WorkflowPlugin())``（公开 API；
      框架内无实例化方）

    .. seealso::

        - :class:`RunWorkflowTool`、:class:`Workflow`
        - :class:`flowing.plugins.Plugin` —— 插件基类与 R1–R4 约定。
    """

    name: ClassVar[str] = "workflow"
    """注册名（显式声明，无框架默认；类名去 ``Plugin`` 后缀转 kebab，
    推荐格式见 ``flowing.plugins`` 命名约定）。
    """

    dependencies: ClassVar[list[str]] = []   # S-10：与基类 Plugin 的 ClassVar 对齐
    """依赖声明（类属性元数据）。本插件无依赖，为空列表。

    .. seealso:: :meth:`flowing.runtime.Runtime._check_dependencies`
    """

    def install(self, runtime: Runtime) -> None:
        """阶段一：注册 ``run-workflow`` 工具。

        .. rubric:: 行为规约

        - 期待行为：``runtime.register_tool(RunWorkflowTool())``，同步
          返回；不创建任何 workflow 实例、不扫描任何目录
          （资源发现现场发生在 :func:`resolve_workflow` 被调用时）。

        .. rubric:: 调用关系（审计）

        - 调用：``flowing.runtime.Runtime.register_tool()``（时机：
          install 同步执行一次，阶段一）
        - 被调：``flowing.runtime.Runtime.use()``（时机：``mount()``
          之前的插件安装阶段，每插件一次）

        .. seealso:: :class:`RunWorkflowTool`
        """
        runtime.register_tool(RunWorkflowTool())
