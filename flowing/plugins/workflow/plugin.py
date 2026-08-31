"""承载 ``RunWorkflowTool`` 与 ``WorkflowPlugin``——Workflow 的 LLM 触发入口工具与扩展插件类。
"""

from typing import Any, ClassVar

from flowing.agent import Agent
from flowing.plugins import Plugin
from flowing.runtime import Runtime
from flowing.tool import ScriptTool, ToolDefinition

from .loader import resolve_workflow
from .workflow import Workflow


class RunWorkflowTool(ScriptTool):
    """内置 ``run-workflow`` 工具——Workflow 的 LLM 触发入口（**async gen 形态**）。

    .. rubric:: 功能介绍

    LLM 传 workflow 文件路径 + 参数（可含自然语言 prompt），本工具按
    路径解析 workflow 定义、实例化、**后台任务启动**并立即返回收据。由
    :class:`WorkflowPlugin` 在 ``install()`` 时注册进全局工具注册表。
    基类为 `ScriptTool`（B12 裁决：进入「应用代码应使用 ScriptTool」的
    正式通道）；类级 ``definition`` 显式声明（保留 ``strict=False``——
    自动生成路径不含 strict 字段），``execute`` 为 async generator
    形态（B1：首 yield 收据 → 后台运行 → 末 yield 最终呈现）。

    .. rubric:: 设计动机

    **异步防死锁**（模块 docstring 专属角度六）：workflow 可反向
    ``caller.query()`` 等 caller 的回合产物，而 caller 工作循环串行——
    同步等待 workflow 完成即死锁。async gen 形态下首 yield 之后的运行段
    由框架后台驱动（``Tool._drive_asyncgen``），收据立即返回、运行段不
    阻塞工具调用栈——防死锁语义不变，且比手写 ``create_task`` 多获得：
    完成可见（末 yield EVENT）、失败可见（EVENT 错误块 + 日志，替代仅
    日志的静默失败）、强引用与取消收敛到 Agent 注册表（B9）。

    .. rubric:: 使用示例

    .. code-block:: python

        class RunWorkflowTool(ScriptTool):
            definition = ToolDefinition(
                name="run-workflow",
                description="启动一个编排工作流。参数 path 为 workflow 定义文件路径（@/ 项目根相对）。",
                params_schema={"path": {"type": "string", "description": "workflow 定义文件路径"}},
                strict=False,   # 其余参数透传给 Workflow.run()
            )

            async def execute(self, *, path: str, caller: Agent, **args: Any):
                wf_class = resolve_workflow(path)      # 轻量准备（首 yield 前）
                instance = wf_class(caller, caller.runtime)
                yield {"status": "started", "workflow": path}   # ① 收据（pending，TOOL 消息带 path）
                await instance.run(**args)             # 长任务（后台，框架驱动）
                yield {"status": "done", "workflow": path}      # ② 最终呈现（EVENT）

    .. rubric:: 行为规约

    - 期待行为：``execute`` 以 async generator 形态运行——**首 yield 前**
      完成轻量准备（``resolve_workflow`` + 实例化），首 yield 产出收据
      （框架包装为 ``status="pending"`` 的 ToolResult，带 ``background_task_id``
      注册键）；``instance.run()`` 在后台由框架驱动，完成时末 yield 产出
      EVENT 消息（LLM 可见）。收据保证键 ``status="started"`` 与
      ``workflow=<路径>``。workflow 自身的**数据结果**仍由编排代码自投
      （``caller.enqueue_message(...)``——框架完成消息与编排结果消息两条
      并存，角色不同：状态 vs 数据）。
    - 非行为：首 yield 之后不接触 ``asyncio.Task`` / ``create_task``
      （框架后台驱动）；不持久化运行状态；不做并发数/节点数上限检查。
    - 边缘情况：``path`` 解析失败（首 yield 前异常）→ ``Tool.__call__``
      的 except 顺序分派——``Intercepted`` → ``blocked``、其余 →
      ``status="error"`` 的 LLM 可见结果；后台运行段异常（``run()`` 抛错）
      → 框架投递 EVENT 错误块 + 诊断日志（双通道，LLM 可见）。
    - 前置条件：``caller`` 非 None（LLM 入口总有 caller）；解析结果须
      为 :class:`Workflow` 子类（函数形态经编译后也满足，见
      :func:`resolve_workflow`）。

    .. rubric:: 测试案例

    - 前置：``@/verify_fix.py`` 定义 ``VerifyFixWorkflow``；caller
      绑定 ``run-workflow`` 条目。操作：LLM 调用 ``run-workflow(
      path="@/verify_fix.py", max_rounds=2)``。期望：``execute`` 在
      ``run()`` 完成**之前**返回 pending 收据（``{"status": "started",
      "workflow": "@/verify_fix.py"}`` + 后台任务 ID）；``VerifyFixWorkflow``
      实例的 ``caller`` 是该 Agent。
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
      ``await instance.run(**args)``（时机：每次工具调用；运行段由
      ``Tool._drive_asyncgen`` 后台驱动，不阻塞工具调用栈）
    - 被调：``flowing.tool.Tool.__call__()`` 调度链包装 ``execute()``
      （时机：每次 LLM 经 ``run-workflow`` 的 tool_call 派发）
    - 实例化方：``flowing.plugins.workflow.WorkflowPlugin.install()``
      （时机：``runtime.use()`` 阶段一，每次安装）

    .. seealso::

        - :class:`Workflow` —— 被拉起的对象。
        - :func:`resolve_workflow` —— 名称解析。
        - :class:`flowing.tool.ScriptTool` —— 基类（async gen 形态见其
          类 docstring「async generator 形态」段落）。
    """

    definition: ToolDefinition = ToolDefinition(
        name="run-workflow",
        description="启动一个编排工作流。参数 path 为 workflow 定义文件路径"
                    "（@/ 项目根相对），其余参数透传给 Workflow.run()。",
        params_schema={"path": {"type": "string",
                                "description": "workflow 定义文件路径"}},
        strict=False,   # 其余参数透传给 Workflow.run()
    )
    """类级默认声明：``name="run-workflow"``、``params`` 只含 ``path``、
    ``strict=False``——workflow 的运行参数由 ``Workflow.run()`` 签名
    决定，工具层透传（与 :class:`flowing.plugins.skills.SkillLoadTool`
    同构的「薄入口 + 内层校验」形态）。
    """

    async def execute(self, *, path: str, caller: Agent, **args: Any):
        """async generator 形态后台启动指定 workflow（B1/B12，语义见类 docstring）。

        .. rubric:: 行为规约

        - 首 yield 前（轻量准备）：``resolve_workflow(path)`` → 实例化——
          异常经 ``Tool.__call__`` 的 except 顺序分派（``Intercepted`` →
          ``blocked``；其余 → ``status="error"``，LLM 可见）。
        - ① 首 yield：收据 ``{"status": "started", "workflow": path}``
          （框架包装为 pending ToolResult + ``background_task_id`` 注册键）。
        - 后台运行段：``await instance.run(**args)``（框架后台驱动；
          ``path`` 不从 ``args`` 透传给 ``run()``）。
        - ② 末 yield：最终呈现 ``{"status": "done", "workflow": path}``
          （EVENT，LLM 可见）；运行段异常由框架投递 EVENT 错误块 + 日志
          （双通道，替代旧版仅日志的静默失败）。
        - 非行为：不等待运行完成；不把 ``run()`` 的返回值写进收据。

        :raises flowing.errors.FlowingError: —— ``resolve_workflow``
           失败时（包装为 error 结果，LLM 可见）。

        .. rubric:: 调用关系（审计）

        - 调用：``flowing.plugins.workflow.resolve_workflow(path)``；
          ``wf_class(caller, caller.runtime)``（即 ``Workflow.__init__``）；
          ``await instance.run(**args)``——时机均为每次工具调用，运行段由
          ``Tool._drive_asyncgen`` 后台驱动（不阻塞工具调用栈）
        - 被调：``flowing.tool.Tool.__call__()``（时机：每次 LLM 经
          ``run-workflow`` 的 tool_call；async gen 形态——首 yield 收据 +
          后台驱动，见类 docstring）

        .. seealso:: :func:`resolve_workflow`、:meth:`Workflow.run`
        """
        wf_class: type[Workflow] = resolve_workflow(path)   # 轻量准备（首 yield 前——出错 → error/blocked 结果）
        instance = wf_class(caller, caller.runtime)
        yield {"status": "started", "workflow": path}       # ① 收据（pending，TOOL 消息带 path）
        await instance.run(**args)                          # 长任务（后台，由 _drive_asyncgen 驱动；path 不透传）
        yield {"status": "done", "workflow": path}          # ② 最终呈现（EVENT）


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
