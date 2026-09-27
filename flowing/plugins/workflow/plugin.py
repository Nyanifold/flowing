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
    """内置 ``run-workflow`` 工具——Workflow 的 LLM 触发入口（async generator 形态）。

    .. rubric:: 功能介绍

    LLM 传 workflow 定义文件路径 + 运行参数（可含自然语言 prompt），本
    工具按路径解析 workflow 定义、实例化，以后台任务方式启动 ``run()``
    并立即返回收据；由 :class:`WorkflowPlugin` 在 ``install()`` 时注册进
    全局工具注册表，Agent 经 ``add_tool("run-workflow")`` 绑定后即可被
    LLM 调用。

    执行体是 async generator（该形态的契约见
    :class:`flowing.tool.ScriptTool` 类 docstring“后台工具形态”）：第一
    个 ``yield`` 是收据（``Tool.__call__`` 等它作为 ``pending`` 收据），
    之后的运行段由框架后台驱动，后续 ``yield`` 逐段投递 EVENT 消息。
    工具声明上使用类级 ``definition`` 显式给出，``strict=False``——
    ``run-workflow`` 的运行参数不止 ``path``，其余由 ``Workflow.run()``
    签名决定，工具层不做参数限制。

    .. rubric:: 行为要点

    - 首 yield 前只做轻量准备：``resolve_workflow(path)`` → 实例化
      ``wf_class(caller, caller.runtime)``。首 yield 产出收据
      ``{"status": "started", "workflow": <path>}``，框架包装为
      ``status="pending"`` 的 ``ToolResult`` 并携带后台任务注册键
      （``background_task_id``）；收据在 ``run()`` 完成之前返回。
    - 后台运行段执行 ``await instance.run(**args)`` （``path`` 不从
      ``args`` 透传给 ``run()``）；完成时末 yield 产出
      ``{"status": "done", "workflow": <path>}``，框架投递为 EVENT 消息
      （LLM 可见）。
    - workflow 的数据结果不由本工具交付——编排代码自行
      ``caller.enqueue_message(...)`` 投递（框架的完成消息与编排的结果
      消息两条并存，角色不同：状态 vs 数据）。
    - ``path`` 解析失败（首 yield 前异常）→ ``Tool.__call__`` 按 except
      顺序分派：``Intercepted`` → ``blocked``、其余 → ``status="error"``
      的 LLM 可见结果；后台运行段异常（``run()`` 抛错）→ 框架投递 EVENT
      错误块 + 诊断日志（双通道，LLM 可见）。
    - 本工具不等待运行完成（收据立即返回）；不持久化运行状态；不做并发
      数 / 节点数上限检查。

    .. seealso::

        - :class:`Workflow` —— 被拉起的对象。
        - :func:`resolve_workflow` —— 按路径解析 workflow 定义。
        - :class:`flowing.tool.ScriptTool` —— 基类（async generator
          形态契约见其类 docstring）。
    """

    definition: ToolDefinition = ToolDefinition(
        name="run-workflow",
        description="Launch an orchestration workflow. The path parameter is the workflow "
                    "definition file path (relative to the @/ project root); remaining "
                    "arguments are passed through to Workflow.run().",
        params_schema={"path": {"type": "string",
                                "description": "path of the workflow definition file"}},
        strict=False,   # 其余参数透传给 Workflow.run()
    )
    """类级默认声明：规范名 ``run-workflow``、参数表只含 ``path``、
    ``strict=False``——运行参数由 ``Workflow.run()`` 签名决定，工具层
    不做参数限制（与 :class:`flowing.plugins.skills.SkillLoadTool`
    同构的“薄入口 + 内层校验”形态）。
    """

    async def execute(self, *, path: str, caller: Agent, **args: Any):
        """async generator 形态后台启动指定 workflow（语义见类 docstring）。

        .. rubric:: 行为要点

        - 首 yield 前（轻量准备）：``resolve_workflow(path)`` → 实例化
          ``wf_class(caller, caller.runtime)``——异常经 ``Tool.__call__``
          的 except 顺序分派（``Intercepted`` → ``blocked``；其余 →
          ``status="error"`` 的 LLM 可见结果）。
        - 首 yield：收据 ``{"status": "started", "workflow": path}``
          （框架包装为 ``status="pending"`` 的 ``ToolResult`` 并携带后台
          任务注册键）。
        - 后台运行段：``await instance.run(**args)`` （由框架后台驱动；
          ``path`` 不从 ``args`` 透传给 ``run()``）。
        - 末 yield：最终呈现 ``{"status": "done", "workflow": path}``
          （框架投递为 EVENT 消息，LLM 可见）；运行段异常由框架投递
          EVENT 错误块 + 诊断日志（双通道，LLM 可见）。
        - 本方法不等待运行完成；不把 ``run()`` 的返回值写进收据。

        :param path: workflow 定义文件路径（支持 ``@/`` 前缀）。
        :param caller: 调用方 Agent（LLM 入口恒非 None）。
        :param args: 传给 ``Workflow.run()`` 的运行参数。

        .. seealso:: :func:`resolve_workflow`、:meth:`Workflow.run`
        """
        wf_class: type[Workflow] = resolve_workflow(path, project_root=caller.runtime.project_root)   # 轻量准备（首 yield 前——出错 → error/blocked 结果）
        instance = wf_class(caller, caller.runtime)
        yield {"status": "started", "workflow": path}       # ① 收据（pending，TOOL 消息带 path）
        await instance.run(**args)                          # 长任务（后台，由 _drive_asyncgen 驱动；path 不透传）
        yield {"status": "done", "workflow": path}          # ② 最终呈现（EVENT）


class WorkflowPlugin(Plugin):
    """Workflow 扩展插件——阶段一入口：注册 ``run-workflow`` 工具并提供 Workflow 根创建入口。

    .. rubric:: 功能介绍

    ``runtime.install(WorkflowPlugin())`` 时框架调用 ``install(runtime)``：
    向 ``runtime.tool_registry`` 注册 :class:`RunWorkflowTool`，并保存
    runtime 引用（供 :meth:`launch` 使用）。之后 Agent 经
    ``add_tool("run-workflow")`` 绑定该工具，即可让 LLM 按路径拉起
    任意 workflow 定义；宿主 / 应用代码经 ``runtime.get_plugin("workflow")
    .launch(path)`` 创建 Workflow 根。

    Workflow 的能力本体是 :class:`Workflow` 基类（import 即用，不需要
    实例级启用）；本插件负责两件事：把 LLM 触发入口接上（``run-workflow``
    工具），以及提供 Workflow 根创建入口（``launch``，需注册以取得
    runtime）。不需要 LLM 入口、也不建根（纯代码驱动子节点）的应用可以
    不安装本插件，``Workflow`` 基类与 :func:`resolve_workflow` 照常可用。

    .. rubric:: 使用示例

    .. code-block:: python

        runtime.install(WorkflowPlugin())          # 阶段一：注册工具 + 保存 runtime
        agent = await runtime.create_agent("main-agent")
        agent.add_tool("run-workflow")         # 绑定到具体 Agent（LLM 可见）

        # Workflow 根（等价旧 mount 的 Workflow 路径）：
        wf = runtime.get_plugin("workflow").launch("@/pipelines/release.py")

    .. rubric:: 行为要点

    - ``install()`` 同步完成：注册工具 + 保存 runtime；不创建任何
      workflow 实例、不扫描任何目录（资源发现发生在
      :func:`resolve_workflow` 被调用时）。
    - :meth:`launch` 依赖 install 保存的 runtime——未注册（未
      ``runtime.install(WorkflowPlugin())``）时调用抛 ``ValueError``。
    - 本插件不声明钩子点、不挂任何 handler；不注册 provide 值、不声明
      Agent 状态键。
    - 重复安装同名插件（再次 ``runtime.install(WorkflowPlugin())``）抛
      ``ValueError`` （``Runtime.install`` 的同名插件查重）。
    - 工具规范名 ``run-workflow`` 已被注册时 →
      :class:`flowing.errors.ToolNameConflictError`。

    :raises flowing.errors.ToolNameConflictError: 工具规范名
        ``run-workflow`` 已被注册时。

    .. seealso::

        - :class:`RunWorkflowTool`、:class:`Workflow`
        - :class:`flowing.plugins.Plugin` —— 插件基类契约。
    """

    name: ClassVar[str] = "workflow"
    """注册名（显式声明，无框架默认）。推荐格式：类名去 ``Plugin`` 后缀转
    kebab-case（``WorkflowPlugin`` → ``"workflow"``）。
    """

    dependencies: ClassVar[list[str]] = []
    """依赖声明（类属性元数据）。本插件无依赖，为空列表。

    .. seealso:: :meth:`flowing.runtime.Runtime.install` —— 依赖校验在该
        方法内执行。
    """

    _runtime: Runtime | None = None
    """注册时保存的 Runtime 实例（``install`` 赋值，:meth:`launch` 使用）。

    未注册（``runtime.install(WorkflowPlugin())`` 未调用）时为 ``None``——
    :meth:`launch` 据此拒绝未注册使用。保存 runtime 供 launch 是用户显式
    调用的工厂路径，非运行期回调，与插件约定“不保存 runtime 用于运行期
    回调”不冲突。
    """

    def install(self, runtime: Runtime) -> None:
        """阶段一：注册 ``run-workflow`` 工具并保存 runtime（供 ``launch`` 使用）。

        .. rubric:: 行为要点

        - ``runtime.register_tool(RunWorkflowTool())``，同步返回；不创建
          任何 workflow 实例、不扫描任何目录（资源发现发生在
          :func:`resolve_workflow` 被调用时）。
        - 保存 ``runtime`` 到 :attr:`_runtime`——:meth:`launch` 创建
          Workflow 根时以此构造（root：``caller=None``）。

        .. seealso:: :class:`RunWorkflowTool`、:meth:`launch`
        """
        self._runtime = runtime
        runtime.register_tool(RunWorkflowTool())

    def launch(self, path: str) -> Workflow:
        """按 Workflow 定义文件创建 Workflow 根（``caller=None`` 的根节点）。

        .. rubric:: 功能介绍

        Workflow 根的创建入口（原 ``Runtime.mount`` 的 Workflow 等价路径
        迁移至此）：``resolve_workflow(path)`` 解析定义文件（文件内需恰好
        一个 ``Workflow`` 子类），以 ``caller=None``（根节点语义）与注册
        时保存的 runtime 实例化。返回的 Workflow 已就位于节点树与 provide
        链（构造即完成，见 :class:`Workflow`）。

        .. rubric:: 使用示例

        .. code-block:: python

            runtime.install(WorkflowPlugin())
            wf = runtime.get_plugin("workflow").launch(
                "@/pipelines/release.py")

        .. rubric:: 行为要点

        - 前置条件：本插件已注册（``runtime.install(WorkflowPlugin())``）——
          未注册时抛 :class:`ValueError`（launch 依赖 install 保存的
          runtime）。
        - ``caller=None``：Workflow 是根节点（``_parent_id`` 指向
          Runtime）；经 ``run-workflow`` 工具拉起的 Workflow 由调用方
          Agent 作 ``caller``，走 :class:`RunWorkflowTool` 的构造路径
          （与 launch 同为 ``workflow_class(caller, runtime)`` 形态）。
        - 同步方法：Workflow 构造是同步的（分配 ``node_id``、就位节点树
          与 provide 链）；不传额外构造参数（``Workflow.__init__`` 固定
          ``(caller, runtime)`` 两参，子类初始化参数由定义文件 / 默认值
          承载）。

        :param path: Workflow 定义文件路径（支持 ``@/`` 前缀规则）。
        :return: 已就位的 ``Workflow`` 根实例。
        :raises ValueError: 本插件未注册（``runtime.install(WorkflowPlugin())``
            未调用）时。
        :raises flowing.errors.FlowingError: 路径缺失或文件形态不合法时
            （经 :func:`resolve_workflow`）。

        .. seealso:: :func:`resolve_workflow`、:class:`Workflow`、
            :class:`RunWorkflowTool`
        """
        if self._runtime is None:
            raise ValueError(
                "WorkflowPlugin not registered: call runtime.install(WorkflowPlugin()) before launch")
        workflow_class = resolve_workflow(path, project_root=self._runtime.project_root)
        return workflow_class(caller=None, runtime=self._runtime)
