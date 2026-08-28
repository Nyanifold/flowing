"""承载 ``resolve_workflow``——裸 workflow 文件解析：按路径解析定义文件并返回 ``Workflow`` 子类。
"""

from flowing.runtime import resolve

from .workflow import Workflow


def resolve_workflow(path: str) -> type[Workflow]:
    """按路径解析 workflow 定义文件，返回 ``Workflow`` 子类（M-101 最终裁决：
    纯路径解析，无 ``workflows/`` 约定目录）。

    .. rubric:: 功能介绍

    ``path`` → ``Workflow`` 子类。接受的两种路径写法：

    - **绝对路径**：指向 ``.py`` 文件（如 ``/home/user/proj/verify_fix.py``）。
    - **带路径符号的 kebab-case**：如 ``flows/verify-fix``——按
      :meth:`flowing.runtime.Runtime.resolve_path` 语义解析
      （``@/`` 前缀为项目根相对），末尾 kebab-case 段依次尝试两个
      候选文件名：先原样补 ``.py``（``flows/verify-fix.py``），
      再将连字符转为下划线（``flows/verify_fix.py``）——underscore
      式文件名同样兼容；两候选都命中属歧义，抛 ``FlowingError``。

    **不设任何默认目录约定**——workflow 文件可以放在任意位置（包括
    面对任务即时在工作区写下的新文件）。查找发生在调用时刻，无启动期
    扫描、无缓存。

    **调用形态不变量（M-101 补充裁决）**：目前所有 Workflow 调用都是
    「加载文件 → 实例化 → 一次性调用」；**不支持** workflow 实例的注册
    与持久化——无跨运行状态、无注册表、崩溃不续跑（与 ``Workflow`` 类
    docstring「每次运行拉起一个新对象」一致）。

    **文件两种形态**（加载时判定，互斥）：

    1. **类形态**：文件内恰好一个 ``Workflow`` 子类 → 直接返回该类。
    2. **函数形态**：文件内没有 ``Workflow`` 子类、但有一个顶层核心函数
       ``async def run(prompt=None, ...)``（**无 self 参数**）→ 经字符串
       处理编译为生成的 ``Workflow`` 子类后返回（见下「函数形态编译」）。

    .. rubric:: 设计动机

    Workflow 定义即资源：一个文件一个编排。旧设计的全局
    ``WorkflowRegistry`` 注册表与「``workflows/`` 约定目录」均已废弃——
    注册表要求启动期枚举（与现场解析矛盾），约定目录把「能不能跑一个
    工作流」绑死在目录布局上；路径解析两者都回避，且天然支持动态写入
    的工作流文件。

    **函数形态编译**（字符串处理 → ``compile``，与 ``.fya`` 的 ``$script``
    编译同族）：

    - 签名注入：``async def run(prompt=None, ...)`` 编译为方法——参数
      列表首位注入 ``self``（源文件中**不写** ``self``）。
    - 裸名改写：函数体内裸写的 ``create_agent(...)``、``tool_call(...)``
      与简写 ``agent(...)`` 一律改写为 ``self.create_agent(...)`` /
      ``self.tool_call(...)``——驱动 Agent 与工具的入口不变（见本模块
      「专属角度二」），只是免去 ``self.`` 前缀与 import。
    - 生成类：类名由文件名推导（kebab-case → PascalCase，如
      ``verify-fix.py`` → ``VerifyFix``）；``run`` 即编译后的函数。
    - 改写只作用于调用表达式（``create_agent(`` / ``agent(`` /
      ``tool_call(`` 后随括号），不误伤字符串与注释之外的同名标识符属
      编译器职责内的已知限制，文档不承诺更复杂的语义分析。

    .. rubric:: 使用示例

    .. code-block:: python

        # verify-fix.py（函数形态——无 import、无 class、无 self）
        async def run(prompt=None, max_rounds: int = 3):
            verifier = await create_agent("verifier-agent")   # 裸名
            result = await verifier.query("运行 tsc --noEmit")
            if result.status == "pass":
                return {"status": "passed"}
            fixer = await agent("fixer-agent")                # 简写等价
            ...

        # 调用侧
        wf_class = resolve_workflow("@/verify-fix.py")
        instance = wf_class(caller, runtime)
        result = await instance.run(prompt="检查并修复")

    .. rubric:: 行为规约

    - 期待行为：命中后返回类对象（**不实例化**——实例化需要 caller 与
      runtime，由调用方完成）；函数形态返回的是生成的 ``Workflow``
      子类，``issubclass(ret, Workflow)`` 恒为 True。
    - **手动调用契约（显式异常）**：本函数对调用方的承诺是「要么返回
      合法的 ``Workflow`` 子类，要么抛出带定位信息的异常」——不返回
      ``None``、不返回占位对象、不静默降级。代码直调方（应用 ``main()``、
      其它 workflow、插件）依赖异常控制流，无需也不应做返回值判空。
      注意区分调用路径：手动调用异常原样上抛；经 ``run-workflow``
      工具（LLM 入口）时同一异常被 ``Tool.__call__`` 包装为
      ``status="error"`` 的 LLM 可见结果——包装只发生在工具边界。
    - 边缘情况：路径不存在 → :class:`flowing.errors.FlowingError`
      （消息含解析后的路径）；文件内有两个以上 ``Workflow`` 子类、或
      既无子类又无顶层 ``run`` 函数 → 同样抛 ``FlowingError``（消息
      说明歧义/缺失）。两类形态同时出现（有子类又有顶层 ``run``）→
      类形态优先，函数被忽略。
    - 前置条件：需经 ``flowing.launch(...)`` 登记的项目上下文（``@``
      绑定）可用；未经 launch 的裸进程调用本函数抛 ``RuntimeError``。
    - 非行为：不做 glob、不递归、不缓存（每次调用现场解析——本地
      不缓存原则）；不执行文件顶层代码以外的任何初始化（导入副作用
      与 ``.fya`` 加载一致：顶层语句执行一次）。

    :raises flowing.errors.FlowingError: —— 路径缺失或文件形态不合法时。
    :raises RuntimeError: —— 无 ``launch`` 登记的项目上下文时。

    .. rubric:: 测试案例

    - 前置：``verify_fix.py`` 含一个 ``Workflow`` 子类。操作：
      ``resolve_workflow("@/verify_fix.py")``。期望：返回该类；
      ``issubclass(ret, Workflow)`` 为 True。
    - 前置：``quick.py`` 只含 ``async def run(prompt=None)``，体内裸写
      ``await agent("x-agent")``。操作：解析并实例化运行。期望：返回
      生成的 ``Workflow`` 子类；运行中 ``agent(...)`` 实际调用
      ``self.create_agent(...)``。
    - 前置：路径 ``@/ghost.py`` 不存在。操作：解析。期望：
      ``FlowingError``。
    - 前置：文件内含两个 ``Workflow`` 子类。操作：解析。期望：
      ``FlowingError``（歧义）。

    .. rubric:: 调用关系（审计）

    - 调用：按 ``flowing.runtime.Runtime.resolve_path`` 语义解析路径
      （时机：每次调用现场解析，无启动期扫描、无缓存）；函数形态经
      字符串处理 + ``compile`` 编译为生成的 ``Workflow`` 子类（具体
      调用目标未见规约）
    - 被调：``flowing.plugins.workflow.RunWorkflowTool.execute()``
      （时机：每次 LLM 经 ``run-workflow`` 工具调用）；
      ``flowing.runtime.Runtime.mount()`` 的 Workflow 根形态（时机：
      每次以 Workflow 定义文件路径 mount，见 runtime.pyi ``mount()``
      规约）；代码直调为用户代码

    .. seealso:: :class:`Workflow`、:class:`RunWorkflowTool`、
        :meth:`flowing.runtime.Runtime.resolve_path`
    """
    resolved = resolve(path)  # -> flowing.runtime.resolve（模块级 @/ 解析，读 _current_project_root）
    # 未 launch 登记（contextvar 为 None）-> RuntimeError；路径不存在 -> FlowingError（消息含解析后路径）
    # kebab-case 末尾段依次尝试两候选：原样补 .py，再将连字符转下划线；两候选都命中 -> FlowingError（歧义）
    # 加载文件（顶层语句执行一次，与 .fya 一致；加载器未见具名符号）后判定形态（互斥判定，类形态优先）：
    #   1. 类形态：恰好一个 Workflow 子类 -> 直接返回该类；两个以上 -> FlowingError（歧义）
    #   2. 函数形态：无子类但有顶层 async def run（无 self）-> 字符串处理 + compile 生成子类
    #      （参数列表首位注入 self；裸名 create_agent/tool_call/agent 改写为 self.*；类名 kebab-case -> PascalCase）
    #   既无子类又无顶层 run -> FlowingError（缺失）
    wf_class: type[Workflow] = resolved  # type: ignore[assignment]  # 占位：上述形态判定的产物；issubclass(ret, Workflow) 恒为 True
    return wf_class  # 手动调用契约：要么返回合法子类，要么抛带定位信息的异常（不返回 None/占位对象）
