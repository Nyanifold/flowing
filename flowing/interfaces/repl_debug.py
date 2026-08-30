"""``flowing.interfaces.repl_debug`` —— 调试扩展版 REPL 子命令（``repl-debug``）。

.. rubric:: 定位

``flowing repl-debug <path>`` 是 :func:`flowing.interfaces.repl.cmd_repl`
的**调试扩展版**：

- 继承 ``repl`` 的全部行为：启动绑定、提示符、非 ``/`` 输入投递、
  既有 7 个 slash 命令、EOF / ``/exit`` 退出。
- 额外提供 4 个调试 slash 命令：``/eval``、``/watch``、
  ``/eval-runtime``、``/watch-runtime``。
- 默认 ``repl`` 的封闭 slash 命令集不变；调试命令只在 ``repl-debug``
  中启用。

.. rubric:: 命令语法

============================= ============================ ==========================================
命令                          语法                        说明
============================= ============================ ==========================================
``/eval <expr>``              ``/eval current_mode``       以当前绑定 Agent 为上下文解析并打印表达式
``/watch <expr>``             ``/watch current_mode``      以当前绑定 Agent 为上下文解析并加入监视
``/eval-runtime <expr>``      ``/eval-runtime self.snapshot().plugins``
                                                           以 Runtime 为上下文解析并打印表达式
``/watch-runtime <expr>``     ``/watch-runtime self.snapshot().agents``
                                                           以 Runtime 为上下文解析并加入监视
============================= ============================ ==========================================

- ``<expr>`` 是 **Jinja2 表达式体**，不需要写外层 ``{{ }}``；本命令
  包装为 ``{{ <expr> }}`` 后求值。
- ``<expr>`` 为空时：打印该命令用法提示，不执行。

.. rubric:: 解析上下文

Agent 上下文（``/eval``、``/watch``）：

- 必须存在当前绑定 Agent；未绑定（``(new agent)>>>``）时打印：
  ``未绑定 Agent：无法解析表达式``。
- 有绑定时，使用 ``agent.parsable("{{ " + expr + " }}").resolve(agent)``，
  与 flowing 其它 ``Parsable`` 求值共用同一套上下文语义（``env`` /
  ``config`` / 实例属性 / Parsable 字段等）。
- 绑定切换（``/use <id>``）后，``/eval`` 和已注册的 Agent watch 都改用
  新绑定 Agent。

Runtime 上下文（``/eval-runtime``、``/watch-runtime``）：

- 上下文 = Runtime 实例，且**必须**以 ``self`` 作为 Runtime 变量名：
  ``self = runtime``。
- 使用 ``Parsable("{{ " + expr + " }}").resolve({"self": runtime})``。
- 不依赖当前是否绑定 Agent；未绑定也能执行。

.. rubric:: 错误处理

- 求值抛出任何异常时，**原样打印异常**：``print(str(exception))``；
  不包装、不改写、不追加 traceback；进程不退出，回到当前提示符。
- 未绑定 Agent 时 ``/eval``、``/watch`` 打印上述特殊提示，不进入求值。

.. rubric:: ``/eval`` 语义

- 立即求值一次并打印结果；打印格式为 ``print(value)``。
- 不记录、不加入 watch。

.. rubric:: ``/watch`` 语义

- 首次执行：
  1. 立即求值一次；
  2. 打印 ``[watch] <expr> => <value>``；
  3. 将 ``(expr, 最后值)`` 加入当前绑定 Agent 的 watch 列表。
- 后续每次 REPL 循环**打印下一个提示符之前**，对 watch 列表逐条重新求值：
  - 值未变：不打印；
  - 值发生变化：打印 ``[watch] <expr> => <new value>``；
  - 求值报错：按错误处理规则打印错误，并保留该 watch（下次继续尝试）。
- Agent watch 与绑定状态：
  - 绑定 Agent 切换后，Agent watch 上下文变为新 Agent；
  - 退出 / EOF 时 watch 随进程消失，不持久化。
- 值比较使用 ``!=``。

.. rubric:: ``/eval-runtime`` / ``/watch-runtime`` 语义

- 与 ``/eval`` / ``/watch`` 相同，只是上下文为 ``{"self": runtime}``；
- Runtime watch 不依赖绑定 Agent，绑定切换不影响它。

.. rubric:: 与默认 ``repl`` 的关系

- ``repl-debug`` 复用 ``repl`` 的启动、绑定、读行循环、slash 命令、
  退出流程；实现方式是利用 ``cmd_repl`` 的扩展注入点
  （``extra_slash_handlers`` / ``pre_prompt_hook`` / ``extra_help_text``）。
- 默认 ``repl`` 不传这些注入点，行为完全不变。
- 默认 ``repl`` 的 ``/help`` 不列出 debug 命令；``repl-debug`` 的
  ``/help`` 额外列出 4 个命令。

.. rubric:: 测试案例

- 前置：已绑定 Agent。操作：``/eval model_tag``。期望：打印当前
  ``model_tag`` 值，不产生消息。
- 前置：已绑定 Agent。操作：``/watch current_mode`` 后修改
  ``current_mode`` 并再次进入提示符。期望：首次打印
  ``[watch] ...``，变更后自动打印新值。
- 前置：未绑定状态。操作：``/eval model_tag``。期望：打印
  ``未绑定 Agent：无法解析表达式``。
- 前置：已绑定 Agent。操作：``/eval {{ bad syntax``。期望：原样打印
  异常，不退出。
- 前置：任意。操作：``/eval-runtime self.snapshot().plugins``。期望：
  打印 Runtime 快照中的插件列表。
- 前置：默认 ``repl``。操作：``/eval model_tag``。期望：仍按未知命令
  处理，不执行求值。
"""

from typing import Any

from flowing.agent import Agent
from flowing.parsable import Parsable
from flowing.runtime import Runtime

from flowing.interfaces.repl import cmd_repl


def _wrap_expr(expr: str) -> str:
    """把用户表达式包装为 Jinja2 EXPRESSION 源。"""
    return "{{ " + expr + " }}"


def _eval_agent(agent: Agent, expr: str) -> tuple[bool, Any]:
    """以 Agent 为上下文求值；返回 ``(ok, value_or_exception)``。"""
    try:
        return True, agent.parsable(_wrap_expr(expr)).resolve(agent)
    except Exception as exc:   # 调试命令：任何错误都原样交给调用方打印
        return False, exc


def _eval_runtime(runtime: Runtime, expr: str) -> tuple[bool, Any]:
    """以 Runtime 为上下文求值（``self = runtime``）。"""
    try:
        # 渲染上下文同时放 "self" 与 "agent" 两键：框架 Jinja 环境在 AST 级
        # 把名字 self 改写为 agent（见 flowing.parsable._SelfToAgentTransformer
        # ——Jinja2 代码生成器劫持根作用域 self 为 TemplateReference），Mapping
        # 上下文里只放 "self" 时改写后的 agent 名义不可达；两键同值保证
        # {{ self.snapshot() }} 与 {{ agent.snapshot() }} 都能求值
        return True, Parsable(_wrap_expr(expr)).resolve({"self": runtime, "agent": runtime})
    except Exception as exc:
        return False, exc


async def cmd_repl_debug(
    path: str,
    main_file: str | None = None,
    **kwargs: str | bool,
) -> int:
    """``flowing repl-debug <path>``：继承 ``repl`` 并附加调试表达式命令。

    完整 spec 见本模块 docstring；本函数只负责构造 4 个调试命令的
    handler、watch 状态与提示前检查钩子，然后委托
    :func:`flowing.interfaces.repl.cmd_repl`。
    """
    watches: list[dict[str, Any]] = []

    async def _print_eval_agent(arg: str, agent: Agent | None, runtime: Runtime) -> None:
        if arg == "":
            print("用法：/eval <expr>")
            return
        if agent is None:
            print("未绑定 Agent：无法解析表达式")
            return
        ok, value = _eval_agent(agent, arg)
        print(value)

    async def _watch_agent(arg: str, agent: Agent | None, runtime: Runtime) -> None:
        if arg == "":
            print("用法：/watch <expr>")
            return
        if agent is None:
            print("未绑定 Agent：无法解析表达式")
            return
        ok, value = _eval_agent(agent, arg)
        if not ok:
            print(value)
            return
        watches.append({"ctx": "agent", "expr": arg, "last": value})
        print(f"[watch] {arg} => {value}")

    async def _print_eval_runtime(arg: str, agent: Agent | None, runtime: Runtime) -> None:
        if arg == "":
            print("用法：/eval-runtime <expr>")
            return
        ok, value = _eval_runtime(runtime, arg)
        print(value)

    async def _watch_runtime(arg: str, agent: Agent | None, runtime: Runtime) -> None:
        if arg == "":
            print("用法：/watch-runtime <expr>")
            return
        ok, value = _eval_runtime(runtime, arg)
        if not ok:
            print(value)
            return
        watches.append({"ctx": "runtime", "expr": arg, "last": value})
        print(f"[watch] {arg} => {value}")

    async def _check_watches(agent: Agent | None, runtime: Runtime) -> None:
        for w in watches:
            if w["ctx"] == "agent":
                if agent is None:
                    print("未绑定 Agent：无法解析表达式")
                    continue
                ok, value = _eval_agent(agent, w["expr"])
            else:
                ok, value = _eval_runtime(runtime, w["expr"])
            if not ok:
                print(value)
            elif value != w["last"]:
                w["last"] = value
                print(f"[watch] {w['expr']} => {value}")

    extra_slash_handlers = {
        "/eval": _print_eval_agent,
        "/watch": _watch_agent,
        "/eval-runtime": _print_eval_runtime,
        "/watch-runtime": _watch_runtime,
    }
    extra_help_text = (
        "/eval <expr>  /watch <expr>  /eval-runtime <expr>  /watch-runtime <expr>"
    )
    return await cmd_repl(
        path,
        main_file=main_file,
        extra_slash_handlers=extra_slash_handlers,
        pre_prompt_hook=_check_watches,
        extra_help_text=extra_help_text,
        **kwargs,
    )
