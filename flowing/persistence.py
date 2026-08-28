"""flowing.persistence —— 持久化机制层（**内部模块，非公开 API**）。

.. rubric:: 功能介绍

本模块承载 flowing 全部「记录落盘」机制，三个成员：

- :class:`RecordStore` —— 后端契约（五动词：``submit`` /
  ``replay`` / ``drain`` / ``close`` / ``sync``）。类型层 Protocol，
  零运行成本；未来接入非文件后端（数据库等）时实现同一 Protocol 即可。
- :class:`FileRecordStore` —— 文件系实现：一行一条 JSON 记录（jsonl），
  write-behind（FIFO 内存队列 + 唯一 drain 任务），提交同步、落盘异步。
- :class:`StateView` —— 状态袋视图（自 ``flowing.agent`` 迁入）：
  属性式 / 字典式访问，写透到内嵌后端（标注契约形态
  :class:`RecordStore`，构造点给具体实现）；Agent 侧单袋
  （``agent.state`` 显式视图唯一通道）、Runtime 侧按命名空间
  （``runtime.state(ns)``）共用本类。

服务的文件：每 Agent session 目录的 ``tree.jsonl``（消息行 + 变更记录
行）与 ``state.jsonl``（状态 set/delete 行）；Runtime
持久化根的 ``<namespace>.jsonl``（全局命名空间状态）。**一个文件一个
``FileRecordStore`` 实例**，实例归属该文件的属主（Agent / Runtime）。

.. rubric:: 设计动机

热路径（消息挂树、``MessageChain`` 五 op、状态赋值）是**同步**的——
让钩子与工具代码 await 落盘会把异步传染性扩散到全框架；而落盘是 IO，
天然异步。write-behind 是标准解法：**提交与执行解耦，中间用 FIFO 队列**。
提交侧 ``put_nowait`` 立即返回；唯一的 drain 任务串行消费队列写文件——
单写入者原则使压缩、末行合并不需要任何锁。

语义分层：本模块只认「记录」（``dict``）与「行」；记录的语义解释
（哪条是消息、墓碑如何作用于树、状态行的 defaults 回退）归
``flowing.message`` / ``flowing.agent`` / ``flowing.runtime``。

.. rubric:: 三条契约（所有后端实现必须兑现）

1. **提交语义 = 产生即排队**：``submit`` 返回不代表已落盘。崩溃窗口
   = 已提交未 drain 的尾部记录（正常为毫秒级）。窗口内丢失**不破坏
   一致性**：内存是权威，崩溃后权威同灭，重放恢复到的状态自洽（如同
   丢失的操作从未发生）；配合撕裂末行丢弃，文件永远不会处于半行状态。
2. **排空屏障钉死在两个位置**：① 属主收尾（Agent ``destroy`` /
   Runtime ``shutdown``）前 ``close()``（内含 ``drain``）；② 任何
   重写类维护（``sync`` 动词请求的全量重写、文件后端的墓碑压缩）
   之前必须先排空——压缩统一由 drain 任务在「队列见底」处执行，
   天然满足此前提，无需外部同步。
3. **poison = fail fast**：drain 中首次落盘失败（磁盘满 / 权限 /
   序列化错误）后，store 进入 poison 态并记住异常；此后任何
   ``submit`` **同步重抛**该异常——沉默接受会造成内存与文件永久分叉
   且无人知晓，必须让错误在操作现场爆出。

.. rubric:: 内部策略（文件后端专属，不进 Protocol）

- **末行合并**（``state.jsonl`` 开，``tree.jsonl`` 关）：同 key 连续
  set → 就地重写末行；末行 ``set k`` 紧跟 ``delete k`` → 截尾。
  文件增长率为 O（高频 distinct key 数）而非 O（写入次数）。安全性
  由单写入者 + 撕裂末行丢弃兜底：重写中途崩溃 → 末行撕裂 → 重放
  丢弃 → 退回上一完整行，语义等价于丢失最后一次赋值。
- **墓碑压缩**（tree.jsonl）：append 时见 ``{"type": "tombstone"}``
  计数 +1；队列排空后计数 ≥ ``tombstone_threshold``（默认 256）→
  整文件重写：物理清除墓碑行与被标记删除的消息行，计数归零。
  触发与执行全在 drain 任务内部（自主触发）。崩溃恢复不设
  checkpoint 机制（已删除，N-06 裁决）：撕裂末行截断丢弃即可，
  中间行损坏按 corruption 报警，不容忍。
- **state 全量压缩**（state.jsonl / ``<namespace>.jsonl``）：三时点
  触发——恢复重放后 / 属主 ``destroy`` / 行数超阈值。**触发决策归
  StateView**（它持有内存终态视图与 defaults 语义，经 ``sync``
  动词提交终态记录，触发符号 :meth:`StateView._maybe_compact`）；
  **物理重写归本类 drain 任务**（队列见底后执行，与墓碑压缩同一
  排空前提）。
- **原子重写统一规约**（墓碑压缩与 state 全量压缩共用）：同目录
  临时文件（``<file>.tmp``，同文件系统是 rename 原子性的前提）→
  写终态内容 → ``fsync`` 该临时文件（保证 rename 落盘时内容已落盘，
  否则崩溃可留下「新名字指向空 inode」）→ ``os.replace`` 覆盖原文件
  （POSIX rename 原子：任何时刻读者只见旧或新，无半压缩状态）；
  打开存储时清扫孤儿 ``.tmp``。目录 fd 的 ``fsync``（保目录项持久）
  **暂不引入**——压缩低频，可承受「白做一次」，语义上限已是无撕裂。

.. rubric:: 格式版本与迁移

每个 jsonl 文件携带**文件级格式版本号**：首行恒为元数据记录
``{"type": "meta", "format_version": <int>}``。选首行的理由：
append-only 模型下首行天然稳定（sync 原子重写时元数据记录原样
保留在最前），``replay`` 从第一行读起、读到即知版本，无需任何
额外 IO 或侧边文件。当前行格式即 ``format_version = 1``；**无版本
号首行的存量文件按版本 0 处理**，走同一条迁移链。

- **重放时迁移**：``replay()`` 开头读首行版本号；低于当前版本 →
  经模块级迁移链 ``MIGRATIONS: dict[int, Callable]``（``MIGRATIONS[1]``
  = v1→v2 这类**相邻版本**变换函数，逐段串联升级）在内存中对重放
  产物生效；高于当前版本 → 报错（文件比框架新，静默读是数据风险）。
- **迁移后回写**：版本落后的文件在迁移完成后借 ``sync`` 的原子
  重写通道整体重写为新版本（含新首行）——否则每次启动都迁移一遍，
  且后续压缩重写会产生混合版本内容。迁移+回写是恢复路径的一次性
  动作，热路径零成本。
- 版本号是**文件级**而非记录级：同一文件内不混版本（回写与 sync
  重写共同保证）。
- 行格式变更的纪律：任何改变既有记录行字段结构/语义的修改 =
  格式版本 +1 并登记一段迁移函数；只增不减的兼容字段可由调用方
  ``dict.get`` 兜底，不算版本变更。

.. rubric:: 后端演进缝（写明但不实现）

五动词契约 + 实例方法形态的 ``replay``（定位信息在构造时给出，文件
后端是 ``Path``，数据库后端是连接串）构成唯一接缝。压缩物理机制 /
末行合并是文件后端的内部策略，**不进 Protocol**——
别的后端有自己的维护方式（但「``sync`` = 原子重写为给定终态」
的语义承诺各后端必须兑现）。**不做**后端注册表、配置项、分布式参数——换装
点固定为构造点（``Agent.__init__`` 经 ``_open_stores``（P3-03）/
``Runtime.register_state``），将来有真实需求时再加注入参数。

.. rubric:: 透明性分层

- Agent 代码只见：``_persist_message`` / ``_persist_tree_record``
  （提交）、``_restore``（重放驱动）、``destroy``（收尾）；
- Runtime 代码只见：``register_state`` / ``state(ns)`` /
  ``shutdown`` 收尾；
- 插件**完全接触不到** ``RecordStore``——Agent 作用域走
  ``agent.register_state`` / ``agent.state``（显式视图唯一通道，
  P3-03），全局走
  ``runtime.register_state`` / ``runtime.state(ns)``；超出 jsonl
  状态袋的持久化需求（自有格式文件、数据库）由插件自管文件。

.. rubric:: 参见

- :class:`flowing.agent.Agent` —— tree/state 两 store 的属主。
- :class:`flowing.runtime.Runtime` —— 全局命名空间 store 的属主。
- :class:`flowing.message.MessageChain` —— 变更记录行（tombstone /
  update / move / 邻接调整）的产生者。
"""

import asyncio
import contextlib
import json
import logging
import os
from collections import deque
from collections.abc import Iterator
from pathlib import Path
from typing import Any, Protocol

from flowing.errors import CorruptionError, FormatVersionError

logger = logging.getLogger(__name__)

FORMAT_VERSION: int = 1
"""当前 jsonl 行格式版本（模块 docstring「格式版本与迁移」）。

每个持久化文件的首行恒为 ``{"type": "meta", "format_version": FORMAT_VERSION}``
（sync / 新建时写入）；无首行的存量文件按版本 0 走迁移链。行格式变更 =
本常量 +1 并在 :data:`MIGRATIONS` 登记一段相邻版本迁移函数。
"""

MIGRATIONS: dict[int, "Any"] = {}
"""迁移链：``MIGRATIONS[v]`` = v → v+1 的相邻版本迁移函数（输入旧版本
记录序列，输出新版本记录序列）；``replay`` 从文件版本起逐段串联升级至
:data:`FORMAT_VERSION`。当前为空表（v1 即现状，无待迁移版本）。
"""


class RecordStore(Protocol):
    """持久化后端契约（五动词）——类型层 Protocol，零运行成本。

    .. rubric:: 功能介绍

    任何后端（本模块的 :class:`FileRecordStore`，或未来的数据库后端）
    兑现这五个动词即可替换接入：``submit``（同步提交一条记录）、
    ``replay``（实例方法，按写入序逐条吐记录，撕裂末行丢弃）、
    ``drain``（排空屏障）、``close``（排空 + 停写任务）、
    ``sync``（原子重写为给定终态记录）。

    .. rubric:: 设计动机

    把「Agent/Runtime 依赖的面」显性化：两侧对后端的全部依赖就这
    五个方法 + 「记录是 dict」的约定。契约显性化后，第二个后端出现
    时接口分歧在类型检查阶段即暴露。物理布局（append / 末行合并 /
    压缩机制）是各后端的实现细节，**不进本契约**——但各动词的
    **语义承诺**（如 sync 的原子性）各后端必须兑现。

    .. rubric:: 行为规约

    - 兑现 persistence 模块 docstring 的三条契约（排队语义 / 两个
      排空屏障点 / poison 重抛）。
    - ``replay`` 按写入顺序产出；遇撕裂末行（尾部不完整 JSON 行）
      截断丢弃，不报错。首行 ``{"type": "meta", "format_version"}``
      元数据记录**不产出**给调用方——版本判读与低版本迁移在
      ``replay`` 内部完成（见模块 docstring「格式版本与迁移」），
      调用方拿到的永远是当前版本的记录序列。
    - ``sync`` 语义承诺：重写完成后存储内容 = 给定终态记录序列；
      过程原子（要么旧要么新，无半重写状态）；生效时机同排队语义
      （请求排队，drain 见底后执行）。

    .. seealso:: :class:`FileRecordStore` —— 文件系实现。
    """

    def submit(self, record: dict) -> None:
        """同步提交一条记录（排队即返；poison 态重抛）。"""
        ...
    def replay(self) -> Iterator[dict]:
        """按写入序逐条产出记录（撕裂末行截断丢弃）。"""
        ...
    async def drain(self) -> None:
        """排空屏障：返回时此前提交的记录已全部落盘。"""
        ...
    async def close(self) -> None:
        """排空并停止写任务；幂等。"""
        ...
    def sync(self, records: list[dict]) -> None:
        """请求将存储整体同步为给定终态记录序列（原子重写）。

        **命名（X1 裁决）**：本动词叫 ``sync`` 而非 compact——终态由
        调用方（StateView）从其**内存权威**（``_persisted`` + defaults
        语义）导出传入，不是后端重放文件归约：文件可能有已提交未
        drain 的尾部记录，重放会得到旧值。真正的「重放 → 归约 → 写回」
        式压缩是文件后端的墓碑压缩（store 自主），与本动词分工明确。

        **没有内存事实的恢复路径**：进程重启后内存为空，文件是唯一
        事实来源——``replay()`` 按行序重建（撕裂末行丢弃），重建完成
        前写闸门锁定、无新写产生，此刻文件与重建出的内存视图一致；
        恢复收尾的 sync（压缩三时点①）即以这份重建出的内存视图为
        终态导出，同时把撕裂末行的逻辑丢弃固化为物理清除。

        排队语义同 ``submit``（请求入队即返，drain 见底后执行，执行前
        天然满足排空前提）；语义承诺原子——任何时刻观察者只见旧内容
        或新内容，无半同步状态。后端不解释记录内容。
        """
        ...


class FileRecordStore:
    """文件系记录存储：一行一条 JSON 记录，write-behind 落盘。

    **内部 API，不属稳定契约。** 本类是 :class:`RecordStore`（契约
    Protocol）的文件系实现，也是当前唯一实现。

    .. rubric:: 功能介绍

    一个实例管一个文件。写侧：``submit`` 同步入队（FIFO），唯一的
    drain 任务串行取出并 append；读侧：``replay`` 按序重放（撕裂
    末行丢弃）；收尾：``close`` = ``drain`` + 停 drain 任务；
    维护：``sync`` 请求入队，drain 见底后执行原子重写。
    内部维护（对调用方透明、不进 Protocol）：末行合并
    （``merge_last_line=True`` 时）、墓碑压缩
    （``tombstone_threshold``）——均由 drain 任务在「队列排空后」
    自主触发（机制见模块 docstring「内部策略」，原子重写统一规约
    同见该节）。

    .. rubric:: 设计动机

    write-behind 解耦同步热路径与异步 IO（模块 docstring「设计
    动机」）；单实例单 drain 任务 = 单写入者，所有重写类维护与
    日常 append 在同一任务内串行发生，零锁。poison 重抛保证
    「内存权威与文件分叉」在第一时间暴露而不是静默扩散。

    .. rubric:: 使用示例

    .. code-block:: python

        # Agent 侧（Agent.__init__ 经 _open_stores，P3-03）：
        self._tree_store = FileRecordStore(session_dir / "tree.jsonl")
        self._tree_store.submit({"type": "message", "id": ..., ...})

        # Runtime 侧（构造点在 Runtime.register_state）：
        store = FileRecordStore(persist_dir / "core.jsonl", merge_last_line=True)

    .. rubric:: 行为规约

    - **FIFO**：同一实例的记录按 ``submit`` 调用顺序落盘。
    - **排空屏障**：``drain()`` 返回 ⇒ 此前全部 ``submit`` 已落盘；
      属主收尾必须走 ``close()``（含 drain），否则尾部记录静默丢。
    - **poison**：drain 中首次写盘异常后，``submit`` 同步重抛同一
      异常；store 不再接受新记录。
    - **replay 容错**：撕裂末行截断丢弃；中间行损坏视为 corruption
      （报警 / 修复，不容忍——沿用持久化层一贯约定）。
    - **压缩排空前提**：墓碑压缩与 ``compact`` 请求的全量重写只在
      drain 任务内、队列见底后执行，不可能与在队记录竞争（契约②的
      结构性兑现）。
    - **sync 原子重写**：按模块 docstring「原子重写统一规约」执行
      （同目录 ``.tmp`` → fsync 临时文件 → ``os.replace``；打开时
      清扫孤儿 ``.tmp``）；目录 fd fsync 不引入。
    - 非行为：不做跨实例排序（跨 Agent / 跨命名空间无顺序承诺）；
      热路径（``submit``/append）不做 fsync 策略承诺（机器级掉电
      耐受为演进位；sync 路径的文件 fsync 是原子性前提，不在此列）。

    :param path: 目标文件路径（父目录由属主管线保证存在）。
    :param merge_last_line: 同 key 连续写时就地重写末行
        （state.jsonl 开、tree.jsonl 关）。
    :param tombstone_threshold: 墓碑计数触发压缩的阈值（默认 256；
        仅 tree.jsonl 有意义）。

    .. rubric:: 测试案例

    - FIFO：连续 ``submit`` 100 条 → ``replay`` 顺序一致。
    - 排空：``submit`` 后立即 ``close`` → 重放可见全部记录。
    - 崩溃窗口：``submit`` 后不 drain、直接丢弃内存模拟崩溃 →
      重放不含该记录，但文件自洽（撕裂末行被截）。
    - poison：drain 中注入写盘异常 → 后续 ``submit`` 同步抛同一异常。
    - 末行合并：同 key 连续 set 1000 次 → 文件该 key 仅 1 行。
    - 墓碑压缩：制造 256+ 条 tombstone → drain → 文件行数下降且
      重放结果不变。
    - 中间行损坏：重放遇损坏行 → corruption 报警（不容忍）。

    .. rubric:: 调用关系（审计）

    - 调用：无框架内具名符号（物理 IO 由 drain 任务执行）
    - 被调：``flowing.agent.Agent.__init__``（经 ``_open_stores()``
      构造 tree/state 两实例，P3-03）、``flowing.agent.Agent._persist_message()`` /
      ``_persist_tree_record()``（submit 通道）、
      ``flowing.agent.Agent._restore()`` / ``destroy()``（replay /
      close）、``flowing.runtime.Runtime.register_state()``（构造
      全局命名空间实例）/ ``shutdown()``（close）
    - 实例化方：Agent（tree.jsonl / state.jsonl 经 StateView 内嵌）
      与 Runtime（每个全局命名空间一个）

    .. seealso:: :class:`RecordStore`（后端契约 Protocol）、
        :class:`StateView`（状态袋视图，经契约形态内嵌本类）。
    """

    _path: Path
    """目标文件路径。内部 API。
    """
    _merge_last_line: bool
    """末行合并开关（state.jsonl 开 / tree.jsonl 关）。内部 API。
    """
    _tombstone_threshold: int
    """墓碑压缩阈值（默认 256；仅 tree.jsonl 语义）。内部 API。
    """
    _poisoned: BaseException | None
    """poison 态记住的首次落盘异常（``None`` = 健康）。内部 API。
    """

    def __init__(
        self,
        path: Path,
        *,
        merge_last_line: bool = False,
        tombstone_threshold: int = 256,
    ) -> None:
        """绑定文件并启动 drain 任务（内部 API，不属稳定契约）。

        构造点即后端换装点（模块 docstring「后端演进缝」）：Agent 侧在
        ``Agent.__init__``（经 ``_open_stores``，P3-03），Runtime 侧在
        ``Runtime.register_state``。

        .. rubric:: 调用关系（审计）

        - 调用：无（启动 drain 任务为内部细节）
        - 被调：``flowing.agent.Agent.__init__``（经 ``_open_stores()``，
          时机：同步骨架首段）、
          ``flowing.runtime.Runtime.register_state()``（时机：全局
          命名空间声明时）
        """
        self._path = Path(path)
        self._merge_last_line = merge_last_line
        self._tombstone_threshold = tombstone_threshold
        self._poisoned: BaseException | None = None
        # FIFO 内存队列 + 唯一 drain 任务（write-behind；单写入者原则）
        # 队列项形态：("record", dict) / ("sync", list[dict]) / ("barrier", Event)
        self._queue: deque[tuple] = deque()
        # X8 澄清：Agent 创建期不做 await（drain 任务需运行中 loop）——
        # drain 任务惰性启动于首个 submit / sync / drain 调用；约束「首次
        # 提交须在运行中的事件循环内」。
        self._drain_task: asyncio.Task | None = None
        self._wakeup: asyncio.Event | None = None
        self._closed = False
        # 写侧状态（drain 任务私有，单写入者无需锁）
        self._file = None            # 二进制句柄（r+b / w+b），惰性打开
        self._write_offset = 0       # 当前文件末尾字节偏移
        self._last_line: tuple[int, str, str] | None = None  # 末行合并跟踪（offset, op, key）
        # 末行合并的截尾安全网：仅当本 session 已知文件从空开始、且被截尾的
        # set 是该 key 在文件中的唯一一行时，delete 才随之省略（否则截尾会
        # 复活更早的同名 set 行）。X9 澄清：注释当前状态与实际期望——
        # 当前实现仅对「新建文件上的连续同 key 序列」做截尾省行，跨交错
        # 序列一律保守补写 delete 行。
        self._fresh_start = False
        self._key_line_counts: dict[str, int] = {}
        self._tombstone_count = 0    # 墓碑计数（append 时 +1；replay 初始化加载时累计）
        # 打开存储时清扫孤儿 .tmp（原子重写统一规约）
        tmp = Path(str(self._path) + ".tmp")
        if tmp.exists():
            tmp.unlink()
    def submit(self, record: dict) -> None:
        """同步提交一条记录：poison 检查 → FIFO 入队，立即返回。

        **返回不代表已落盘**（契约①「产生即排队」）；落盘失败历史
        （poison 态）使本方法**同步重抛**首次异常（契约③）。记录必须
        JSON 可序列化——校验在 drain 落盘时执行，不可序列化即 poison
        来源之一。

        .. rubric:: 调用关系（审计）

        - 调用：无（FIFO 队列入队）
        - 被调：``flowing.agent.Agent._persist_message()`` /
          ``_persist_tree_record()``（时机：每次挂树与树手术）、
          :meth:`StateView.__setitem__` / ``__delitem__``（时机：
          每次状态写透）
        """
        if self._closed:
            raise RuntimeError(f"record store already closed: {self._path}")
        if self._poisoned is not None:
            raise self._poisoned  # 契约③：poison 态同步重抛首次落盘异常
        self._queue.append(("record", record))
        self._ensure_drain_task()
        self._wakeup.set()
    def replay(self) -> Iterator[dict]:
        """按写入序逐条产出记录；撕裂末行截断丢弃，中间行损坏报警。

        实例方法（非 classmethod）：定位信息在构造时给出——这是非文件
        后端的演进缝（数据库后端无路径概念）。只读，不写、不维护
        （低版本文件的迁移后回写是例外：借 ``sync`` 通道完成，
        见下）。

        版本处理（模块 docstring「格式版本与迁移」）：首行
        ``{"type": "meta", "format_version"}`` 不产出给调用方；
        版本低于当前 → 经 ``MIGRATIONS`` 迁移链逐段升级到当前版本后
        产出，并借 ``sync`` 把文件原子回写为新版本（一次性动作）；
        版本高于当前 → 报错；无版本首行的存量文件按版本 0 走同一
        迁移链。

        .. rubric:: 调用关系（审计）

        - 调用：无（只读文件；低版本时 ``sync`` 回写）
        - 被调：``flowing.agent.Agent._restore()``（时机：recover 管线
          重放 tree.jsonl / state.jsonl——重放前按版本号迁移即由此
          承载）、``flowing.runtime.Runtime``
          引导重放（时机：``set_persist_dir`` 后、池扫描前重放全局
          命名空间）
        """
        records, file_version = self._read_records_with_version()
        if file_version > FORMAT_VERSION:
            raise FormatVersionError(self._path, file_version, FORMAT_VERSION)
        if file_version < FORMAT_VERSION:
            for v in range(file_version, FORMAT_VERSION):
                migrate = MIGRATIONS.get(v)
                if migrate is not None:
                    records = list(migrate(records))
                # 缺段 = 恒等（X5 澄清：v0 与 v1 行格式相同，v1 仅新增
                # meta 首行约定，无待迁移版本，不做特判、不虚构迁移函数）
            # 迁移后借 sync 通道原子回写为新版本（含新 meta 首行；
            # 一次性动作）。约束：须在运行中的事件循环内消费完本生成器
            # （sync 经队列提交，drain 任务惰性启动，X8）；无 loop 时跳过
            # 回写仅告警——记录照常产出，下次 replay 重试迁移。
            try:
                self.sync(records)
            except RuntimeError:
                logger.warning(
                    "migration rewrite of %s deferred: no running event loop",
                    self._path,
                )
        # X16 澄清：墓碑计数在初始化加载（replay）时一并累计
        self._tombstone_count += sum(
            1 for r in records if r.get("type") == "tombstone"
        )
        yield from records
    async def drain(self) -> None:
        """排空屏障：返回时此前提交的记录已全部落盘。

        两个钉死调用点（契约②）：属主收尾（经 ``close``）与重写类
        维护之前。压缩由 drain 任务内部触发，无需外部调本方法。

        .. rubric:: 调用关系（审计）

        - 调用：无（等待队列消费完毕）
        - 被调：:meth:`close`（时机：收尾排空）；墓碑压缩的内部前提
          （时机：drain 任务队列见底、计数超阈值时）
        """
        if self._drain_task is None and not self._queue:
            # 从未提交（drain 任务惰性未启动）：无在队记录，drain 任务私有
            # 状态不可能被并发触碰，直接在本调用点做压缩检查——覆盖
            # 「replay 初始化加载累计墓碑后随即 drain / close」的路径（X16）
            self._maybe_compact_tombstones()
            return
        self._ensure_drain_task()
        if self._drain_task.done():
            return  # drain 任务已终结（poison / close），barrier 不会有人消费
        barrier = asyncio.Event()
        self._queue.append(("barrier", barrier))
        self._wakeup.set()
        await barrier.wait()
    async def close(self) -> None:
        """排空 + 停止 drain 任务；幂等（重复调用安全）。

        .. rubric:: 调用关系（审计）

        - 调用：:meth:`drain`（时机：收尾排空）
        - 被调：``flowing.agent.Agent.destroy()``（时机：工作循环取消后）、
          :meth:`StateView._close`（时机：随属主收尾）、
          ``flowing.runtime.Runtime.shutdown()``（时机：全局命名空间
          收尾）
        """
        if self._closed:
            return  # 幂等
        self._closed = True
        if self._drain_task is None:
            return  # 从未启动（无提交），无任务可停
        await self.drain()  # 排空屏障（barrier 处含墓碑压缩检查，X16 收尾时点）
        self._drain_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._drain_task
    def sync(self, records: list[dict]) -> None:
        """契约动词实现：请求将本文件全量原子同步为给定终态记录。

        poison 检查后把维护标记（含终态记录）入队即返；drain 任务在
        队列见底后取出执行原子重写（同目录 ``.tmp`` → fsync 临时文件
        → ``os.replace``，见模块 docstring「原子重写统一规约」）。
        终态记录由 StateView 从内存权威导出传入（X1：非重放文件归约，
        见 ``RecordStore.sync`` 命名说明），本类不解释记录内容。重写时
        首行 ``meta`` 版本记录原样保留在最前（「格式版本与迁移」节），
        重写产物永远是当前版本格式。

        .. rubric:: 调用关系（审计）

        - 调用：无（维护标记入队；物理重写在 drain 任务内）
        - 被调：:meth:`StateView._maybe_compact`（时机：三时点——
          恢复重放后 / 属主 ``destroy`` 收尾 / append 行数超阈值）
        """
        if self._closed:
            raise RuntimeError(f"record store already closed: {self._path}")
        if self._poisoned is not None:
            raise self._poisoned  # poison 检查同 submit（契约③）
        # 维护标记入队；FIFO 序保证执行时此前记录均已落盘（排空前提的
        # 结构性兑现），drain 任务处理到本标记即执行原子重写
        self._queue.append(("sync", list(records)))
        self._ensure_drain_task()
        self._wakeup.set()

    # ------------------------------------------------------------------
    # 以下为内部实现（单写入者：写侧状态仅由 drain 任务触碰）
    # ------------------------------------------------------------------

    def _ensure_drain_task(self) -> None:
        """惰性启动唯一 drain 任务（X8：需在运行中的事件循环内调用）。"""
        if self._drain_task is None:
            self._wakeup = asyncio.Event()
            self._drain_task = asyncio.get_running_loop().create_task(
                self._drain_loop()
            )

    async def _drain_loop(self) -> None:
        """唯一 drain 任务：串行消费队列写文件，见底处做维护（压缩 / barrier）。"""
        try:
            while True:
                if not self._queue:
                    # 队列见底：墓碑压缩的自主触发点（S-31；X16 只按计数）
                    self._maybe_compact_tombstones()
                    assert self._wakeup is not None
                    self._wakeup.clear()
                    await self._wakeup.wait()
                    continue
                kind, payload = self._queue.popleft()
                if kind == "record":
                    self._write_record(payload)
                elif kind == "sync":
                    self._atomic_rewrite(payload)
                else:  # barrier：排空信号，先收尾维护再唤醒等待方
                    self._maybe_compact_tombstones()
                    payload.set()
        except asyncio.CancelledError:
            raise  # close() 的正常收尾路径
        except Exception as exc:
            # poison = fail fast（契约③）：记住首次落盘异常，唤醒全部
            # 排队 barrier（drain 等待方不挂死），任务终结；此后 submit
            # 同步重抛。异常不在任务内再抛（避免 unretrieved 任务告警）。
            self._poisoned = exc
            logger.error("record store poisoned (%s): %s", self._path, exc)
            for item in self._queue:
                if item[0] == "barrier":
                    item[1].set()
            self._queue.clear()
        finally:
            if self._file is not None:
                self._file.close()
                self._file = None

    def _read_records_with_version(self) -> tuple[list[dict], int]:
        """读文件全部完整行 → (记录列表, 文件版本)；meta 首行不进记录列表。

        撕裂末行（无换行结尾的尾部残行）截断丢弃；中间行 JSON 损坏 →
        logging.error 报警 + CorruptionError（X7，不容忍）。
        """
        if not self._path.exists():
            return [], FORMAT_VERSION  # 文件不存在 = 新建，视为当前版本
        raw = self._path.read_bytes()
        if not raw:
            return [], FORMAT_VERSION  # 空文件同上
        segments = raw.split(b"\n")
        segments.pop()  # 末段：文件以 \n 结尾时为 b""；否则即撕裂末行，丢弃
        version = 0  # 无 meta 首行的存量文件按版本 0 处理（X5 澄清：无特判）
        had_meta = False
        if segments:
            try:
                first = json.loads(segments[0])
            except json.JSONDecodeError:
                logger.error("corrupted record line at %s:%d", self._path, 1)
                raise CorruptionError(self._path, 1) from None
            if isinstance(first, dict) and first.get("type") == "meta":
                found = first.get("format_version")
                if not isinstance(found, int):
                    logger.error("corrupted record line at %s:%d", self._path, 1)
                    raise CorruptionError(self._path, 1)
                version = found
                had_meta = True
                segments = segments[1:]
        records: list[dict] = []
        start_lineno = 2 if had_meta else 1
        for i, seg in enumerate(segments, start=start_lineno):
            try:
                records.append(json.loads(seg))
            except json.JSONDecodeError:
                logger.error("corrupted record line at %s:%d", self._path, i)
                raise CorruptionError(self._path, i) from None
        return records, version

    def _ensure_file(self):
        """惰性打开文件句柄；新建 / 空文件先写 meta 首行（格式版本约定）。"""
        if self._file is None:
            if self._path.exists():
                self._file = open(self._path, "r+b")
                self._file.seek(0, os.SEEK_END)
                self._write_offset = self._file.tell()
                if self._write_offset == 0:
                    self._fresh_start = True
                    self._write_meta()
            else:
                self._file = open(self._path, "w+b")
                self._write_offset = 0
                self._fresh_start = True
                self._write_meta()
        return self._file

    def _write_meta(self) -> None:
        """在当前写入位置写 meta 首行（仅新建 / 重写时调用）。"""
        line = json.dumps(
            {"type": "meta", "format_version": FORMAT_VERSION},
            ensure_ascii=False,
        ).encode("utf-8") + b"\n"
        self._file.write(line)
        self._file.flush()
        self._write_offset += len(line)

    def _write_record(self, record: dict) -> None:
        """append 一条记录（含末行合并 / 截尾的内部策略，merge_last_line 开时）。"""
        data = json.dumps(record, ensure_ascii=False).encode("utf-8") + b"\n"
        f = self._ensure_file()
        op = record.get("op")
        key = record.get("key")
        if self._merge_last_line and self._last_line is not None:
            loffset, lop, lkey = self._last_line
            if lop == "set" and lkey == key and op == "set":
                # 同 key 连续 set → 就地重写末行（X9：seek + truncate 机制，
                # POSIX 语义。跨平台文件锁 / 偏移语义若出问题，演进方向是
                # 退化为 sync 式整文件重写——逻辑契约「透明性」不变）
                f.seek(loffset)
                f.write(data)
                f.truncate()
                f.flush()
                self._write_offset = loffset + len(data)
                return  # _last_line 不变（offset/op/key 均同）
            if lop == "set" and lkey == key and op == "delete":
                # 末行 set k 紧跟 delete k → 截尾。安全网（见 __init__
                # 注释）：仅当已知该 set 是 k 在文件中的唯一一行时才省略
                # delete 行，否则截尾后补写 delete（防复活更早的同名 set）
                f.seek(loffset)
                f.truncate()
                f.flush()
                self._write_offset = loffset
                self._last_line = None
                self._key_line_counts[key] = self._key_line_counts.get(key, 1) - 1
                if self._fresh_start and self._key_line_counts[key] <= 0:
                    return  # k 在文件中已无行 → delete 一并省略
                f.seek(self._write_offset)
                f.write(data)
                f.flush()
                self._write_offset += len(data)
                return
        f.seek(self._write_offset)
        f.write(data)
        f.flush()
        if self._merge_last_line and op in ("set", "delete") and key is not None:
            self._last_line = (self._write_offset, op, key)
            if op == "set":
                self._key_line_counts[key] = self._key_line_counts.get(key, 0) + 1
        else:
            self._last_line = None
        self._write_offset += len(data)
        if record.get("type") == "tombstone":
            self._tombstone_count += 1  # 墓碑计数：append 时 +1

    def _atomic_rewrite(self, records: list[dict]) -> None:
        """原子重写统一规约：同目录 .tmp → fsync 临时文件 → os.replace。

        调用方传入的终态记录**不含** meta（X12）；本函数自动写当前版本
        meta 首行再写 records。重写后旧句柄指向旧 inode——关闭并惰性
        重开；末行合并跟踪随文件内容变化失效，重置为已知新起点。
        """
        tmp = Path(str(self._path) + ".tmp")
        with open(tmp, "wb") as f:
            f.write(
                json.dumps(
                    {"type": "meta", "format_version": FORMAT_VERSION},
                    ensure_ascii=False,
                ).encode("utf-8")
                + b"\n"
            )
            for record in records:
                f.write(json.dumps(record, ensure_ascii=False).encode("utf-8") + b"\n")
            f.flush()
            os.fsync(f.fileno())  # rename 落盘时内容已落盘（否则可留空 inode）
        os.replace(tmp, self._path)  # POSIX rename 原子：读者只见旧或新
        if self._file is not None:
            self._file.close()
            self._file = None
        self._write_offset = 0
        self._last_line = None
        # 重写产物内容已知：终态记录即文件全部记录
        self._fresh_start = True
        self._key_line_counts = {}
        for record in records:
            if record.get("op") == "set" and record.get("key") is not None:
                self._key_line_counts[record["key"]] = (
                    self._key_line_counts.get(record["key"], 0) + 1
                )

    def _maybe_compact_tombstones(self) -> None:
        """墓碑压缩：计数 ≥ 阈值时整文件原子重写（drain 见底 / barrier 处触发）。

        X16 澄清：只按计数触发（初始化加载 replay 累计 / 最终销毁 close
        收尾的 drain 也会经此）；「Agent 空闲」条件由 L2/L3 的触发时机
        结构性保证，store 不感知 Agent。
        """
        if self._tombstone_count < self._tombstone_threshold:
            return
        records, _ = self._read_records_with_version()
        tombstoned = {r["id"] for r in records if r.get("type") == "tombstone"}
        kept = [
            r
            for r in records
            if r.get("type") != "tombstone"
            and not (r.get("type") == "message" and r.get("id") in tombstoned)
        ]
        self._atomic_rewrite(kept)  # 物理清除墓碑行与被标记删除的消息行
        self._tombstone_count = 0


class StateView:
    """状态袋视图——写透持久化，插件无 save 概念（M-68 最终裁决；
    单袋化最终裁决：Agent 侧去命名空间（``agent.state`` 显式视图唯一通道），
    Runtime 侧保留命名空间）。

    .. rubric:: 功能介绍

    :meth:`Agent.register_state` 逐键声明（``register_state("count", 0)``），
    :attr:`Agent.state` property 返回本视图——**一个 Agent 一袋**，无
    命名空间。Runtime 侧保留命名空间：``Runtime.register_state`` /
    ``Runtime.state(ns)`` 按命名空间各返回一个本类实例（命名空间一文件、
    无宿主 agent），本类契约对两侧一致适用。语义上，**每个状态量就是该
    智能体的一个特殊属性**：「特殊」体现在读写走持久化通道（写透到该
    Agent 自己 session 目录的 ``state.jsonl``）、有声明过的默认值、
    recover 时被落盘值覆盖。Agent 侧访问形态（P3-03 配套裁决：**读写
    统一走显式视图**，``agent.xxx`` 不回退状态量）::

        agent.state.count += 1        # 属性式（key 须为合法标识符）
        agent.state["weird-key"] = 1  # 字典式（任意字符串 key）
        del agent.state.jobs          # 删除持久值；之后读回退到 default（若有）

    经 ``agent.count = 1`` / ``del agent.count`` 写删已注册状态键 →
    :class:`flowing.errors.StateKeyError`（防遮蔽 fail-fast：同名实例
    属性与状态键并存会造成两处真值静默漂移）。写透后 dispatch 属主
    watcher 通道（``watch`` 对状态键照常生效，触发点在本类；``del``
    不触发）。

    .. rubric:: 设计动机

    状态系统的意义是让插件**放心**：耐久性依赖插件作者记得调 save 的
    设计，失败模式是静默丢状态（危险方向）。写透把默认方向反过来——
    忘记做任何事都安全；「这个状态不需要恢复」（高频遥测等）才是插件
    的显式决策，其代价只是文件多几行（安全方向）。Agent 侧单袋
    （Pinia 式）：读写统一走 ``agent.state`` 显式视图——同名歧义由
    **声明期冲突检测**挡在前面（撞 Agent 类属性 / 方法 / ``_extra``
    键 / 已注册状态键 → ``register_state`` 立即报错），写侧另有
    ``StateKeyError`` 防遮蔽护栏。Runtime 侧不拍平
    （本体方法多、使用者为带前缀约定的插件、收益小），仍经
    ``runtime.state(ns)`` 显式访问。

    .. rubric:: 使用示例

    .. code-block:: python

        class MyAgent(Agent):
            async def setup(self, **args):
                self.register_state("tracker_count", 0)

        async def on_something(agent, ctx):
            agent.state.tracker_count += 1   # 写透落盘，watch/watcher 照常触发

    .. rubric:: 行为规约

    - **写透**：set/delete 经 ``_store.submit`` **同步提交、排队即
      返回**（``submit`` 返回不代表已落盘，见模块 docstring 契约①）；
      物理 append / 末行合并由后端 drain 任务执行（RecordStore 层
      内部策略，P1-20 裁决）。值必须 JSON 可序列化，否则**写入时**
      报错（fail fast，不留到恢复时爆雷）。
    - **写闸门**：视图在管线到位前锁定——create 管线在「池元数据 +
      初始 state 写盘」后解锁；recover 管线在 :meth:`Agent._restore`
      完成后解锁。闸门未开时**写**抛错（fail fast）；**读**只见
      defaults 不见持久值（重放尚未执行）。因此 ``setup()`` 中禁写
      state，初始值一律走 ``register_state`` 的 ``default``。
    - **defaults 回退**：``register_state(key, default)`` 提供键的
      默认值；**默认值不落盘**（不产生 ``state.jsonl`` 行）；读出时按
      ``持久值 ?? default`` 回退；recover 时落盘值覆盖默认值。
    - **末行合并（归属 RecordStore 层，P1-20 裁决）**：同 key 连续
      set 的物理合并（就地重写末行 / 截尾）是**后端的内部策略**，
      由 store drain 任务执行，对本视图透明；本层只承诺「逻辑内容
      = 全部操作的顺序重放结果」（见下文承诺④）。物理机制
      （``pwrite`` / ``ftruncate``）与文件增长率论证见模块
      docstring「内部策略」与 :class:`FileRecordStore`。
    - **崩溃容忍**：加载器重放时截断到最后一条完整换行结尾的行，
      尾部残行（就地重写的撕裂产物）丢弃——至多丢失崩溃前最后一次
      修改，语义等价于断电。
    - **压缩三时点**：恢复重放后 / Agent ``destroy`` / 行数超阈值，
      将终态**持久化状态**原子重写（临时文件 + rename）；写入侧永远
      append 或就地改末行，热路径无压缩。**承担主体（P3-13 裁决）**：
      触发决策归本类——触发符号 :meth:`_maybe_compact`（三时点调用方：
      ``Agent._restore`` 收尾、``Agent.destroy`` 收尾（force）、
      ``__setitem__``/``__delitem__`` 的 append 计数超
      ``_compact_threshold``）；终态计算也归本类（仅 ``_persisted``
      持久值——defaults 不落盘原则不变），经 ``_store.sync(
      terminal_records)`` 提交；物理重写归后端 drain 任务（原子重写
      统一规约见模块 docstring「内部策略」）。（术语约定：持久化侧
      一律称「持久化状态」，「快照 / Snapshot」专指观测通道的只读
      投影，两者无转换关系。）
    - **契约**：只放恢复真正需要的状态；高频遥测数据属插件内存。
      **时间缓冲/消抖（debounce 批量写）明确不引入**——末行合并已
      覆盖单 key 高频场景，时间缓冲只会引入崩溃丢失窗口而换不到
      实质 IO 收益（M-68 裁决）。
    - **演进位（写明但不实现）**：多线程下本视图的写需 per-agent
      写锁（粒度 = 每 agent 一把，文件本就 per-agent，无全局瓶颈）；
      分布式需租约保证单 writer、池扫描换共享索引、append-only 后端
      （``pwrite``/``ftruncate`` 是 POSIX 特性）——逻辑契约（有序、
      后写覆盖先写、崩溃截断）与后端解耦。

    .. rubric:: 对下游的承诺（语义保证）

    插件可以依赖的不变量，按强度排序：

    1. **写透耐久性（崩溃窗口 = 已提交未 drain 的尾部）**：set/delete
       返回只代表操作已 ``submit`` 进 store 队列，**不代表已落盘**
       （P1-19 裁决，与模块 docstring 契约①同口径）。drain 完成后的
       修改在**进程崩溃**下不丢（写入已进 OS 页缓存即达标；机器级
       掉电的 fsync 策略初版不承诺，列为演进位）。
    2. **恢复一致性**：``recover_agent`` 返回后，本视图重建出的逻辑
       字典与崩溃前完全一致（per-key 后写覆盖先写，delete 删除）；
       唯一例外是崩溃点尾部残行对应的至多最后一次修改。
    3. **顺序性**：同一 Agent 的写操作按调用顺序生效；单线程语义，
       跨 Agent 无顺序承诺。
    4. **透明性**：末行合并与压缩不改变任何逻辑内容——插件观察到
       的字典永远等于「全部操作的顺序重放结果」。
    5. **隔离性（约定级）**：插件键名按约定带注册名 underscore 前缀
       （如 cron 插件的 ``cron_jobs``）；框架核心键裸名保留（如
       ``current_head_id``），插件声明撞核心键 → ``register_state``
       报错。插件间互写在单袋内无机制阻止，靠前缀约定与声明期冲突
       检测约束。
    6. **fail fast**：不可 JSON 序列化的值在**写入时**报错，不会
       留到恢复时爆雷；读从未写入且未声明的键 → ``KeyError``。
    7. **无 schema（P3-04 裁决）**：写闸门开后，**直接赋值即写入**
       ——不强制先 ``register_state``；声明只提供 default 与声明期
       冲突检测。recover 重放把全部持久化键**直接装袋**（无论是否
       声明过），不存在「未声明键」特例。

    .. rubric:: 测试案例

    - 写透：``agent.state["n"] = 1`` 后等待 drain（或显式 ``drain``）
      再模拟进程崩溃（丢弃内存、直接重放文件）→ 期望：恢复后
      ``agent.state["n"] == 1``；提交后**未 drain** 即崩溃 → 尾部
      修改允许丢失（崩溃窗口，P1-19）。
    - 显式视图读写：``agent.state.n += 1`` 与
      ``agent.state["n"] += 1`` 产生相同落盘行与读出值；写后
      ``watch("n")`` / watcher 通道照常触发（触发点在本类写路径）；
      ``agent.n = 1``（``n`` 已注册）→ 期望：抛 ``StateKeyError``。
    - 写闸门：create 管线 setup 中 ``agent.state.x = 1``
      → 期望：抛错；recover 管线
      ``_restore()`` 完成前写 → 期望：抛错；解锁前读 → 期望：仅见
      defaults。
    - 末行合并：同 key 连续 set 1000 次 → 期望：文件中该 key 仅
      1 行，值为末次写入；文件总行数不随次数增长。
    - 截尾：``set k`` 后紧跟 ``del k`` → 期望：文件中无 ``k`` 的行。
    - 崩溃撕裂：人为在文件尾部写入半行残字节 → 操作：恢复 → 期望：
      残行被截断丢弃，之前完整行的状态完整重建。
    - fail fast：``agent.state.x = object()`` → 期望：写入时立即抛
      ``TypeError``，文件不产生新行。
    - 未声明键：``agent.state["typo"]`` → ``KeyError``；
      ``agent.typo`` → ``AttributeError``。
    - 默认值不落盘：``register_state("n", 0)`` 后不做任何写入 →
      期望：``state.jsonl`` 无对应行；读出 ``agent.state.n == 0``。
    - recover 覆盖：崩溃前写入 ``n = 5``，声明 ``register_state("n", 0)``
      → 操作：恢复 → 期望：``agent.state.n == 5``（落盘值覆盖默认值）。

    .. rubric:: 调用关系（审计）

    - 调用：无（视图行为见各方法条目）
    - 被调：``flowing.agent.Agent.register_state()``（时机：setup 中
      逐键声明）、``flowing.agent.Agent.state`` property（时机：显式
      视图访问）、``flowing.agent.Agent.__setattr__`` /
      ``__delattr__``（时机：状态键防遮蔽检查，撞键抛
      ``StateKeyError``）、``flowing.runtime.Runtime.register_state()`` /
      ``Runtime.state(ns)``（Runtime 侧保留命名空间，返回同一个类；
      时机：未见规约）
    - 实例化方：``flowing.agent.Agent.__init__``（经 ``_open_stores()``，
      Agent 侧单袋，P3-03）与
      ``flowing.runtime.Runtime.register_state()``（Runtime 侧每命名
      空间一个实例，时机：全局命名空间声明时）；内嵌
      :class:`RecordStore` 由构造方一并建好传入

    .. seealso:: :meth:`Agent.register_state`、:attr:`Agent.state`、
        :meth:`Agent._restore`
    """

    _store: RecordStore
    """内嵌落盘后端（每袋一个文件：Agent 侧 ``state.jsonl``、Runtime
    侧 ``<namespace>.jsonl``，均 ``merge_last_line=True``）。
    内部 API，不属稳定契约。
    """
    _defaults: dict[str, Any]
    """``register_state`` 声明的键默认值表（不落盘，读出回退）。
    内部 API。
    """
    _persisted: dict[str, Any]
    """内存持久值表（写透时同步更新；``None`` 值与缺席需区分，
    以键在不在表中为准）。内部 API。
    """
    _write_gate_open: bool
    """写闸门（``False`` = 锁定：写抛错、读只见 defaults）；
    create 管线「初始 state 写盘」后 / recover 管线
    ``Agent._restore`` 完成后 / Runtime 引导重放后解锁。内部 API。
    """
    _compact_threshold: int
    """state.jsonl 全量压缩的行数阈值（默认 256，构造参数可调）。
    内部 API。
    """
    _lines_since_compact: int
    """自上次压缩以来的 append 行数计数（``__setitem__`` /
    ``__delitem__`` 各 +1；超 ``_compact_threshold`` 触发
    :meth:`_maybe_compact`，压缩请求发出后归零）。内部 API。
    """

    def __init__(
        self,
        store: RecordStore,
        defaults: dict[str, Any] | None = None,
        owner: Any | None = None,
        compact_threshold: int = 256,
    ) -> None:
        """绑定落盘后端与默认值表（**内部 API，不属稳定契约**）。

        构造即锁定写闸门（``_write_gate_open = False``）——解锁由管线
        完成点负责（见类 docstring 写闸门条）。构造点：Agent 侧在
        ``Agent.__init__``（经 ``_open_stores``，P3-03），Runtime 侧在
        ``Runtime.register_state``。``store`` 标注契约形态
        :class:`RecordStore`——本类只用五动词，不依赖文件后端内部策略
        （构造点给具体实现，换装点见模块 docstring「后端演进缝」）。

        :param owner: 属主 Agent（可选）。非 ``None`` 时 ``__setitem__``
          写透后 fire-and-forget 通知属主的 watcher 通道——
          ``watch`` 对状态键的触发点（P3-03 配套：写路径唯一化到
          ``agent.state.xxx`` 后，触发责任随写路径搬进本类）。
          Runtime 侧命名空间袋无属主（``None`` → 不 dispatch）。
        :param compact_threshold: state 全量压缩的行数阈值
          （默认 256，见 ``_compact_threshold``）。

        .. rubric:: 调用关系（审计）

        - 调用：无（纯赋值）
        - 被调：``flowing.agent.Agent.__init__``（经 ``_open_stores()``）、
          ``flowing.runtime.Runtime.register_state()``
        """
        # 内部字段绕过状态通道（__setattr__ 拦截一切属性写，
        # 下划线前缀的内部字段必须走 object.__setattr__，否则自举死循环）
        object.__setattr__(self, "_store", store)
        object.__setattr__(self, "_defaults", dict(defaults or {}))
        object.__setattr__(self, "_persisted", {})
        object.__setattr__(self, "_owner", owner)
        object.__setattr__(self, "_compact_threshold", compact_threshold)
        object.__setattr__(self, "_lines_since_compact", 0)
        # 构造即锁定写闸门；解锁归管线完成点
        object.__setattr__(self, "_write_gate_open", False)

    async def _close(self) -> None:
        """随属主收尾：转调内嵌 store 的 ``close()``（内部 API）。

        .. rubric:: 调用关系（审计）

        - 调用：:meth:`RecordStore.close`（时机：排空 + 停写任务）
        - 被调：``flowing.agent.Agent.destroy()``、
          ``flowing.runtime.Runtime.shutdown()``（时机：属主收尾）
        """
        await self._store.close()

    def _register(self, key: str, default: Any = None) -> None:
        """登记一个状态键及其默认值（**内部 API，不属稳定契约**）。

        声明期冲突检测的一部分：key 已在 ``_defaults`` 中 → 报错
        （重复声明）。撞属主类属性 / 核心保留键的判定不在本方法——
        那是属主侧 ``register_state`` 的职责（它持有类与核心键清单）。

        .. rubric:: 调用关系（审计）

        - 调用：无（写 ``_defaults`` 表；default 不落盘）
        - 被调：``flowing.agent.Agent.register_state()``（时机：
          setup 中逐键声明）；Runtime 侧逐键等价物是
          ``Runtime.register_state`` 的 ``defaults`` 表（构造时给）
        """
        if key in self._defaults:
            raise ValueError(f"duplicate state key: {key}")
        self._defaults[key] = default

    def _maybe_compact(self, *, force: bool = False) -> None:
        """state.jsonl 全量压缩的触发符号（内部 API；P3-13 裁决的承担
        主体）。

        .. rubric:: 功能介绍

        三时点的统一入口：① 恢复重放后（``Agent._restore`` 收尾、
        Runtime 引导重放后，``force=True``）；② 属主 ``destroy`` 收尾
        （``force=True``，在 ``_close`` 之前调用，请求随收尾排空一并
        执行）；③ append 行数超阈值（``__setitem__`` / ``__delitem__``
        内 ``_lines_since_compact >= _compact_threshold`` 时调用，
        ``force=False``）。

        .. rubric:: 行为规约

        - 终态计算：仅 ``_persisted`` 持久值（defaults 不落盘原则
          不变）逐键生成 ``{"op": "set", "key", "value"}`` 行，经
          ``_store.sync(terminal_records)`` 提交；请求发出后
          ``_lines_since_compact`` 归零。
        - 物理重写（原子性、排空前提）归后端 drain 任务——本方法只做
          决策与提交，热路径开销为一次计数比较。
        - 非行为：不 await、不阻塞写路径；sync 请求与后续 append
          不竞争（契约②结构性保证）。

        .. rubric:: 调用关系（审计）

        - 调用：``RecordStore.sync``（契约动词；时机：判定通过时）
        - 被调：``flowing.agent.Agent._restore()`` / ``destroy()``、
          ``flowing.runtime.Runtime`` 引导重放收尾 / ``shutdown()``、
          :meth:`__setitem__` / :meth:`__delitem__`（时机：三时点）
        """
        # force 或 _lines_since_compact >= _compact_threshold 时：
        # terminal_records = _persisted 逐键 {"op": "set", ...}
        # -> _store.compact(terminal_records)；_lines_since_compact = 0
        ...

    def __getattr__(self, key: str) -> Any:
        """属性式读（key 须为合法标识符；语义同 ``__getitem__``）。

        .. rubric:: 调用关系（审计）

        - 调用：无
        - 被调：无（运算符协议方法，由属性访问语法隐式触发）
        """
        return self[key]   # 语义同 __getitem__
    def __setattr__(self, key: str, value: Any) -> None:
        """属性式写（语义同 ``__setitem__``，受写闸门约束）。

        下划线前缀的内部字段（``_store`` 等）不经本通道——以
        ``object.__setattr__`` 直写（见 :meth:`__init__`）。

        .. rubric:: 调用关系（审计）

        - 调用：无
        - 被调：无（运算符协议方法；写透落盘时序见类 docstring 行为规约）
        """
        self[key] = value   # 语义同 __setitem__（受写闸门约束）
    def __delattr__(self, key: str) -> None:
        """属性式删（语义同 ``__delitem__``）。

        .. rubric:: 调用关系（审计）

        - 调用：无
        - 被调：无（运算符协议方法，由属性访问语法隐式触发）
        """
        del self[key]   # 语义同 __delitem__
    def __getitem__(self, key: str) -> Any:
        """读出：``持久值 ?? defaults[key]``；两者皆无 → ``KeyError``。

        .. rubric:: 调用关系（审计）

        - 调用：无
        - 被调：无（运算符协议方法；``get()`` 为其不抛错形式）
        """
        # 读：_persisted[key] ?? _defaults[key]；两者皆无 -> KeyError。
        # 内部字段访问一律走 object.__getattribute__（__getattr__ 拦截
        # 一切缺失属性，直接 self._persisted 在 __init__ 完成前会递归
        # 回本方法——自举死循环护栏）
        ...
    def __setitem__(self, key: str, value: Any) -> None:
        """写透落盘（见类 docstring 行为规约）。

        写透后若 ``_owner`` 非空，fire-and-forget 通知属主的
        watcher 通道（FieldUpdate 快照，old 取写前读值）——``watch``
        对状态键的触发点（P3-03 配套裁决）。``del`` 不触发（删除不是
        赋值事件，与 ``Agent.__delattr__`` 同律）。

        .. rubric:: 调用关系（审计）

        - 调用：:meth:`RecordStore.submit`（时机：每次写透，同步排队；
          末行合并等物理策略在 store 内部）；属主
          ``hooks._notify_watch``（时机：写透后，
          fire-and-forget 不 await）
        - 被调：无（运算符协议方法；写闸门约束见类 docstring）
        """
        # 写闸门检查（_write_gate_open 未开 -> 抛错）-> JSON 可序列化校验
        # （fail fast）-> _persisted[key] = value -> _store.submit(
        # {"op": "set", "key", "value"})（同步排队即返；末行合并等物理
        # 策略在 FileRecordStore 内部）-> _lines_since_compact += 1
        # -> _maybe_compact()（阈值判定，超阈即提交压缩请求）
        # -> owner 非空且 owner.hooks 已建立（__dict__.get 护栏）时：
        #    构造 FieldUpdate(name=key, old=写前读值, new=value)，局部导入
        #    flowing.agent.FieldUpdate 破环，调用 owner.hooks._notify_watch(
        #    owner, fu)（无运行中 loop -> 静默跳过，写照常）
        ...
    def __delitem__(self, key: str) -> None:
        """删除持久值；之后读回退到 default（若有）。

        .. rubric:: 调用关系（审计）

        - 调用：无
        - 被调：无（运算符协议方法，由 ``del`` 语句隐式触发）
        """
        # 写闸门检查 -> _persisted.pop(key, None) -> _store.submit(
        # {"op": "delete", "key"})（末行 set k 紧跟 delete k 时的截尾
        # 优化在 FileRecordStore 内部）-> _lines_since_compact += 1
        # -> _maybe_compact()；之后读回退到 _defaults（若有）
        ...
    def __contains__(self, key: str) -> bool:
        """key 有持久值或注册默认值即视为存在。

        .. rubric:: 调用关系（审计）

        - 调用：无
        - 被调：``flowing.agent.Agent.__setattr__`` /
          ``__delattr__``（时机：状态键防遮蔽检查，每次公开名
          赋值/删除）；运算符协议方法，由 ``in`` 表达式隐式触发
        """
        try:
            self[key]   # 持久值或注册 default 命中即存在
        except KeyError:
            return False
        return True
    def get(self, key: str, default: Any = None) -> Any:
        """``__getitem__`` 的不抛错形式（注意与注册默认值叠加：
        注册 default 优先于本参数的 ``default``）。

        .. rubric:: 调用关系（审计）

        - 调用：无
        - 被调：无（公共便捷方法，框架内无调用方）
        """
        try:
            return self[key]   # __getitem__ 的不抛错形式（注册 default 优先）
        except KeyError:
            return default
