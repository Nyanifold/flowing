"""``flowing.snapshot`` —— 观测面：Runtime / Agent 状态的一致性只读快照与 Info 视图。

.. rubric:: 功能介绍

本模块定义 Flowing 框架的只读观测面：两个一致性快照结构
（:class:`RuntimeSnapshot` / :class:`AgentSnapshot`）与一组 Info 只读视图
（:class:`NodeInfo` / :class:`AgentInfo` / :class:`ExecutionInfo` /
:class:`EntryInfo` / :class:`ModelInfo` / :class:`TurnContextInfo` /
:class:`MessageTreeInfo` / :class:`MessageQueueInfo`）。它们回答一个问题：
不侵入框架核心执行流的前提下，外部（UI、监控、测试、运维）如何知道
Runtime / Agent 此刻是什么状态。

观测面由两条互补的机制通道组成，本模块承载「拉取」这一条（「推送」由
钩子系统承载）：

.. list-table::
   :header-rows: 1
   :widths: 22 26 12 40

   * - 通道
     - 触发方式
     - 方向
     - 机制归属
   * - ``snapshot()``
     - 观察者主动调用
     - 拉取
     - 本模块（返回结构）+ ``flowing.runtime.Runtime`` / ``flowing.agent.Agent`` （入口方法）
   * - 钩子订阅
     - 框架在关键时点 dispatch
     - 推送
     - ``flowing.hooks.HookRegistry`` （既有钩子系统）

两条通道不互斥：钩子 handler 内部可再调用 ``snapshot()`` 拿事件发生后的
完整状态；快照也可独立于钩子被调用。

快照是观测量，不是持久化格式：持久化侧由 ``tree.jsonl`` （消息树操作
日志）与 ``state.jsonl`` （``flowing.persistence.StateView`` 写透存储）
承载，恢复 = 重放日志现场重建对象。不存在「拍全量状态照存档、下次整张
加载」的快照文件；观测字段与持久化格式无转换关系、不保证字段兼容
（观测字段由「当下想看什么」驱动，持久化格式由兼容性驱动）。

快照不包含插件状态：框架不为插件提供快照命名空间挂载机制。插件要暴露
自身状态，走自己的只读查询 API——观察者经 ``runtime.get_plugin(...)``
拿到插件对象后调用其公开方法。

.. rubric:: 使用示例

.. code-block:: python

    from flowing import launch

    runtime = await launch("@/")
    root = await runtime.get_agent("root")

    # 拉取通道：Runtime 全集快照与 Agent 快照
    rsnap = runtime.snapshot()
    print(rsnap.plugins)                  # 已安装插件名列表
    print(rsnap.agents["root"].loaded)    # 该 Agent 实例是否已创建

    asnap = root.snapshot()
    print(asnap.paused, asnap.messages.count, asnap.current_head_id)
    for exc_id, exc in asnap.executions.items():
        print(exc.kind, exc.tags, exc.started_at)

    # 推送通道：订阅钩子，handler 内再拉快照
    def use_monitor(agent):
        agent.hooks.after_turn(_on_turn_end, by="monitor", tags=["observe"])

    def _on_turn_end(agent, turn):
        print(agent.snapshot().message_queue.size)   # 只观察
        return turn                                  # 原样放行

.. rubric:: 行为要点

- 两个入口都是同步方法：``runtime.snapshot()`` 与 ``agent.snapshot()``
  不是协程，调用时不需要 ``await``。快照收集在同一事件循环 task 内同步
  完成，不等待任何长操作。
- 单时刻一致性：一次调用内部各字段取自同一时刻的框架状态，不出现
  「``messages.count`` 取自 t1、``executions`` 取自 t2」的撕裂。一致性
  承诺以一次调用为单位：需要多个切面在同一时刻一致，必须在同一次调用
  中传入全部所需 key（``keys={"tool_entries", "executions"}``）；拆成
  两次调用各取一个切面，两次之间框架可能已变化，跨切面比对失效。
- 按需收集（``keys`` 参数）：``keys=None`` （默认）收集全部切面；
  指定 ``keys`` 时只收集指定切面，未收集的字段为 ``None``——高频轮询
  单切面时不付无关切面的收集成本（典型即 ``context_usage`` 的锚点扫描
  与工具 schema 估算）。key 集合 = 快照字段名。
- 不承诺跨快照原子：两次调用之间框架可能发生任意变化；观察者不得
  缓存旧快照做控制决策——快照是观察通道，不是控制依据。
- 只读隔离：快照返回副本 / 不可变视图，与内部存储隔离；修改快照
  不影响框架状态。快照永不暴露内部可变对象（dict 引用、``cancel`` /
  ``pause`` 等控制信号 Event、队列内部 deque、``_provided`` dict）。
  修改框架状态必须走正式 API（``provide()`` / ``Agent.register_state()``
  等）。
- 不抛异常：快照收集对内部状态做防御性读取；正常路径（含 Agent
  已销毁、Runtime shutdown 进行中）不抛异常，返回当前可得的一致切片。
- 可序列化：快照全部字段（含 Info 视图嵌套）只由 JSON 可序列化
  标量 / ``datetime`` / list / dict / Info 视图组成——不引入任何类实例、
  可调用对象或对内部结构的引用。``serve`` 的 ``GET /snapshot`` 直接
  JSON 序列化即本不变量的消费点；新增字段必须满足此约束。
- 观测不进核心执行流：快照调用不改变任何框架状态，观测者存在与否
  不影响框架行为；观测 handler 只是钩子消费者，快照只是只读投影。

.. seealso::

    ``flowing.runtime.Runtime.snapshot``
    ``flowing.agent.Agent.snapshot``
    ``flowing.agent.TurnContext`` （逻辑 turn 执行期对象，``TurnContextInfo`` 的被投影对象）
    ``flowing.agent.Execution`` （执行条目，``ExecutionInfo`` 的被投影对象）
    ``flowing.hooks.HookRegistry`` （推送通道的机制基座）
    ``flowing.agent.Agent.state`` （持久化状态袋，与观测正交）
    ``flowing.runtime.Runtime.get_plugin`` （插件状态的只读查询入口）
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from flowing.context import ContextUsageEstimate   # 注解级引用（context 不 import snapshot，无环）


@dataclass
class NodeInfo:
    """Runtime 对象图中单个节点的只读元信息视图。

    .. rubric:: 功能介绍

    ``RuntimeSnapshot.nodes`` 的值类型：一个节点在共享 ID 空间
    （``runtime-*`` / ``workflow-*`` / ``agent-*``）中的类型与亲节点指针。快照
    只投影节点身份的两个标量，不暴露节点对象本身（节点实例是可变内部对象，
    经快照暴露会破坏只读隔离）。

    .. rubric:: 行为要点

    - 两个字段均为创建后不变的标量拷贝；修改本视图不影响框架。
    - ``parent_id`` 为 ``None`` 当且仅当该条目是 Runtime 自身（链终点无
      亲节点）；Agent / Workflow 的根条目亲节点指针指向其 Runtime 的 ``node_id``
      ——「根」由「亲节点是 Runtime」表达，不由 ``None`` 表达。
    - 不暴露节点的 hooks / provided / children 等任何可变结构。
    - 节点在快照生成后被 ``destroy()``，本视图仍保留生成时刻的值（快照不
      追踪后续变化；需要新鲜状态请重新调用 ``snapshot()``）。

    .. seealso::

        ``flowing.runtime.ProvideNode``
        ``flowing.snapshot.RuntimeSnapshot.nodes``
    """

    type: str
    """节点类型名（``"runtime"`` / ``"agent"`` / ``"workflow"``），取自节点
    id 的前缀；快照生成时刻固定。"""
    parent_id: str | None
    """亲节点 id；Runtime 自身（链终点）为 ``None``。仅作标识用，不可经它
    反查对象（反查走 ``Runtime.get_agent`` 等正式 API）。"""


@dataclass
class AgentInfo:
    """agent 池注册表单条元数据的只读视图。

    .. rubric:: 功能介绍

    ``RuntimeSnapshot.agents`` 的值类型：agent 池注册表（``agent_id`` →
    元数据）中一个条目的投影。池持久化的是注册表而非实例——实例懒创建，
    因此 ``loaded`` 区分「有 key 有 value」与「有 key 无 value」两种状态。

    .. rubric:: 行为要点

    - ``loaded=False`` 不表示异常：它表示实例尚未创建（懒加载）或已
      ``destroy()`` （记录保留，可现场恢复）；``Runtime.get_agent(agent_id)``
      触发「有 key 无 value → 现场恢复」。
    - ``agent_type`` 为字符串类型名（恢复重建实例的依据），非类对象。
    - ``created_at`` 取自注册表元数据，恢复后保持原值（身份连续、可重现，
      ``node_id == agent_id``），不是「本次加载时间」。
    - 显式删除 session（删持久化目录）后条目从注册表移除，下一张快照中
      该 key 消失。

    .. seealso::

        ``flowing.runtime.Runtime.get_agent``
        ``flowing.runtime.Runtime.create_agent`` / ``flowing.runtime.Runtime.recover_agent``
        ``flowing.agent.Agent.destroy`` （销毁实例但保留 session）
    """

    agent_type: str
    """字符串类型名（恢复重建实例的依据）；非类对象、非模块路径。"""
    parent_agent_id: str
    """亲代 Agent 的 id；根 Agent 的值为其 Runtime 的 ``node_id``。类型为
    ``str``，不会出现 ``None``。亲子在持久化目录上平级，亲子关系仅逻辑记录。"""
    created_at: datetime
    """创建时间戳（注册表元数据原值，恢复后不改变）。"""
    loaded: bool
    """实例是否已在内存中：``True`` = 已创建的活实例；``False`` = 有 key
    无 value（懒加载未触发或已 destroy），可经 ``Runtime.get_agent`` 现场
    恢复。"""


@dataclass
class ExecutionInfo:
    """单个活跃执行条目的只读视图。

    .. rubric:: 功能介绍

    ``AgentSnapshot.executions`` 的值类型：Agent 执行注册表中一个条目的
    投影——观测者能看到「有哪些执行在飞、打了什么标签、跑了多久」。

    .. rubric:: 行为要点

    - ``kind`` 为开放字符串：内置 ``"tool"`` / ``"agent"`` / ``"request"`` /
      ``"side_query"``，扩展可引入新值（如 ``"workflow"`` / ``"cron"``）；
      快照不做枚举校验，原样投影。
    - ``tags`` 为拷贝：快照后执行条目的 tag 变化不影响本视图。
    - 执行条目只在其存活期间出现于快照（清理在 ``finally`` 中保证，快照
      里不会出现已结束的「幽灵条目」）。
    - 快照生成后执行完成，本视图仍保留生成时刻的值——不得据此做「它还在
      跑」的控制决策（快照是观察通道，不是控制依据）。
    - 不提供经过时间与进度字段：「跑了多久」由观察者用 ``started_at`` 与
      当前时间自行计算。
    - 不暴露 ``cancel`` / ``pause`` 两个控制信号 Event——取消 / 暂停是正式
      控制 API（``Agent.cancel()`` / ``Agent.pause()`` 族），不是观测面能力。

    .. seealso::

        ``flowing.agent.Execution`` （被投影对象，含控制信号，不进快照）
        ``flowing.agent.Agent.cancel`` / ``flowing.agent.Agent.cancel_by_tag``
        ``flowing.snapshot.AgentSnapshot.executions``
    """

    kind: str
    """执行种类（开放字符串，内置 ``tool`` / ``agent`` / ``request`` /
    ``side_query``）；只描述类型，不参与控制分发。"""
    tags: list[str]
    """自由标签的拷贝，用于分组观察（与 ``cancel_by_tag`` 的分组语义对应）；
    快照后源条目 tag 变化不影响本视图。"""
    started_at: datetime
    """启动时间戳；供监控 / 日志 / 「跑了多久」类计算使用。"""


@dataclass
class EntryInfo:
    """能力绑定条目（ToolEntry / SubagentEntry）的只读视图。

    .. rubric:: 功能介绍

    ``AgentSnapshot.tool_entries`` 与 ``AgentSnapshot.subagent_entries``
    的元素类型：三层能力描述中「Agent 级绑定」层的投影——同一个可执行对象在
    不同 Agent 上可经 entry 覆写别名与启用状态，快照呈现的是本 Agent 上的
    绑定结果，而非全局注册表。

    .. rubric:: 行为要点

    - ``alias`` 是 Agent 内查找键（``tool_call()`` 仅按别名查找）；同一工具
      在不同 Agent 快照中可有不同别名。
    - ``visible=False`` 表示绑定存在但当前对 LLM 不可见（编程式调用不受
      影响——可见性≠可执行性）；条目本身仍在（可被重新置可见）。
    - ``agent_type`` 仅子 Agent 条目有值；工具条目恒为 ``None``。
    - 不投影 entry 的 schema 覆写内容（specified / inject 参数等）——观测
      面只给「绑没绑、叫什么、开没开」；LLM 可见声明请经
      ``ToolEntry.llm_definition()`` 获取。
    - 条目在快照生成后被移除 / 停用，本视图保留生成时刻的值。

    .. seealso::

        ``flowing.tool.ToolEntry``
        ``flowing.agent.Agent.tool_call``
        ``flowing.snapshot.AgentSnapshot.tool_entries`` / ``flowing.snapshot.AgentSnapshot.subagent_entries``
    """

    alias: str
    """Agent 内别名（查找键）；可能与全局注册名不同（per-Agent 覆写）。"""
    visible: bool
    """可见性状态：``False`` 时对 LLM 不可见（编程式调用不受影响——
    可见性≠可执行性），但绑定仍保留。对应 Tool/Subagent/Skill 条目的
    ``visible`` 字段（别名键 dict 承载，不经 ``ManagedList`` 管理——两
    家族分界见 ``flowing.lists`` 模块 docstring）。"""
    agent_type: str | None
    """子 Agent 条目的目标类型名；工具条目恒为 ``None``。"""


@dataclass
class ModelInfo:
    """``self.model`` （ModelConfig）解析结果的只读视图。

    .. rubric:: 功能介绍

    ``AgentSnapshot.model`` 的值类型：Agent 当前模型结构体的投影。Agent
    持有但不解释 ModelConfig 字段（只机械传给
    ``provider.generate(context, model)``），快照同样只做投影不做解释。

    .. rubric:: 行为要点

    - 取值时机：快照生成时对 ``self.model`` 现场求值投影（Parsable 字段按
      ``ModelConfig.resolve()`` 语义字段级求值）；本地不缓存——每次快照
      反映当下值，动态改 ``self.model`` / ``model_tag`` 后下一张快照即见
      新值。
    - 任何字段求值失败（如 Parsable 引用缺失）：该字段以 ``None`` 投影，
      快照整体不因此抛异常（观测通道不打穿核心）。
    - ``model_tag`` 为当前标签；经「直接赋 ``ModelConfig``」路径换模型时
      （可指向配置中从未定义的模型）为 ``None``。
    - 不含 Provider 凭证、不含 ``api_key`` 等任何敏感配置（凭证不进消息、
      不落盘、不进快照）。
    - Agent 尚未完成模型解析（创建管线中）时，``AgentSnapshot.model``
      整体为 ``None``，而非半初始化的 ModelInfo。

    .. seealso::

        ``flowing.model.ModelConfig`` / ``flowing.model.ModelConfig.resolve``
        ``flowing.providers.Provider`` （1:1 绑定，懒创建——实例化与否不进快照）
        ``flowing.snapshot.AgentSnapshot.model``
    """

    model: str | None
    """模型 ID（传给 API 的值）；求值失败时为 ``None``。"""
    provider: str | None
    """绑定的 provider 条目名（1:1 单值，无候选列表、无 fallback 链）。"""
    model_tag: str | None
    """当前模型标签；经直接赋 ``ModelConfig`` 路径时为 ``None``。"""
    context_window: int | None
    """上下文窗口大小（tokens）；模型条目未声明或求值失败时为 ``None``。"""
    max_output_tokens: int | None
    """最大输出 tokens；未声明或求值失败时为 ``None``。"""
    thinking_budget: int | None
    """思考强度；未声明或求值失败时为 ``None``。"""


@dataclass
class TurnContextInfo:
    """当前逻辑 turn（TurnContext）的只读视图。

    .. rubric:: 功能介绍

    ``AgentSnapshot.current_turn`` 的值类型：逻辑 turn 执行期临时对象
    ``TurnContext`` 的投影。Turn 只是逻辑执行阶段（消费一条消息 →
    ``finish=True``），不是树节点；TurnContext 不落盘、不进树、崩溃后不
    恢复，因此其快照视图同样只存在于执行期间。

    .. rubric:: 行为要点

    - 仅当逻辑 turn 执行期间（从 turn 开始到收尾清理为止）
      ``AgentSnapshot.current_turn`` 非 ``None``；收尾观察钩子
      （``after_turn``）与交付期快照即无此项——「快照无此字段」不等于
      「回合钩子已跑完」。
    - ``message_count`` 含 turn 首条（触发消息）在内；逻辑标识可用 turn
      首条消息 id（树中真实节点），快照不提供独立的逻辑 turn 标识符。
    - ``aborted`` 反映 TurnContext 的取消标记（协作式 cancel 已请求）；它
      是状态投影，不是取消入口——取消请走 ``cancel()`` / ``stop()`` 族。
    - 不暴露 ``message_ids`` 列表本体（可变内部对象）；不暴露 turn 内循环
      的中间 LLM 输出（流式观察走 ``on_provider_delta`` 钩子订阅，不走
      快照轮询）。
    - 崩溃恢复后不出现半截 TurnContextInfo：进行中的逻辑 turn 不持久化、
      不恢复，恢复后的 Agent 快照 ``current_turn`` 恒为 ``None`` 直到新
      turn 开始。

    .. seealso::

        ``flowing.agent.TurnContext`` （被投影对象）
        ``flowing.agent.TurnResult`` （逻辑 turn 的收尾产物，``turn`` 字段即 TurnContext）
        ``flowing.agent.Agent.cancel`` / ``flowing.agent.Agent.stop``
    """

    started_at: datetime
    """逻辑 turn 开始时间戳；供「本 turn 已跑多久」类计算。"""
    finished_at: datetime | None
    """turn 收尾完成时间戳投影；快照仅存在于执行期间，故此字段恒为
    ``None``——完整取值见 ``flowing.agent.TurnResult.turn.finished_at``。"""
    message_count: int
    """本 turn 已产生消息的数量（含首条触发消息）；只给计数，不给 id 列表
    本体。"""
    aborted: bool
    """取消标记投影：``True`` 表示协作式 cancel 已请求；只读，不是取消入口。"""


@dataclass
class MessageTreeInfo:
    """消息级树的规模与游标只读视图。

    .. rubric:: 功能介绍

    ``AgentSnapshot.messages`` 的值类型：消息级树的两个标量投影——消息总数
    与树游标。树节点 = 消息（``Message.id`` + ``Message.parent_id`` 链），
    游标 ``current_head_id`` 指向消息 id。

    .. rubric:: 行为要点

    - ``count`` 为树中消息节点总数（含各分支；不含已删除的消息——删除
      以 tombstone（删除标记）记录，被标记的消息不计入；副线消息不落盘
      不进树，不计入）。
    - ``head_id`` 即 ``current_head_id`` 的值（消息 id）；空树（尚无已挂树
      消息）时为 ``None``。
    - fork 只切游标不动树：fork 后 ``head_id`` 变化、``count`` 不变。
    - 崩溃恢复后：``head_id`` 指向最后一条已挂树消息；半截逻辑 turn 的消息
      保留在树中（计入 ``count``），组装上下文时才会截断到最后一个
      ``turn_end=True`` 边界——快照如实投影树内容，不做上下文截断。
    - 不提供分支枚举、不提供消息内容、不提供按 id 反查（反查走正式读取
      路径 ``MessageChain`` / 持久化层）。

    .. seealso::

        ``flowing.message.Message`` / ``flowing.message.MessageChain``
        ``flowing.agent.Agent.current_head_id``
        ``flowing.snapshot.AgentSnapshot.current_head_id``
    """

    count: int
    """树中消息节点总数（含各分支；不含已删除（tombstone 标记）的消息与
    副线消息）。"""
    head_id: str | None
    """树游标（``current_head_id`` 的值，消息 id）；空树为 ``None``。"""


@dataclass
class MessageQueueInfo:
    """消息队列规模的只读视图。

    .. rubric:: 功能介绍

    ``AgentSnapshot.message_queue`` 的值类型：每 Agent 独立消息队列的两个
    计数投影——待消费消息数与等待 ``message()`` 结果的 pending 数。队列
    内部是 deque 与等待中的 Future，均为可变内部对象；观测只需要两个计数
    即可回答「有没有积压」「有没有人等结果」。

    .. rubric:: 行为要点

    - ``size``：已入队未出队的消息数；cancel 后待处理消息不丢弃，因此
      cancel 后 ``size`` 不归零（处置交应用层）。
    - ``pending``：等待 ``message()`` 结果、尚未 resolve 的等待者数；逻辑
      turn 收尾 resolve waiters 后递减。
    - ``destroy()`` 后不应再调用 ``snapshot()``——对池条目状态的观察请用
      ``RuntimeSnapshot.agents[id].loaded``。
    - 不提供队列内容枚举（内容走钩子订阅 ``on_enqueue`` /
      ``on_dequeue`` 或消息级树读取）。

    .. seealso::

        ``flowing.message.MessageQueue``
        ``flowing.agent.Agent.message`` / ``flowing.agent.Agent.enqueue_message`` / ``flowing.agent.Agent.cancel_queued``
    """

    size: int
    """待消费消息数（已入队未出队）；cancel 不丢弃待处理消息，故不归零。"""
    pending: int
    """等待 ``message()`` 结果的 pending 数（未 resolve 的 waiter 计数）。"""


@dataclass
class RuntimeSnapshot:
    """Runtime 的一致性只读快照：``runtime.snapshot()`` 的返回类型。

    .. rubric:: 功能介绍

    Runtime 全局存储的单时刻一致切片：对象图（``nodes``）、插件清单
    （``plugins``）、agent 池注册表（``agents``）、provider 候选清单名
    （``providers``）、配置覆盖层只读副本（``config_overrides``）、Resource
    注册名（``resources``）。快照对应 Runtime 各全局存储的读投影，一处存储
    一个字段，不多不少；核心不为插件状态留挂载点——插件状态经插件自己的
    只读 API 观测（``runtime.get_plugin(...)``）。

    .. rubric:: 使用示例

    .. code-block:: python

        rsnap = runtime.snapshot()
        for node_id, node in rsnap.nodes.items():
            print(node_id, node.type, node.parent_id)
        for agent_id, info in rsnap.agents.items():
            if not info.loaded:
                agent = await runtime.get_agent(agent_id)  # 现场恢复

    .. rubric:: 行为要点

    - 一致性：一次调用内全部字段同一时刻取值（见模块 docstring「单时刻
      一致性」）；返回结构与内部存储隔离（副本 / 不可变视图），修改快照
      不影响框架状态。
    - 无副作用、不抛异常：正常路径（含 shutdown 进行中）返回当前可得
      切片。
    - 不暴露项：节点 / Agent / Provider 实例本体；``_provided`` dict；配置
      命名空间原文中的敏感值（API key 等凭证不进快照）；任何
      ``asyncio.Event`` / Task / Future。
    - provider 清单只给候选名——实例化与否（懒创建）不进快照；未加载任何
      模型条目时 ``providers`` 为空列表而非报错。
    - 前置条件：无（``launch`` 返回后即可调用）；后置条件：无（纯观察）。

    .. seealso::

        ``flowing.runtime.Runtime.snapshot``
        ``flowing.snapshot.AgentSnapshot``
        ``flowing.runtime.Runtime.get_agent`` / ``flowing.runtime.Runtime.shutdown``
    """

    nodes: dict[str, NodeInfo]
    """``node_id`` → ``NodeInfo``；共享 ID 空间（``runtime-*`` /
    ``workflow-*`` / ``agent-*``）中全部节点的只读投影。注：Runtime 无
    生命周期状态机，无 ``status`` 字段——「Runtime 是否已关闭」见
    ``Runtime.shutdown()`` 语义（``shutdown()`` 返回后即可知）。"""
    plugins: list[str]
    """已安装插件名（``plugin.name``）列表；未 ``runtime.install(...)`` 的扩展
    不出现（「没存在过」，不是「被 skip」）。"""
    agents: dict[str, AgentInfo]
    """``agent_id`` → ``AgentInfo``；agent 池注册表投影（含未实例化条目，
    见 ``AgentInfo.loaded``）。只给池级「有没有、载没载」，不展开 Agent
    内部状态——实例级观测走 ``agent.snapshot()`` （两级快照不嵌套）。"""
    providers: list[str]
    """provider 候选清单名（仅名字；懒创建，实例化与否不进快照）。"""
    config_overrides: dict[str, Any]
    """配置覆盖层（``set_config`` 写入）的只读副本；修改它不回写框架。副本
    中不含凭证类敏感值。"""
    resources: list[str]
    """Resource 注册名列表（仅名字）。"""


@dataclass
class AgentSnapshot:
    """单个 Agent 的一致性只读快照：``agent.snapshot()`` 的返回类型。

    .. rubric:: 功能介绍

    Agent 实例状态的单时刻一致切片：标识与关系（``node_id`` /
    ``parent_id`` / ``agent_type``）、消息级树视图（``messages`` /
    ``current_head_id``）、当前逻辑 turn（``current_turn``）、活跃执行
    （``executions``）、消息队列规模（``message_queue``）、模型解析结果
    （``model``）、能力绑定（``tool_entries`` / ``subagent_entries``）、
    上下文占用估计（``context_usage``）。

    与 ``RuntimeSnapshot`` 分工：池级「有没有、载没载」看 Runtime 快照；
    实例级「在跑什么」看 Agent 快照（实例未加载时应先看
    ``RuntimeSnapshot.agents[id].loaded``）。

    .. rubric:: 使用示例

    .. code-block:: python

        asnap = agent.snapshot()
        if asnap.current_turn is not None:
            print("turn 已产生消息:", asnap.current_turn.message_count)
        for exc_id, exc in asnap.executions.items():
            print("在飞:", exc.kind, exc.tags)
        disabled = [e.alias for e in asnap.tool_entries if not e.visible]

    .. rubric:: 行为要点

    - 一致性 / 隔离 / 无副作用：同 ``RuntimeSnapshot`` （见模块 docstring
      一致性四条）。
    - 不暴露项：``cancel`` / ``pause`` 控制信号 Event；``TurnContext`` 的
      ``message_ids`` / ``pending_messages`` 列表本体；队列内部 deque；
      ``_pending_turns`` 的 Future；``_provided`` / ``_children`` /
      ``_messages`` 等内部 dict 引用；模型凭证。
    - 实例已 ``destroy()`` 后不应再调用（池级观察走
      ``RuntimeSnapshot.agents``）；恢复后的 Agent 首张快照
      ``current_turn is None`` （进行中的逻辑 turn 不恢复）。
    - 不提供消息内容、不提供 LLM 可见 schema、不提供历史轨迹（历史 = 日志
      插件策略，经钩子订阅收集）。

    .. seealso::

        ``flowing.agent.Agent.snapshot`` /
        ``flowing.agent.Agent.current_head_id`` / ``flowing.message.MessageChain``
        ``flowing.agent.TurnContext`` / ``flowing.agent.Execution``
        ``flowing.snapshot.RuntimeSnapshot``
    """

    node_id: str
    """节点 id（``== agent_id == session_id``，身份连续、可重现）。"""
    parent_id: str | None
    """亲节点 id。``None`` 当且仅当该节点是 Runtime 自身（链终点无亲节点）；根
    Agent 的亲节点指针指向其 Runtime 的 ``node_id``——「根」由「亲节点是 Runtime」
    表达，不由 ``None`` 表达。"""
    agent_type: str
    """字符串类型名（与池注册表元数据一致）。"""
    paused: bool
    """是否暂停中（``Agent.paused`` 的投影）。注：Agent 无生命周期状态机
    ——「在干什么」由 ``current_turn`` / ``executions`` / ``paused`` 等
    投影字段直接表达，快照不汇总出单一状态值。"""
    messages: MessageTreeInfo
    """消息级树规模与游标视图。"""
    current_head_id: str | None
    """树游标值（消息 id），与 ``messages.head_id`` 同源同刻；空树为
    ``None``。单独成字段是为与既有 ``Agent.current_head_id`` 属性读法对齐。"""
    current_turn: TurnContextInfo | None
    """当前逻辑 turn 只读视图；无执行中 turn 时为 ``None``。"""
    executions: dict[str, ExecutionInfo]
    """执行条目 id → ``ExecutionInfo``；不含控制信号（``cancel`` / ``pause``
    Event 永不进快照）。只含存活期条目（清理有 ``finally`` 保证）。"""
    message_queue: MessageQueueInfo
    """消息队列规模视图。"""
    model: ModelInfo | None
    """``self.model`` 解析结果只读视图；模型尚未解析（创建管线中）为
    ``None``。"""
    tool_entries: list[EntryInfo]
    """工具绑定条目列表（本 Agent 上的绑定结果，按别名投影）。"""
    subagent_entries: list[EntryInfo]
    """子 Agent 绑定条目列表（``agent_type`` 字段有值）。"""
    context_usage: ContextUsageEstimate | None
    """上下文占用估计的观测投影（快照时刻 ``Agent.estimate_context_tokens()``
    的取值；锚点实测 + 尾部估算的混合值，``usage_ratio`` 不 clamp、``>1``
    即溢出信号）。纯观测、永不用于计费；模型尚未解析（创建管线中）为
    ``None``。"""
