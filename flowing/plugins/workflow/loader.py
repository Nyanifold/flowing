"""承载 ``resolve_workflow``——按路径解析 workflow 定义文件并返回 ``Workflow`` 子类。

本模块只做「路径 → 类」解析：不实例化、不运行，实例化由调用方完成
（需要 caller 与 runtime）。
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
    """按路径解析 workflow 定义文件，返回 ``Workflow`` 子类（类对象，不实例化）。

    .. rubric:: 功能介绍

    ``path`` 指向一个 workflow 定义文件，本函数返回该文件中定义的
    :class:`Workflow` 子类。查找发生在调用时刻：不做启动期扫描、不缓存，
    workflow 文件可以放在任意位置，没有默认目录约定（面对任务即时写下的
    新文件也可以直接引用）。接受两种路径写法：

    - 绝对路径，或 ``@/`` 前缀的项目根相对路径（``@`` 上下文由
      :func:`flowing.launch` 登记），如 ``"@/flows/verify_fix.py"``；
    - 无 ``.py`` 后缀的 kebab-case 路径，如 ``"@/flows/verify-fix"``：
      先按原样补 ``.py``（``flows/verify-fix.py``），未命中再把连字符转
      下划线补 ``.py``（``flows/verify_fix.py``）；两个候选同时存在属
      歧义，抛 :class:`flowing.errors.FlowingError`。

    定义文件两种形态（加载时判定，互斥）：

    1. 类形态：文件内恰好一个由该文件自身定义的 ``Workflow`` 子类
       （import 进来的他处子类不计）——直接返回该类；文件同时存在顶层
       ``run`` 函数时忽略函数（类形态优先）。
    2. 函数形态：文件内没有 ``Workflow`` 子类，但有一个顶层
       ``async def run(prompt=None, ...)``（不带 ``self`` 参数）——
       编译为生成的 ``Workflow`` 子类后返回。函数形态下 ``run`` 体内可
       直接调用 ``create_agent(...)``（简写 ``agent(...)`` 等价）与
       ``tool_call(...)``，免写 ``self.`` 前缀与 import；生成类名由文件
       名推导（kebab-case 转 PascalCase，如 ``verify-fix.py`` →
       ``VerifyFix``）。

    .. rubric:: 使用示例

    .. code-block:: python

        # verify-fix.py：函数形态定义文件（无 import、无 class、无 self）
        async def run(prompt=None, max_rounds: int = 3):
            verifier = await create_agent("verifier-agent")
            result = await verifier.query("运行 tsc --noEmit 并列出所有错误")
            if result.status == "completed" and "error" not in result.final_text:
                return {"status": "passed"}
            fixer = await agent("fixer-agent")      # agent(...) 是 create_agent(...) 的简写
            fix_result = await fixer.query("修复以上错误")
            await fixer.destroy()
            return {"status": "fixed"}

        # 调用侧（代码直接驱动；caller 为 None 时是根节点 workflow）
        wf_class = resolve_workflow("@/verify-fix.py")
        instance = wf_class(caller=None, runtime=runtime)   # runtime 为你的 Runtime 实例
        result = await instance.run(prompt="检查并修复")

    .. rubric:: 行为要点

    - 本函数对调用方的承诺是「要么返回合法的 ``Workflow`` 子类，要么抛出
      带定位信息的异常」：不返回 ``None``、不返回占位对象、不静默降级。
      代码直接调用方依赖异常控制流，无需判空返回值。经 ``run-workflow``
      工具（LLM 入口）调用时，同一异常由工具层包装为 ``status="error"``
      的 LLM 可见结果——包装只发生在工具边界。
    - 路径不存在 → :class:`flowing.errors.FlowingError`（消息含解析后的
      路径）；文件内有两个以上本文件定义的 ``Workflow`` 子类、或既无子类
      又无顶层 ``run`` 函数 → 同样抛 ``FlowingError``（消息说明歧义或
      缺失）。
    - 文件顶层语句随加载执行一次（与 ``.fya`` 加载一致）；每次调用现场
      解析，无缓存。
    - 未经 :func:`flowing.launch` 登记 ``@`` 上下文（裸进程）时抛
      ``RuntimeError``。

    :param path: workflow 定义文件的路径（支持 ``@/`` 前缀）。
    :return: 文件中定义的 ``Workflow`` 子类（类对象，不实例化）。
    :raises flowing.errors.FlowingError: 路径缺失或文件形态不合法时。
    :raises RuntimeError: 未经 ``launch`` 登记项目上下文时。

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
