"""flowing.plugins.clipboard —— 剪贴板扩展（ClipboardPlugin / use_clipboard）。

.. rubric:: 功能介绍

剪贴板是 Flowing 的**内置扩展**（随 ``flowing`` 包发布但不自动启用），
为 Agent 提供「文件区段的剪切 / 复制 / 粘贴」能力——面向 LLM 跨文件
搬运代码段的常见场景：LLM 先 cut/copy 一个区段（小内容进内存剪贴板、
大内容落文件），再 paste 到目标位置，避免在上下文里来回复述大段文本。

框架核心不内置剪贴板——它是策略性便利设施，由扩展承载；核心只提供它
复用的机制：插件状态持久化（``Agent.state`` ——剪贴板缓冲就是一个状态
键）、工具注册（``Runtime.register_tool``）、caller 注入（工具经
``caller`` 读写属主 Agent 的缓冲与阈值）。

.. rubric:: 注册面清单

- 启用方式：

  - 阶段一：``runtime.use(ClipboardPlugin())``——``install(runtime)``
    注册 ``clipboard-cut`` / ``clipboard-copy`` / ``clipboard-paste``
    三件全局工具（注册不等于可见：Agent 仍需 ``add_tool`` 才对 LLM
    可见）；
  - 阶段二：``setup()`` 中 ``use_clipboard(self)``——登记缓冲状态键并
    设定阈值实例属性。``use_clipboard`` 可重入：``state.register``
    幂等（键已持久化时跳过并返回持久值），重复调用不报错，阈值以
    最后一次调用为准；recover 在新实例重跑 ``setup()`` 同样安全。

- 注册的资源：

  - 工具注册：三件工具类（:class:`ClipboardCutTool` /
    :class:`ClipboardCopyTool` / :class:`ClipboardPasteTool`）注册进
    Runtime 全局工具注册表；对 LLM 可见仍需 Agent 侧显式声明
    （``.fya`` ``tools:`` 或 ``agent.add_tool(...)``）。
  - Agent 状态键：``clipboard_buffer`` （``state.register("clipboard_buffer",
    None)``，默认 ``None`` 表示空剪贴板；写透到该 Agent 自己的
    ``state.jsonl``，recover 可恢复）。
  - 阈值实例属性：``clipboard_max_lines`` / ``clipboard_max_chars`` （三件工具经 ``caller`` 读取）。


- 声明的钩子点：无（剪贴板无事件面）。
- 挂载的钩子：无。
- 未启用时的行为（未调用 ``use_clipboard`` 的 Agent）：零开销——无
  ``clipboard_buffer`` 状态键、无阈值属性；此时调用三件工具 → 
  「剪贴板未启用」error ``ToolResult``。

.. rubric:: 缓冲契约（state 键 clipboard_buffer）

- 值为 ``None`` （空）或 JSON 纯数据 dict：``{"content": str,
  "origin_path": str | None, "lines": int, "chars": int}`` （state 值
  必须 JSON 可序列化的硬约束——缓冲不落对象）。
- **每 Agent 一份** （per-agent state.jsonl 单 writer，多 Agent 天然
  隔离）；跨 Agent 搬运经文件输出中转。
- **一次性语义**：``paste`` 成功且来源为剪贴板 → 缓冲清空为
  ``None`` （防止长期占用 state.jsonl 体积）。
- **阈值双限**：cut/copy 进剪贴板的内容行数超过
  ``clipboard_max_lines`` （默认 500）**或**字符数超过
  ``clipboard_max_chars`` （默认 10k）→
  拒绝入缓冲，强制文件输出；文件输出无阈值。

.. rubric:: 位置与路径口径

- 路径遵循 **``cwd`` 基准制**（与 builtins 全部工具同口径）：
  ``cwd=None`` （默认）时一切文件参数仅收绝对路径；``cwd`` 非
  ``None`` 时允许相对路径（相对 ``cwd`` 解析）；``cwd`` 自身必须是
  绝对路径。相对路径无基准或 ``cwd`` 非绝对 → error ``ToolResult``。
  ``cwd`` 同时是「``.fya`` 定义期经智能体属性传参」的示范位
  （Parsable 覆写 ``cwd: "{{ cwd }}"``，完整示例见
  :mod:`flowing.builtins.tools` 模块 docstring）。
- 行号 **1 起**（对齐编辑器）、offset **0 起**（对齐 Python 切片）；
  区间一律 start 包含、end 排除；越界各自报错（fail fast）。

.. seealso:: :mod:`flowing.plugins.clipboard.tools`（三件工具）、
    :mod:`flowing.plugins.cron`（双层启用与同构的 state 键模式先例）
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from flowing.plugins import Plugin
from flowing.plugins.clipboard.tools import (
    ClipboardCopyTool,
    ClipboardCutTool,
    ClipboardPasteTool,
)

if TYPE_CHECKING:
    from flowing.agent import Agent
    from flowing.runtime import Runtime

__all__ = ["ClipboardPlugin", "use_clipboard"]


class ClipboardPlugin(Plugin):
    """剪贴板扩展插件——阶段一入口：注册三件工具。

    .. rubric:: 功能介绍

    ``runtime.use(ClipboardPlugin())`` 时框架调用 ``install(runtime)``，
    注册 ``clipboard-cut`` / ``clipboard-copy`` / ``clipboard-paste``
    三件全局工具。缓冲持久化不在 install 挂载——状态键是 per-agent
    声明，由 ``use_clipboard(self)`` 在各 Agent 的 ``setup()`` 中完成
    （与 cron 的 ``cron_jobs`` 同构）。

    .. rubric:: 行为要点

    - ``install()`` 同步完成三次 ``register_tool`` （插件约定 R1：
      install 只注册）；不挂载任何状态持久化；不读取其他插件状态。
    - 构造无参数、无注册副作用。

    .. seealso:: :func:`use_clipboard`（阶段二入口）
    """

    name: ClassVar[str] = "clipboard"
    """注册名（显式声明，无框架默认；推荐格式见 ``flowing.plugins``
    命名约定）。
    """

    dependencies: ClassVar[list[str]] = []   # 与基类 Plugin 的 ClassVar 对齐
    """依赖声明（类属性元数据）。本插件无依赖，为空列表。
    """

    def install(self, runtime: Runtime) -> None:
        """注册三件剪贴板工具到 Runtime 注册表。

        .. rubric:: 行为要点

        - 三次 ``register_tool`` 全部同步完成（install 只注册）。
        - 不挂载任何状态持久化（``clipboard_buffer`` 是 per-agent
          声明，归 ``use_clipboard``）；不读取其他插件状态。
        - 后置条件：三件工具已注册；此后每个 ``use_clipboard`` 的
          Agent 各自获得缓冲声明与阈值配置。

        :param runtime: 当前安装的 Runtime 实例。

        .. seealso:: :func:`use_clipboard`、
            :class:`flowing.plugins.clipboard.tools.ClipboardCutTool`
        """
        runtime.register_tool(ClipboardCutTool())
        runtime.register_tool(ClipboardCopyTool())
        runtime.register_tool(ClipboardPasteTool())


def use_clipboard(agent: Agent, *, max_lines: int = 500,
                  max_chars: int = 10_000) -> None:
    """Agent 级启用剪贴板（阶段二入口，``setup()`` 中调用）。

    .. rubric:: 功能介绍

    两件事：① 声明 ``clipboard_buffer`` 状态键（默认 ``None`` =
    空剪贴板；写透落盘、recover 可恢复）；② 把阈值写到实例属性
    ``clipboard_max_lines`` / ``clipboard_max_chars`` （插件绑定成员
    命名约定：注册名 underscore 前缀），三件工具经 ``caller`` 读取。

    .. rubric:: 使用示例

    .. code-block:: python

        async def setup(self):
            use_clipboard(self)          # 默认 500 行 / 10k 字符
            self.add_tool("clipboard-cut")
            self.add_tool("clipboard-copy")
            self.add_tool("clipboard-paste")

    .. rubric:: 行为要点

    - 状态键声明与阈值赋值同步完成；LLM 可见性仍需
      ``agent.add_tool("clipboard-cut")`` 等显式声明。
    - 参数校验：``max_lines`` / ``max_chars`` 必须为正整数，否则
      ``ValueError`` （不产生任何注册副作用——先校验后注册）。
    - 可重入：``state.register`` 幂等（键未持久化时写入默认值、已
      持久化时跳过并返回持久值），对同一实例重复调用不报错，阈值
      以最后一次调用为准；recover 在新实例重跑 setup 同样安全。
    - 不自动 ``add_tool`` （可见性是 Agent 开发者的显式决策）；不注册
      钩子点（剪贴板无事件面）。

    :param agent: 要启用的 Agent 实例。
    :param max_lines: 剪贴板行数上限（正整数，默认 500）。
    :param max_chars: 剪贴板字符数上限（正整数，默认 10_000）。
    :raises ValueError: ``max_lines`` / ``max_chars`` 不是正整数时
        （先校验后注册，不产生副作用）。

    .. seealso:: :class:`ClipboardPlugin`、
        :meth:`flowing.agent.Agent.register_state`
    """
    if not (isinstance(max_lines, int) and max_lines > 0):
        raise ValueError("max_lines 必须为正整数")
    if not (isinstance(max_chars, int) and max_chars > 0):
        raise ValueError("max_chars 必须为正整数")
    agent.state.register("clipboard_buffer", None)
    agent.clipboard_max_lines = max_lines
    agent.clipboard_max_chars = max_chars
