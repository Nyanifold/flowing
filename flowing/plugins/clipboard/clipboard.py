"""flowing.plugins.clipboard —— 剪贴板扩展（ClipboardPlugin / use_clipboard，N-01）。

.. rubric:: 模块定位

剪贴板是 Flowing 的**内置扩展**（随 ``flowing`` 包发布但不自动启用），
为 Agent 提供「文件区段的剪切 / 复制 / 粘贴」能力——面向 LLM 跨文件
搬运代码段的常见场景：LLM 先 cut/copy 一个区段（小内容进内存剪贴板、
大内容落文件），再 paste 到目标位置，避免在上下文里来回复述大段文本。

框架核心不内置剪贴板——它是策略性便利设施，由扩展承载；核心只提供它
复用的机制：插件状态持久化（``Agent.register_state`` / ``Agent.state``
——剪贴板缓冲就是一个状态键）、工具注册（``Runtime.register_tool``）、
caller 注入（工具经 ``caller`` 读写属主 Agent 的缓冲与阈值）。

遵循全局**双层启用**模型：

- 阶段一（Runtime 安装）：``runtime.use(ClipboardPlugin())`` →
  ``install(runtime)`` 注册 ``clipboard-cut`` / ``clipboard-copy`` /
  ``clipboard-paste`` 三件全局工具（注册 ≠ 可见：Agent 仍需
  ``add_tool`` 才对 LLM 可见，S-19）。
- 阶段二（Agent 启用）：``setup()`` 中 ``use_clipboard(self)`` 经
  ``register_state("clipboard_buffer", None)`` 声明缓冲状态键（挂载到
  该 Agent 自己的 ``state.jsonl``，写透落盘、recover 可恢复——
  N-01② 裁决），并设定阈值实例属性（``clipboard_max_lines`` /
  ``clipboard_max_chars``）。

不调用 ``use_clipboard()`` 的 Agent 零开销：无 ``clipboard_buffer``
状态键、无阈值属性；此时调用三件工具 → 「剪贴板未启用」error
``ToolResult``。

.. rubric:: 缓冲契约（state 键 ``clipboard_buffer``）

- 值为 ``None``（空）或 JSON 纯数据 dict：``{"content": str,
  "origin_path": str | None, "lines": int, "chars": int}``（state 值
  必须 JSON 可序列化的硬约束——缓冲不落对象）。
- **每 Agent 一份**（per-agent state.jsonl 单 writer，多 Agent 天然
  隔离）；跨 Agent 搬运经文件输出中转。
- **一次性语义（N-01⑦）**：``paste`` 成功且来源为剪贴板 → 缓冲清空
  为 ``None``（防止长期占用 state.jsonl 体积）。
- **阈值双限（N-01⑥）**：cut/copy 进剪贴板的内容行数 >
  ``clipboard_max_lines``（默认 500）**或**字符数 >
  ``clipboard_max_chars``（默认 10k）→ 拒绝入缓冲，强制文件输出；
  文件输出无阈值。

.. rubric:: 位置与路径口径

- 路径遵循 **``cwd`` 基准制**（N-01① 修订裁决，与 builtins 全部
  工具同口径）：``cwd=None``（默认）时一切文件参数仅收绝对路径；
  ``cwd`` 非 ``None`` 时允许相对路径（相对 ``cwd`` 解析）；
  ``cwd`` 自身必须是绝对路径。相对路径无基准或 ``cwd`` 非绝对 →
  error ``ToolResult``。``cwd`` 同时是「``.fya`` 定义期经智能体
  属性传参」的示范位（Parsable 覆写 ``cwd: "{{ cwd }}"``，完整
  示例见 :mod:`flowing.builtins.tools` 模块 docstring）。
- 行号 **1 起**（对齐编辑器）、offset **0 起**（对齐 Python 切片）；
  区间一律 start 包含、end 排除；越界各自报错（fail fast，N-01④）。

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
    三件全局工具。缓冲持久化不在 install 挂载——状态袋是 per-agent
    声明，由 ``use_clipboard(self)`` 在各 Agent 的 ``setup()`` 中完成
    （与 cron 的 ``cron_jobs`` 同构）。

    .. rubric:: 设计动机

    剪贴板把「跨文件搬运区段」从「LLM 复述文本」降为「两次工具调用」，
    省 token 且消除复述转写错误；阈值双限把大内容挡在 state 文件之外
    （state.jsonl 不是大文本仓库），一次性粘贴语义同理。
    """

    name: ClassVar[str] = "clipboard"
    """注册名（生态约定：类名去 Plugin 后缀转 kebab；显式写出为规范）。"""

    dependencies: ClassVar[list[str]] = []   # S-10：与基类 Plugin 的 ClassVar 对齐

    def install(self, runtime: Runtime) -> None:
        """注册三件剪贴板工具到 Runtime 注册表。

        .. rubric:: 行为规约

        - 期待行为：三次 ``register_tool`` 全部同步完成（R1：install
          只注册）。
        - 非行为：不挂载任何状态持久化（``clipboard_buffer`` 是
          per-agent 声明，归 ``use_clipboard``）；不读取其他插件
          状态（R2）。
        - 后置条件：三件工具已注册；此后每个 ``use_clipboard`` 的
          Agent 各自获得缓冲声明与阈值配置。

        .. rubric:: 调用关系（审计）

        - 调用：``Runtime.register_tool`` ×3（时机：本方法体内同步）
        - 被调：``flowing.runtime.Runtime.use``（阶段一安装，每个插件
          恰好一次）

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

    一件事的两个侧面：① 声明 ``clipboard_buffer`` 状态键（默认
    ``None`` = 空剪贴板；写透落盘、recover 可恢复）；② 把阈值写到
    实例属性 ``clipboard_max_lines`` / ``clipboard_max_chars``
    （插件绑定成员命名约定：注册名 underscore 前缀），三件工具经
    ``caller`` 读取。

    .. rubric:: 行为规约

    - 期待行为：状态键声明与阈值赋值同步完成；LLM 可见性仍需
      ``agent.add_tool("clipboard-cut")`` 等显式声明（S-19）。
    - 参数校验：``max_lines`` / ``max_chars`` 必须为正整数，否则
      ``ValueError``（不产生任何注册副作用——先校验后注册）。
    - 重复启用：``register_state`` 不支持重复声明（单袋化裁决），
      同一实例手写两次 ``use_clipboard`` 属编程错误，声明冲突即报错；
      recover 在新实例重跑 setup，天然不撞。
    - 非行为：不自动 ``add_tool``（可见性是 Agent 开发者的显式决策）；
      不注册钩子点（剪贴板无事件面）。

    .. rubric:: 使用示例

    .. code-block:: python

        async def setup(self):
            use_clipboard(self)          # 默认 500 行 / 10k 字符
            self.add_tool("clipboard-cut")
            self.add_tool("clipboard-copy")
            self.add_tool("clipboard-paste")

    .. rubric:: 测试案例

    - 前置：``setup`` 中 ``use_clipboard(self)`` → 期望：
      ``agent.state.clipboard_buffer is None``、
      ``agent.clipboard_max_lines == 500``；写缓冲后模拟崩溃重放 →
      期望：缓冲内容恢复（写透）。
    - 前置：已 ``use_clipboard`` → 操作：``use_clipboard(agent,
      max_lines=0)`` → 期望：``ValueError`` 且无注册副作用。

    .. rubric:: 调用关系（审计）

    - 调用：``Agent.register_state``（时机：本函数体内同步）
    - 被调：各 Agent 的 ``setup()``（create / recover 两管线均跑）

    .. seealso:: :class:`ClipboardPlugin`、
       :meth:`flowing.agent.Agent.register_state`
    """
    if not (isinstance(max_lines, int) and max_lines > 0):
        raise ValueError("max_lines 必须为正整数")
    if not (isinstance(max_chars, int) and max_chars > 0):
        raise ValueError("max_chars 必须为正整数")
    agent.register_state("clipboard_buffer", None)
    agent.clipboard_max_lines = max_lines
    agent.clipboard_max_chars = max_chars
