"""承载 ``resolve_workflow``——裸 workflow 文件解析：按路径解析定义文件并返回 ``Workflow`` 子类。
"""

import ast
import importlib.util
import inspect
from pathlib import Path
from uuid import uuid4

from flowing.errors import FlowingError
from flowing.runtime import resolve

from .workflow import Workflow

_BARE_CALL_REWRITES = {"create_agent": "create_agent", "agent": "create_agent",
                       "tool_call": "tool_call"}
"""函数形态编译的裸名改写表：``agent(...)`` 是 ``create_agent(...)`` 的简写。
"""


class _BareCallRewriter(ast.NodeTransformer):
    """调用表达式级裸名改写：``create_agent(`` / ``agent(`` / ``tool_call(``
    （``ast.Name`` 直接调用）前补 ``self.``（``agent`` 归一到
    ``create_agent``）。ast 级改写天然不误伤字符串与注释；属性调用
    （``x.create_agent(...)``）与同名局部变量遮蔽之外的语义分析不做
    （已知限制，文档不承诺）。"""

    def visit_Call(self, node: ast.Call) -> ast.AST:
        self.generic_visit(node)
        func = node.func
        if isinstance(func, ast.Name) and func.id in _BARE_CALL_REWRITES:
            node.func = ast.Attribute(
                value=ast.Name(id="self", ctx=ast.Load()),
                attr=_BARE_CALL_REWRITES[func.id],
                ctx=ast.Load(),
            )
        return node


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
    # 未 launch 登记（contextvar 为 None）-> RuntimeError（resolve 内部抛出）
    file_path = _locate_file(resolved, raw=path)
    return _load_workflow_class(file_path)


def _locate_file(resolved: Path, *, raw: str) -> Path:
    """定位定义文件：直接命中即用；无 ``.py` 后缀的 kebab 末段依次尝试
    两候选（原样补 ``.py`` → 连字符转下划线补 ``.py``），双命中歧义抛错。
    """
    if resolved.is_file():
        return resolved
    if resolved.suffix == ".py":
        raise FlowingError(f"workflow 定义文件不存在：{resolved}")
    stem = resolved.name
    candidates = [resolved.with_name(stem + ".py")]   # 候选一：原样补 .py
    snake = stem.replace("-", "_")
    if snake != stem:
        candidates.append(resolved.with_name(snake + ".py"))   # 候选二：kebab → snake
    hits = [c for c in candidates if c.is_file()]
    if len(hits) > 1:
        raise FlowingError(
            f"workflow 路径歧义：{raw!r} 同时命中 {hits[0]} 与 {hits[1]}")
    if not hits:
        raise FlowingError(
            f"workflow 定义文件不存在：{resolved}（已尝试候选："
            + "、".join(str(c) for c in candidates) + "）")
    return hits[0]


def _load_workflow_class(file_path: Path) -> type[Workflow]:
    """加载定义文件（顶层语句执行一次，与 ``.fya`` 加载一致）并做两形态
    互斥判定：恰好一个本文件定义的 ``Workflow`` 子类 → 直接返回（类形态
    优先）；否则有合法顶层 ``async def run``（无 self）→ 函数形态编译。
    """
    module_name = f"flowing_workflow_{file_path.stem}_{uuid4().hex[:8]}"
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)   # 顶层语句执行一次；原生 import 异常直接上抛
    # 只数本文件定义的子类（__module__ 判别——import 进来的他文件子类不算，
    # 否则「复用 import」会被误判为歧义）
    classes = [
        obj for obj in vars(module).values()
        if isinstance(obj, type) and issubclass(obj, Workflow)
        and obj is not Workflow and obj.__module__ == module_name
    ]
    if len(classes) > 1:
        raise FlowingError(
            f"workflow 文件形态歧义：{file_path} 含 {len(classes)} 个 "
            f"Workflow 子类（{[c.__name__ for c in classes]}），一个文件一个编排")
    if classes:
        return classes[0]   # 类形态优先（与顶层 run 并存时忽略 run）
    run_fn = vars(module).get("run")
    if run_fn is None:
        raise FlowingError(
            f"workflow 文件形态缺失：{file_path} 既无 Workflow 子类也无顶层 "
            f"async def run")
    params = list(inspect.signature(run_fn).parameters)
    if not inspect.iscoroutinefunction(run_fn) or (params and params[0] == "self"):
        raise FlowingError(
            f"workflow 函数形态不合法：{file_path} 的顶层 run 必须是"
            f" async def 且无 self 参数（self 由编译注入）")
    return _compile_function_form(module, file_path)


def _compile_function_form(module: object, file_path: Path) -> type[Workflow]:
    """函数形态编译（字符串/ast 处理 → compile，与 ``.fya`` 的 ``$script``
    编译同族）：参数列表首位注入 ``self``；裸名 ``create_agent`` /
    ``agent`` / ``tool_call`` 调用改写为 ``self.*``；类名由文件名推导
    （kebab/snake → PascalCase，如 ``verify-fix.py`` → ``VerifyFix``）。
    """
    source = file_path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(file_path))
    run_node = next(
        node for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == "run"
    )
    run_node.args.args.insert(0, ast.arg(arg="self", annotation=None))   # 签名首位注入 self
    _BareCallRewriter().visit(run_node)   # 裸名调用改写（调用表达式级）
    ast.fix_missing_locations(run_node)
    code = compile(ast.Module(body=[run_node], type_ignores=[]),
                   filename=str(file_path), mode="exec")
    env = dict(vars(module))   # 继承模块全局（顶层 import 等绑定对 run 可见）
    exec(code, env)
    class_name = "".join(
        part.capitalize()
        for part in file_path.stem.replace("_", "-").split("-") if part
    )
    return type(class_name, (Workflow,),
                {"run": env["run"], "__module__": getattr(module, "__name__", None)})
