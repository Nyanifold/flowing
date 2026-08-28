"""内置工具全集（``builtin::`` 命名空间，P3-06 裁决）。

.. rubric:: 功能介绍

核心内置 `SubagentInvokeTool`（子智能体唤起的唯一工具入口，核心特性
S-01/S-02）与 `FinishTool`（子 Agent 可选交卷），加六个标准文件 /
shell 工具：`ReadTool` / `WriteTool` / `BashTool` / `EditTool` /
`GrepTool` / `GlobTool`。全部为普通 :class:`flowing.tool.Tool` 子类，
经 ``register_builtins()`` 进注册表——**核心特性 ≠ 静默附加，注册 ≠
可见**：任何工具对 LLM 可见都必须经 Agent 级显式声明（``.fya``
``tools:`` 或 ``add_tool``，S-19）。

.. rubric:: 设计动机

- **机制 vs 策略**：本模块只给「能干什么」；「该不该批准」（Bash 任意
  命令、Write/Edit 覆盖写）是策略，由 ``before_tool_call`` 钩子 /
  审批插件承担。docstring 把每个工具的危险面写明，声明方自负。
- **读侧与写侧分家**：Read/Grep/Glob 只读（ExploreAgent 的工具集）；
  Write/Edit/Bash 可写，docstring 危险面段落显著标注。
- **路径基准（``cwd``）口径（N-01① 修订裁决）**：一切文件/目录参数
  （``path``、clipboard 的 ``source``/``output``）**默认仅收绝对
  路径**；相对路径当且仅当该次调用的 ``cwd`` 非 ``None`` 时允许
  （相对 ``cwd`` 解析）；``cwd`` 自身**必须是绝对路径**（默认
  ``None`` = 不给基准）。相对路径无基准（``cwd=None``）或
  ``cwd`` 非绝对 → ``status="error"`` 的 ``ToolResult``（LLM 可见、
  可自纠正）。设计意图：基准必须由调用方显式给出，LLM 视角下没有
  隐含的「当前目录」。

  .. rubric:: fya 示范——工具参数经 Parsable 引用智能体属性

  ``cwd`` 同时是「``.fya`` 定义期经智能体属性传参」的示范位：Agent
  自己有 ``cwd`` 属性（如 setup 赋值 ``self.cwd = "/srv/proj"`` 或
  args 别名 ``working_dir as cwd``）时，``.fya`` 的 tools 条目把
  ``cwd`` 参数覆写为引用该属性的 Parsable，调用期自动求值注入::

      tools:
        - read:
            args:
              cwd: "{{ cwd }}"      # Parsable 模板：渲染上下文含实例属性
      ---
      $script:
      async def setup(self, working_dir: str):
          self.cwd = working_dir

  之后 LLM 调 ``read(path="src/main.py")`` 时 ``cwd`` 已由声明层
  固定为该 Agent 的 ``cwd``——LLM 只需给相对路径，基准管理是
  声明层的职责。
"""

from __future__ import annotations

import asyncio

from flowing.agent import Agent
from flowing.params import schema_to_model
from flowing.paths import NamingRules
from flowing.tool import Tool, ToolDefinition

__all__ = [
    "BashTool",
    "EditTool",
    "GlobTool",
    "GrepTool",
    "ReadTool",
    "WriteTool",
]


class ReadTool(Tool):
    """``read``——读 UTF-8 文本文件（只读）。

    .. rubric:: 行为规约

    - 参数：``path``（必填；``cwd=None`` 时**仅绝对路径**，
      ``cwd`` 非 ``None`` 时允许相对路径——相对 ``cwd`` 解析；
      N-01① 修订）；``cwd``（路径基准，默认 ``None``，给则必须
      绝对路径）；``offset`` / ``limit``（行号
      窗口，0 基；缺省从头读全）。
    - 输出：带行号前缀的文本（``<行号>\\t<内容>``）；超长行截断。
    - 边缘情况：路径是目录 / 二进制 / 不存在 / 相对路径且无
      ``cwd`` / ``cwd`` 非绝对 → ``status="error"`` 的
      ``ToolResult``（不抛异常——工具失败是 LLM 可见的正常产物）。
    - 非行为：不追随 symlink 之外的任何写操作；不做编码猜测（非
      UTF-8 即 error）。

    .. rubric:: 测试案例

    - 前置：三行文本文件 → 操作：``read(path, offset=1, limit=1)`` →
      期望：仅第二行，带行号。
    """

    def __init__(self) -> None:
        """构造定义与内部校验模型（S-33 契约，构造期一次）。"""
        self.definition = ToolDefinition(
            name="read",
            description="读取 UTF-8 文本文件，支持行号窗口（offset/limit）。",
            params_schema={
                "path": {"type": "string"},
                "cwd": {"type": ["string", "null"], "default": None},
                "offset": {"type": "integer", "default": 0},
                "limit": {"type": ["integer", "null"], "default": None},
            })
        self._has_caller = False
        self._execution = None
        self._args_model = schema_to_model(type(self).__name__ + "Args", self.definition.params_schema)

    async def execute(self, *, path: str, cwd: str | None = None,
                      offset: int = 0,
                      limit: int | None = None) -> str:
        """读文件并返回带行号文本（``<行号>\\t<内容>``，行窗由
        offset/limit 截）（D20：标准件统一返 ``str``）。

        .. rubric:: 调用关系（审计）

        - 调用：无（文件 IO 为内部细节）
        - 被调：``flowing.tool.Tool.__call__`` 调度层（LLM 每次调用 read）
        """
        # 读文件 -> 非 UTF-8 / 目录 / 不存在 -> 由 Tool.__call__ 包装为
        # error ToolResult；行窗 offset/limit 切片 -> 行号前缀拼接
        ...


class WriteTool(Tool):
    """``write``——覆盖写 UTF-8 文本文件（**可写工具**）。

    .. rubric:: 行为规约

    - 参数：``path`` / ``content``（均必填；``path`` 遵循
      ``cwd`` 基准口径——``cwd=None`` 时仅绝对路径，N-01① 修订）；
      ``cwd``（路径基准，默认 ``None``，给则必须绝对路径）。
    - 语义：整文件覆盖（父目录自动创建）；**非追加**。
    - **危险面**：任意路径覆盖写。审批 / 路径白名单属策略层
      （``before_tool_call`` 钩子），本工具不做。
    - 边缘情况：路径是目录 / 相对路径且无 ``cwd`` → error
      ``ToolResult``。

    .. rubric:: 测试案例

    - 前置：目标不存在且父目录不存在 → 操作：``write`` → 期望：父目录
      被创建、内容精确写入；再次写 → 期望：整体覆盖无残留。
    """

    def __init__(self) -> None:
        """构造定义与内部校验模型（S-33 契约）。"""
        self.definition = ToolDefinition(
            name="write",
            description="覆盖写入 UTF-8 文本文件（父目录自动创建）。",
            params_schema={
                "path": {"type": "string"},
                "cwd": {"type": ["string", "null"], "default": None},
                "content": {"type": "string"},
            })
        self._has_caller = False
        self._execution = None
        self._args_model = schema_to_model(type(self).__name__ + "Args", self.definition.params_schema)

    async def execute(self, *, path: str, content: str,
                      cwd: str | None = None) -> str:
        """覆盖写并返回路径收据文本（D20：内置工具统一返 ``str``）。

        .. rubric:: 调用关系（审计）

        - 调用：无（文件 IO 为内部细节）
        - 被调：``flowing.tool.Tool.__call__`` 调度层
        """
        # mkdir(parents=True) -> 整文件覆盖写
        ...


class BashTool(Tool):
    """``bash``——经 ``/bin/bash`` 执行 shell 命令（**高危可写工具**）。

    .. rubric:: 行为规约

    - 参数：``command``（必填）；``timeout``（秒，默认 120，超时杀
      进程组并返回 error）；``cwd``（工作目录，可选；给则**必须是
      绝对路径**，相对 → error ``ToolResult``，N-01① 修订）。
    - 输出：stdout / stderr / exit_code 拼成的裸文本（D20：标准件
      统一返 ``str``，LLM 看裸文本）；非零退出码**不是**异常——照常
      在 output 里返回（LLM 应看到）。
    - **危险面**：任意命令执行，继承 Agent 进程全部权限。审批 /
      命令过滤属策略层（``before_tool_call``），本工具不做。
    - 非行为：不做交互式命令（stdin 关闭）；不保持会话状态（每次
      调用是新进程，工作目录经 ``cwd`` 参数显式给出）。

    .. rubric:: 测试案例

    - 前置：无 → 操作：``bash("echo hi")`` → 期望：stdout 含 hi、
      exit_code=0；``bash("sleep 999", timeout=1)`` → 期望：超时
      error。
    """

    def __init__(self) -> None:
        """构造定义与内部校验模型（S-33 契约）。"""
        self.definition = ToolDefinition(
            name="bash",
            description="经 /bin/bash 执行 shell 命令，返回 stdout/stderr/exit_code。",
            params_schema={
                "command": {"type": "string"},
                "timeout": {"type": "integer", "default": 120},
                "cwd": {"type": ["string", "null"], "default": None},
            })
        self._has_caller = False
        self._execution = None
        self._args_model = schema_to_model(type(self).__name__ + "Args", self.definition.params_schema)

    async def execute(self, *, command: str, timeout: int = 120,
                      cwd: str | None = None) -> str:
        """执行命令并返回 stdout/stderr/exit_code 拼成的裸文本（D20）。

        .. rubric:: 调用关系（审计）

        - 调用：无（asyncio 子进程为内部细节）
        - 被调：``flowing.tool.Tool.__call__`` 调度层
        """
        # asyncio.create_subprocess_exec("bash", "-c", command) ->
        # communicate(timeout)（超时杀进程组）-> 三元组拼为文本
        ...


class EditTool(Tool):
    """``edit``——精确字符串替换（**可写工具**）。

    .. rubric:: 行为规约

    - 参数：``path`` / ``old_string`` / ``new_string``（必填；``path``
      遵循 ``cwd`` 基准口径——``cwd=None`` 时仅绝对路径，N-01①
      修订）；``cwd``（路径基准，默认 ``None``，给则必须绝对路径）；
      ``replace_all``（默认 ``False``）。
    - 唯一性约束：``replace_all=False`` 时 ``old_string`` 命中次数
      ≠ 1 → error（0 次 = 没找到，>1 次 = 歧义，均不改文件）。
    - 先读后写：同一进程内「读-替换-写」非原子——并发模型下编辑
      冲突由调用方避免（同一 Agent 串行，天然安全）。
    - **危险面**：任意路径改文件。审批属策略层（``before_tool_call``）。

    .. rubric:: 测试案例

    - 前置：文件含两处 ``foo`` → 操作：``edit(old="foo")`` → 期望：
      歧义 error、文件不变；``edit(old="foo", replace_all=True)`` →
      期望：两处全换。
    """

    def __init__(self) -> None:
        """构造定义与内部校验模型（S-33 契约）。"""
        self.definition = ToolDefinition(
            name="edit",
            description="精确字符串替换编辑文件（默认要求唯一命中）。",
            params_schema={
                "path": {"type": "string"},
                "cwd": {"type": ["string", "null"], "default": None},
                "old_string": {"type": "string"},
                "new_string": {"type": "string"},
                "replace_all": {"type": "boolean", "default": False},
            })
        self._has_caller = False
        self._execution = None
        self._args_model = schema_to_model(type(self).__name__ + "Args", self.definition.params_schema)

    async def execute(self, *, path: str, old_string: str, new_string: str,
                      replace_all: bool = False,
                      cwd: str | None = None) -> str:
        """替换并返回收据文本（路径与替换次数）（D20）。

        .. rubric:: 调用关系（审计）

        - 调用：无（文件 IO 为内部细节）
        - 被调：``flowing.tool.Tool.__call__`` 调度层
        """
        # 读 -> 计数（≠1 且非 replace_all -> error）-> 替换 -> 覆盖写
        ...


class GrepTool(Tool):
    """``grep``——内容搜索，体内委托 ``rg``（只读）。

    .. rubric:: 行为规约

    - 参数：``pattern``（ripgrep 正则，必填）；``path``（搜索根，
      必填；遵循 ``cwd`` 基准口径——``cwd=None`` 时仅绝对路径，
      N-01① 修订）；``cwd``（路径基准，默认 ``None``，给则必须
      绝对路径）；``glob``（文件名过滤，可选）。
    - 实现：``rg`` 子进程（带行号、默认跳过 .gitignore 与隐藏文件——
      rg 语义）；**``rg`` 未安装 → error ToolResult**（提示安装，
      不做 Python 兜底扫描——行为一致性优先于可用性）。
    - 输出截断：匹配行数超上限（默认 250）时截断并注明；``path``
      为绝对路径，匹配行中的路径随之以绝对形式呈现。

    .. rubric:: 测试案例

    - 前置：目录含两处匹配 → 操作：``grep(pattern)`` → 期望：两行
      带路径与行号；操作：``rg`` 不存在（模拟）→ 期望：error。
    """

    def __init__(self) -> None:
        """构造定义与内部校验模型（S-33 契约）。"""
        self.definition = ToolDefinition(
            name="grep",
            description="内容搜索（ripgrep 正则），带行号输出。",
            params_schema={
                "pattern": {"type": "string"},
                "path": {"type": "string"},
                "cwd": {"type": ["string", "null"], "default": None},
                "glob": {"type": ["string", "null"], "default": None},
            })
        self._has_caller = False
        self._execution = None
        self._args_model = schema_to_model(type(self).__name__ + "Args", self.definition.params_schema)

    async def execute(self, *, pattern: str, path: str,
                      cwd: str | None = None,
                      glob: str | None = None) -> str:
        """调 rg 并返回带行号匹配文本（D20）。

        .. rubric:: 调用关系（审计）

        - 调用：无（``rg`` 子进程为内部细节）
        - 被调：``flowing.tool.Tool.__call__`` 调度层
        """
        # rg --line-number [--glob glob] pattern path -> 截断 -> 文本；
        # FileNotFoundError -> error ToolResult（提示安装 rg）
        ...


class GlobTool(Tool):
    """``glob``——按模式找文件（只读）。

    .. rubric:: 行为规约

    - 参数：``pattern``（glob 模式，必填，``**`` 递归）；``path``
      （基准目录，必填；遵循 ``cwd`` 基准口径——``cwd=None`` 时仅
      绝对路径，N-01① 修订）；``cwd``（路径基准，默认 ``None``，
      给则必须绝对路径）。
    - 输出：匹配文件的**绝对路径**文本（每行一条；相对 ``path``
      经 ``cwd`` 解析后仍以绝对形式输出），**按 mtime 倒序**
      （最近修改在前）；只列文件不列目录；结果数超上限（默认 100）
      截断并注明。
    - 非行为：不做内容过滤（那是 ``grep``）；不跟随 ``.gitignore``
      （与 ``grep`` 的 rg 语义不同——glob 是字面文件系统枚举）。

    .. rubric:: 测试案例

    - 前置：目录含 ``a.py`` 与 ``sub/b.py`` → 操作：
      ``glob("**/*.py", path="/abs/dir")``
      → 期望：两者皆列出（绝对路径），mtime 新的在前。
    """

    def __init__(self) -> None:
        """构造定义与内部校验模型（S-33 契约）。"""
        self.definition = ToolDefinition(
            name="glob",
            description="按 glob 模式枚举文件（** 递归），按 mtime 倒序。",
            params_schema={
                "pattern": {"type": "string"},
                "path": {"type": "string"},
                "cwd": {"type": ["string", "null"], "default": None},
            })
        self._has_caller = False
        self._execution = None
        self._args_model = schema_to_model(type(self).__name__ + "Args", self.definition.params_schema)

    async def execute(self, *, pattern: str, path: str,
                      cwd: str | None = None) -> str:
        """枚举并返回绝对路径文本（每行一条，mtime 倒序）（D20）。

        .. rubric:: 调用关系（审计）

        - 调用：无（``pathlib`` 枚举为内部细节）
        - 被调：``flowing.tool.Tool.__call__`` 调度层
        """
        # pathlib.Path(path).glob(pattern) -> 仅文件 -> mtime 倒序 ->
        # 截断（默认 100）-> 逐行拼接为文本
        ...


class FinishTool(Tool):
    """子 Agent 结构化交卷工具——**普通工具，显式绑定**（非隐式内置）。

    .. rubric:: 功能介绍

    LLM 主动调用 ``finish`` 以结束子 Agent 当前逻辑 Turn（文档 14 口径：
    逻辑执行阶段收尾，无物理结构提交）。工具级 ``definition`` 只有骨架参数
    ``summary``；真正的结构化返回字段来自 Agent ``tools:`` 条目中的
    ``output:`` 声明。

    **不调用 ``finish`` 是子 Agent 的最常用法**：回合自然结束时，其 plain
    文本回复（``last_result``）即作为结果回传父 Agent——``finish`` 只是
    给想结构化提前交卷的子 Agent 一个可选出口，不是必备能力（S-01 裁决
    修正：废弃「隐式注册」表述）。

    .. rubric:: 设计动机

    这是「绑定层覆写」的旗舰案例：同一 `FinishTool` 在 ReviewerAgent 上
    LLM 看到 ``score``/``pass``/``issues``，在 PaymentAgent 上看到
    ``transaction_id``/``status``/``charged_amount``——全部经
    `ToolEntry.override_params` 完成，不修改全局注册表。

    .. rubric:: 使用示例

    .. code-block:: yaml

        # reviewer-agent.fya —— output 声明展开为 finish 的额外参数
        tools:
          - finish:
              output:
                score:
                  type: integer
                  minimum: 0
                  maximum: 100
                  description: 代码质量评分（0-100）
                pass:
                  type: boolean
                  description: 是否通过审查
                issues:
                  type: array
                  description: 发现的问题列表

    .. rubric:: 行为规约（动态 schema 机制）

    1. ``output`` 走 `ToolEntry.override_params`——框架核心不感知
       ``output`` 的含义，它只是普通覆写字段；
    2. 动态 schema 不修改 `ToolRegistry` 原始定义（
       ``clone_with_overrides`` 语义保证）；
    3. ``output`` 字段与 ``finish`` 自身参数合并（``summary`` + output 字段）；
       ``output`` 本身不是 LLM 可见参数；
    4. 无 ``output`` 声明 → 只有 ``summary``，LLM 以自由文本结束；
    5. **可见性走通用通道**：``finish`` 对 LLM 可见只经 ``.fya``
       ``tools:`` 声明或显式 ``add_tool("finish")``（S-01 裁决：
       工具绑定不经任何 use composable；原「``use_subagents()`` 隐式
       注册」表述废弃）；
    6. ``output:`` 与 ``args:`` 在 ToolEntry 内合并进同一
       ``override_params``，对 ``llm_definition()`` / ``resolve()`` 透明。
    7. ``output`` 覆写是**任意工具的通用字段**（与 ``args:`` 平级）：
       省略字段 = 移除，改变类型 = 覆写，无需完整重声明。
    8. 收尾机制（N-04 裁决）：``execute`` 把返回载荷置位
       ``caller.current_turn.finish_output``——**置位即请求本回合自然
       结束**（视同 ``finish=True``），无特例收尾通道；配对 TOOL 消息
       正常挂树（成对不变量不破），本回合 ``turn_end=True`` 落在该
       消息上；同响应的并行工具调用照常执行完，回合才收尾。

    .. rubric:: 测试案例

    - 前置：子 Agent 声明上述 ``output:`` → 操作：``llm_definition()`` →
      期望：产物 params 含 ``summary`` + ``score``/``pass``/``issues``
      四键，注册表中骨架定义仍只有 ``summary``。

    .. rubric:: 调用关系（审计）

    - 调用：``无``
    - 被调：``无`` （执行经 ``Tool.__call__`` 调度链）
    - 实例化方：``Runtime.__init__`` 经
      :func:`flowing.builtins.register_builtins` 注册（时机：Runtime
      构造期，随 Runtime 天生在场；可见性由 Agent 级显式声明控制）

    .. seealso::

        - :class:`flowing.tool.ToolEntry` —— 覆写数据载体。
        - :meth:`flowing.agent.Agent.add_tool` —— 显式绑定入口。
    """

    def __init__(self) -> None:
        """构造骨架定义并编译内部校验模型（S-33 契约）。

        .. rubric:: 调用关系（审计）

        - 调用：``flowing.tool.ToolDefinition`` 构造；
          ``flowing.params.schema_to_model()``（时机：构造期一次）
        - 被调：``flowing.builtins.register_builtins``（Runtime 构造期）
        """
        self.definition = ToolDefinition(
            name="finish",
            description="结束当前任务并返回结构化结果。调用后子 Agent 的"
                        "当前逻辑执行阶段结束。",
            params_schema={"summary": {"type": "string", "default": ""}})
        self._has_caller = True   # execute 声明 caller: Agent
        self._execution = None
        self._args_model = schema_to_model(type(self).__name__ + "Args", self.definition.params_schema)

    async def execute(self, *, summary: str = "", caller: Agent, **kwargs: Any) -> dict[str, Any]:
        """收集结构化返回字段并结束子 Agent 当前逻辑 Turn。

        .. rubric:: 行为规约

        - ``kwargs`` 接收 ``output:`` 声明展开的动态字段；返回
          ``{"summary": ..., **动态字段}``——**默认返回格式与无 output
          时同构**：无声明时值为自由文本字符串，声明后值换为结构化字段集，
          不改变返回结构本身。返回值经正常工具管线塑形挂树（配对 TOOL
          消息，无特例）。
        - 结束机制：同一载荷置位 ``caller.current_turn.finish_output``
          ——置位即请求本回合自然结束（视同 ``finish=True``，工具段照常
          执行完）。收尾动作（写 ``last_result`` = 本载荷、
          ``TurnContext`` 结束、resolve waiters、更新
          ``current_head_id``）全部由**子 Agent 自己的统一收尾段**承担，
          无物理结构提交。父 Agent 侧经 ``invoke_subagent`` 读
          ``last_result`` 构造 ``SubagentResult``，并推
          ``kind=SUBAGENT`` 消息入父队列。
        - 前置：``caller.current_turn`` 非 None（工具只在回合内执行）。

        .. rubric:: 调用关系（审计）

        - 调用：写 ``caller.current_turn.finish_output``（每次执行；
          父 Agent 侧 ``invoke_subagent`` 读 ``last_result`` 转
          ``kind=SUBAGENT`` 消息）
        - 被调：``flowing.tool.Tool.__call__`` 调度层（LLM 调用
          ``finish`` 时，每次子 Agent 收尾）

        .. seealso::

            - :class:`flowing.agent.TurnContext` —— 逻辑 Turn 执行期对象。
        """
        payload = {"summary": summary, **kwargs}
        # 置位即请求本回合自然结束：turn loop 工具段照常执行完，随后视同
        # finish=True 走子 Agent 自己的统一收尾段（写 last_result=本载荷
        # → TurnContext 结束 → resolve waiters）；配对 TOOL 消息正常挂树，
        # 本回合 turn_end=True 落在其上（_run_turn 置位转移检测）
        caller.current_turn.finish_output = payload
        return payload



class SubagentInvokeTool(Tool):
    """子智能体唤起工具——核心内置（S-01/S-02 裁决：子智能体是核心特性）。

    .. rubric:: 功能介绍

    LLM 唤起子 Agent 的唯一工具入口（spec-draft 08 §935：**不需要**为每个
    子 Agent 生成独立 `ToolDefinition`——工具只有这一个，各子 Agent 的
    参数描述经 ``<available_subagents>`` XML catalog 注入 system prompt）。
    骨架参数四个（全部可选）：``name``（新建时命名，之后可续接）、
    ``agent_type``（子 Agent 类型，与 ``resume`` 二选一）、``prompt``
    （任务提示）、``resume``（已存在实例名续接，与 ``agent_type`` 互斥）；
    各类型声明的其余 args 经 catalog 让 LLM 感知、经
    ``SubagentEntry.resolve()`` 聚合。

    .. rubric:: 设计动机

    「核心特性」的含义（S-01 裁决）：不需要任何 use composable「启用」——
    本工具随 Runtime 构造期经 :func:`flowing.builtins.register_builtins`
    注册（永远在注册表在场；P3-06 起本体物理归 ``flowing.builtins``）；
    Agent 有子
    Agent 条目（``.fya`` ``subagents:`` 或编程式绑定）时，catalog 动态
    prompt block 由核心管线自动处理。**但工具可见性不自动**——
    ``subagent-invoke`` 需用户显式声明（``tools:`` /
    ``add_tool``）才进 ``Context.tools``（S-19 最终裁决：
    一切工具以用户声明为准，框架不隐式附加；spec-draft 08 §960 的
    「存在 enabled 子 Agent 即自动进 tools」作废）。

    .. rubric:: 使用示例（LLM 视角）

    .. code-block:: text

        # 新建 + 命名
        subagent-invoke(name="my-reviewer", agent_type="coder", prompt="审查 auth 模块")
        # 之后续接同一实例
        subagent-invoke(resume="my-reviewer", prompt="继续审查 payment 模块")
        # 不等待：立即回收据，答卷后续以 SUBAGENT 消息到达
        subagent-invoke(agent_type="coder", prompt="后台跑全量测试", asynchronized=true)

    .. rubric:: 行为规约

    - **本工具是特殊设计，不遵循通用「``execute`` 返回
      ``asyncio.Task`` → pending 收据 + EVENT 入队」模式**：它的
      ``execute`` 是普通 ``async def``，通过显式参数 ``asynchronized``
      拆段实现异步。原因是：异步路径也必须先同步完成子 Agent 创建
      （错误要能同步反馈给 LLM），且真实结果必须是独立的 ``SUBAGENT``
      消息，不能走工具 EVENT 通道。
    - 期待行为：转发 ``caller.invoke_subagent(...)``（resolve + 唤起钩子 +
      生命周期策略都在那一层，见 :meth:`flowing.agent.Agent.invoke_subagent`）。
    - 同步路径（``asynchronized=False``）：调用公开同步 API
      ``invoke_subagent``（该 API 不 enqueue），把 ``SubagentResult``
      的全部字段平铺进本工具的返回 dict（``status`` / ``name_alias`` /
      ``subagent_id`` / ``result`` / ``subagent_status``），**不**再另发
      SUBAGENT 消息，避免同源结果二次入队。
    - 异步路径（``asynchronized=True``）：仍返回 ``started`` 短收据；子
      Agent 真实产出以 ``Message(kind=SUBAGENT)`` 在后台运行段完成时推入
      父队列，LLM 在后续回合感知。
    - ``asynchronized``（bool，缺省 ``False``）：``True`` 时**不等待**子
      Agent 完成，但**创建保证不变**——同步段（``Agent._prepare_subagent``：
      resolve + ``before_subagent_invoke`` + Execution 注册 + 创建/续接）
      仍然 await，失败（``Intercepted`` / 校验 / 创建抛错）照常产
      ``status="error"`` 结果（LLM 可见的自我修正反馈）；仅运行段
      （``Agent._run_subagent``）包进后台任务，随后立即返回
      ``{"invoked": ..., "status": "started"}`` 收据（此时收据字面成立：
      子 Agent 已存在并在跑）。运行段结局走 ``TurnResult`` 四态正常通道
      （``subagent_status`` + SUBAGENT 消息照常送达），
      ``after_subagent_invoke`` 钩子在后台任务完成时照常发生，cancel 经
      ``Execution`` 注册照常可命中；只剩框架 bug 走框架错误通道。
    - 校验：``agent_type`` 与 ``resume`` 互斥且至少其一；违反 →
      ``status="error"`` 结果（LLM 可见的自我修正反馈）。
    - 非行为：不直接创建/销毁 Agent；不感知 catalog 渲染。
    - 可见性：本工具由 Runtime 核心注册，但**不自动出现在任何 Agent
      的 ``Context.tools``**——需用户显式声明（``.fya`` 的 ``tools:``
      或 ``add_tool("subagent-invoke")``；S-19 最终裁决：一切
      工具以用户声明为准，框架不隐式附加）。

    .. rubric:: 调用关系（审计）

    - 调用：``flowing.agent.Agent.invoke_subagent()``（时机：每次执行）
    - 被调：``flowing.tool.Tool.__call__`` 调度层（LLM 每次调用
      ``subagent-invoke``）
    - 实例化方：``Runtime.__init__`` 经
      :func:`flowing.builtins.register_builtins` 注册（时机：Runtime
      构造期）

    .. seealso::

        - :meth:`flowing.agent.Agent.invoke_subagent` —— 真正的唤起管线。
        - :class:`flowing.subagents.SubagentEntry` —— catalog 与参数聚合。
        - :class:`flowing.builtins.FinishTool` —— 子 Agent 侧的可选交卷工具。
    """

    def __init__(self) -> None:
        """构造骨架定义并编译内部校验模型（S-33 契约）。

        .. rubric:: 调用关系（审计）

        - 调用：``flowing.tool.ToolDefinition`` 构造；
          ``flowing.params.schema_to_model()``（时机：构造期一次）
        - 被调：``flowing.builtins.register_builtins``（Runtime 构造期）
        """
        self.definition = ToolDefinition(
            name="subagent-invoke",
            description="唤起一个子 Agent：新建（agent_type + 可选 name）或"
                        "续接（resume 实例名）。返回确认收据；子 Agent 产出"
                        "以消息形式后续到达。asynchronized=True 时不等待完成，"
                        "立即返回收据。",
            params_schema={
                "name": {"type": "string", "default": ""},
                "agent_type": {"type": "string", "default": ""},
                "prompt": {"type": "string", "default": ""},
                "resume": {"type": "string", "default": ""},
                "asynchronized": {"type": "boolean", "default": False},
            })
        self._has_caller = True   # execute 声明 caller: Agent
        self._execution = None
        self._args_model = schema_to_model(type(self).__name__ + "Args", self.definition.params_schema)

    async def execute(
        self,
        *,
        caller: Agent,
        name: str = "",
        agent_type: str = "",
        prompt: str = "",
        resume: str = "",
        asynchronized: bool = False,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """转发 ``caller.invoke_subagent()`` 并返回确认收据。

        .. rubric:: 行为规约

        - ``agent_type`` 与 ``resume`` 互斥且至少其一（空串视为未给）；
          违反时抛 ``ValueError``——经 ``Tool.__call__`` 包装为
          ``status="error"``（LLM 可见）。
        - ``kwargs`` 为该子 Agent 类型声明的其余 args（catalog 中 LLM
          可见），原样透传给 ``invoke_subagent`` 经 ``SubagentEntry.
          resolve()`` 聚合。
        - ``asynchronized=True``：不等待运行段，但创建保证同步——先
          await ``caller._prepare_subagent(...)``（失败即上抛，经
          ``Tool.__call__`` 包装为 ``status="error"``，LLM 可见），再
          ``asyncio.ensure_future(caller._run_subagent(..., enqueue_result=True))``
          后台承载运行段，立即返回 ``status="started"`` 收据（此时收据
          字面成立：子 Agent 已创建）。
        - 缺省 ``False``：同步等待子 Agent 完成，以 ``enqueue_result=False``
          调用 ``invoke_subagent``；随后把返回的 ``SubagentResult`` 全字段
          平铺进本工具返回 dict，不再另发 SUBAGENT 消息。

        .. rubric:: 调用关系（审计）

        - 调用：``flowing.agent.Agent.invoke_subagent()``（每次执行；
          ``asynchronized=True`` 时拆段为 :meth:`Agent._prepare_subagent`
          （同步 await）+ :meth:`Agent._run_subagent`（经
          ``asyncio.ensure_future`` 后台））
        - 被调：``flowing.tool.Tool.__call__`` 调度层
        """
        if bool(agent_type) == bool(resume):  # 互斥且至少其一
            raise ValueError("agent_type 与 resume 必须二选一")
        if asynchronized:
            # 不等待运行段，但创建保证同步：准备段失败（Intercepted / 校验 /
            # 创建抛错）在此同步上抛 -> Tool.__call__ 包装为 status="error"
            # （LLM 可见）；运行段结局走 TurnResult 四态 + SUBAGENT 消息
            # 正常通道，只剩框架 bug 走框架错误通道
            child, invocation, execution = await caller._prepare_subagent(
                agent_type, prompt=prompt or None, name=name or None,
                resume=resume or None, kwargs=kwargs)
            asyncio.ensure_future(caller._run_subagent(
                child, invocation, execution, enqueue_result=True))
            return {"invoked": name or resume, "status": "started"}
        result = await caller.invoke_subagent(
            agent_type, prompt=prompt or None, name=name or None,
            resume=resume or None, **kwargs)
        # 同步路径：把 SubagentResult 全字段平铺进工具返回值；
        # 运行段已跳过 SUBAGENT 入队，避免同源结果二次入队。
        return {
            "status": "completed",
            "name_alias": result.name_alias,
            "subagent_id": result.subagent_id,
            "result": result.result,
            "subagent_status": result.subagent_status,
        }


TOOL_NAMING = NamingRules(
    suffixes=(".tool.fya", ".fya", ".py"),
    generic_names=frozenset({"TOOL.fya", "tool.fya", "TOOL.py", "tool.py"}),
)
"""Tool 资源的路径形态身份名推断规则表（:class:`flowing.paths.NamingRules`）。

紧邻候选链声明（:meth:`ToolRegistry.get`）：命中通用名候选
（``TOOL.fya`` 等）→ 身份名取目录名；否则文件名去首个匹配后缀、
snake→kebab。供装配层调 :func:`flowing.parser.parse_fya` 时传入
``naming=TOOL_NAMING``，以及 name 断言的推断侧。
"""

