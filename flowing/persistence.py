"""``flowing.persistence`` —— 持久化机制层：记录落盘、重放与状态袋视图。

.. rubric:: 功能介绍

本模块承载 Flowing 全部「记录落盘」机制，三个成员：

- :class:`RecordStore` —— 持久化后端契约（五个动词：``submit`` /
  ``replay`` / ``drain`` / ``close`` / ``sync``）。类型层 Protocol，
  零运行成本；未来接入非文件后端（数据库等）时实现同一 Protocol 即可。
- :class:`FileRecordStore` —— 文件系实现：一行一条 JSON 记录（jsonl），
  write-behind 落盘（提交同步排队、写入由唯一后台任务异步执行）。
- :class:`StateView` —— 状态袋视图：属性式 / 字典式访问，写透到落盘
  后端；经 ``flowing.agent.Agent.state`` （默认袋）、
  ``flowing.agent.Agent.register_state`` （命名袋）与
  ``flowing.runtime.Runtime.register_state`` / ``Runtime.states``
  （全局命名空间）暴露。

服务的文件：每 Agent session 目录的 ``tree.jsonl`` （消息行 + 变更记录
行）与 ``state.jsonl`` （状态 set/delete 行）；Runtime 持久化根的
``<namespace>.jsonl`` （全局命名空间状态）。一个文件一个
``FileRecordStore`` 实例，实例归属该文件的属主（Agent / Runtime）。

文件格式契约（jsonl）：每个文件第一行恒为元数据记录
``{"type": "meta", "format_version": <int>}``，之后每行一条 JSON 记录。
``tree.jsonl`` 的消息行与变更记录行格式由 :mod:`flowing.message`
（``Message.to_record`` / ``MessageChain`` 五 op）定义；``state.jsonl``
与 ``<namespace>.jsonl`` 的状态行格式由 :class:`StateView` 定义
（``{"op": "set", "key": ..., "value": ...}`` / ``{"op": "delete", "key": ...}``）。

.. rubric:: 使用示例

.. code-block:: python

    # 常规使用：状态读写经 Agent / Runtime 的视图入口（见 StateView）
    agent.state.register("tracker_count", 0)     # 登记键（缺省即写落盘）
    agent.state.tracker_count += 1               # 写透到 state.jsonl

    # 直接使用 FileRecordStore 属框架集成面（消息 / 状态的落盘载体）
    store = FileRecordStore(session_dir / "tree.jsonl")
    store.submit({"op": "set", "key": "n", "value": 1})
    await store.close()                          # 收尾：先写完未写盘内容

.. rubric:: 行为要点

- 提交返回不代表已写盘（write-behind）：``submit`` 只把记录排入内存
  队列并立即返回；真正写入文件由唯一的后台任务异步执行。正常关闭
  （``close()``）时会先把未写盘的内容写完再结束。崩溃窗口 = 已提交
  未写入的尾部记录（正常为毫秒级）；窗口内丢失不破坏一致性——内存是
  权威，崩溃后权威同灭，重放恢复到的状态自洽（如同丢失的操作从未
  发生）。
- 排空屏障钉死在两个位置：属主收尾（Agent ``destroy`` / Runtime
  ``shutdown``）前 ``close()`` （内含排空）；任何整文件重写（``sync``
  请求、压缩）之前——压缩统一由后台任务在队列见底处执行，天然满足
  此前提。
- poison = fail fast：写入过程中首次落盘失败（磁盘满 / 权限 / 序列化
  错误）后，store 进入 poison 态并记住该异常；此后任何 ``submit``
  同步重抛同一异常——沉默接受会造成内存与文件永久分叉且无人知晓，
  必须让错误在操作现场爆出。
- 版本与迁移：文件版本号是文件级而非记录级。重放时版本低于当前 →
  经迁移链逐段升级到当前格式（未登记迁移函数的段，记录原样通过）；
  版本高于当前 → 抛 :class:`flowing.errors.FormatVersionError`
  （文件比框架新，静默读是数据风险）。迁移完成后文件借 ``sync``
  原子重写为新版本（一次性动作）。无版本首行的存量文件按版本 0
  处理，走同一迁移链。
- 容错：撕裂末行（崩溃半截写产物，无换行结尾）截断丢弃；中间行 JSON
  损坏抛 :class:`flowing.errors.CorruptionError` （数据事故，报警不
  容忍）。
- 维护对调用方透明：末行合并（同 key 连续写就地重写末行，文件大小
  不随写入次数增长）、墓碑压缩（``tree.jsonl`` 的删除标记行
  （tombstone）累积到阈值后整文件重写）、状态全量压缩，均不改变任何
  逻辑内容——重放结果永远等于「全部操作的顺序重放结果」。
- 恢复 = 重放重建：进程重启后内存为空，文件是唯一事实来源——重放
  记录序列即可重建出与崩溃前一致的对象状态（消息树经 ``tree.jsonl``、
  状态经 ``state.jsonl`` / ``<namespace>.jsonl``）。TurnContext 不落盘，
  恢复时不存在「半截回合」。
- 跨实例无顺序承诺：不同文件（跨 Agent / 跨命名空间）之间没有写入
  顺序承诺；同一文件内的记录按提交顺序落盘。

.. seealso::

    :class:`flowing.agent.Agent` —— tree/state 两 store 的属主。
    :class:`flowing.runtime.Runtime` —— 全局命名空间 store 的属主。
    :class:`flowing.message.MessageChain` —— 变更记录行（tombstone /
    update / move / 邻接调整）的产生者。
    :mod:`flowing.errors` —— ``FormatVersionError`` / ``CorruptionError``。
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
"""当前 jsonl 行格式版本号。

每个持久化文件的首行恒为 ``{"type": "meta", "format_version": FORMAT_VERSION}``
（新建 / 整文件重写时写入）；无版本首行的存量文件按版本 0 处理。行格式
变更 = 本常量 +1，并在 :data:`MIGRATIONS` 登记一段相邻版本迁移函数。
"""

MIGRATIONS: dict[int, "Any"] = {}
"""迁移链：``MIGRATIONS[v]`` = v → v+1 的相邻版本迁移函数（输入旧版本
记录序列，输出新版本记录序列）；``replay`` 从文件版本起逐段串联升级至
:data:`FORMAT_VERSION`。当前为空表（v1 即现状，无待迁移版本）。
"""


class RecordStore(Protocol):
    """持久化后端契约（五动词）——类型层 Protocol，零运行成本。

    .. rubric:: 功能介绍

    任何后端（本模块的 :class:`FileRecordStore`，或未来的数据库后端）兑现
    这五个动词即可替换接入：``submit`` （同步提交一条记录）、``replay``
    （实例方法，按写入序逐条吐记录）、``drain`` （排空屏障）、``close``
    （排空 + 停写任务）、``sync`` （请求整文件原子重写为给定终态记录）。
    契约把「Agent / Runtime 依赖后端的面」显性化：两侧对后端的全部依赖
    就这五个方法 + 「记录是 dict」的约定。物理布局（append / 末行合并 /
    压缩机制）是各后端的实现细节，不进本契约——但各动词的语义承诺（如
    ``sync`` 的原子性）各后端必须兑现。

    .. rubric:: 行为要点

    - 兑现模块 docstring 的持久化契约（排队语义 / 两个排空屏障点 /
      poison 重抛）。
    - ``replay`` 按写入顺序产出；遇撕裂末行（尾部不完整 JSON 行）截断
      丢弃、不报错。首行 ``{"type": "meta", "format_version": ...}``
      元数据记录不产出给调用方——版本判读与低版本迁移在 ``replay`` 内部
      完成，调用方拿到的永远是当前版本的记录序列。
    - ``sync`` 语义承诺：重写完成后存储内容 = 给定终态记录序列；过程原子
      （任何时刻观察者只见旧内容或新内容，无半重写状态）；生效时机同排队
      语义（请求排队，后台任务队列见底后执行）。

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

        终态记录由调用方从其内存权威导出传入（如 ``StateView`` 从
        ``_persisted`` 导出），不是后端对文件重放结果的归约——文件可能有已
        提交未写入的尾部记录，直接重放会得到旧值。真正的「重放 → 归约 →
        写回」式压缩是文件后端的墓碑压缩（store 自主触发），与本动词分工
        明确。

        排队语义同 ``submit`` （请求入队即返，后台任务在队列见底后执行）；
        语义承诺原子——任何时刻观察者只见旧内容或新内容，无半同步状态。
        后端不解释记录内容。
        """
        ...


class FileRecordStore:
    """文件系记录存储：一行一条 JSON 记录，write-behind 落盘。

    :class:`RecordStore` （契约 Protocol）的文件系实现，也是当前唯一实现。
    一个实例管一个文件。写侧：``submit`` 同步入队（FIFO），唯一的后台
    任务串行取出并 append；读侧：``replay`` 按序重放（撕裂末行丢弃）；
    收尾：``close`` = 排空 + 停写任务；维护：``sync`` 请求入队，后台任务
    在队列见底后执行原子重写。

    常规使用不直接接触本类：Agent / Runtime 侧的状态读写经
    :class:`StateView` （``agent.state`` / ``agent.register_state`` /
    ``runtime.register_state``），消息落盘由 ``flowing.agent.Agent`` 内部
    完成。直接使用本类属框架集成面（构造点：Agent 的 ``__init__`` 经
    ``_open_stores``，Runtime 的 ``register_state``）。

    .. rubric:: 使用示例

    .. code-block:: python

        store = FileRecordStore(session_dir / "tree.jsonl")
        store.submit({"op": "set", "key": "n", "value": 1})
        store.submit({"op": "delete", "key": "n"})
        await store.close()                 # 收尾：先写完未写盘内容

        # 重放（新实例；撕裂末行自动丢弃，meta 首行不产出）
        store2 = FileRecordStore(session_dir / "tree.jsonl")
        for record in store2.replay():
            print(record)

    .. rubric:: 行为要点

    - FIFO：同一实例的记录按 ``submit`` 调用顺序写入文件。
    - write-behind：``submit`` 返回不代表已写盘；``close()`` 会先把未写盘
      的内容写完再结束。属主收尾必须走 ``close()``，否则尾部记录静默丢失。
    - 排空屏障：``drain()`` 返回时，此前提交的记录已全部落盘。
    - poison：写入过程中首次落盘异常后，``submit`` 同步重抛同一异常；
      store 不再接受新记录。
    - replay 容错：撕裂末行截断丢弃；中间行损坏抛
      :class:`flowing.errors.CorruptionError` （数据事故，报警不容忍）。
    - 版本处理：首行 ``meta`` 记录不产出；版本低于当前 → 经 ``MIGRATIONS``
      迁移链升级后产出，并借 ``sync`` 原子回写为新版本（一次性）；版本
      高于当前 → 抛 :class:`flowing.errors.FormatVersionError`。
    - 末行合并（``merge_last_line=True`` 时）：同 key 连续 ``set`` 就地
      重写末行，末行 ``set k`` 紧跟 ``delete k`` 截尾——文件大小不随写入
      次数增长，逻辑内容不变。
    - 墓碑压缩（``tombstone_threshold``）：文件里删除标记行（tombstone）
      计数达到阈值（默认 256）后，后台任务在队列见底处整文件原子重写，
      物理清除墓碑行与被标记删除的消息行，重放结果不变。
    - 压缩与重写的排空前提：墓碑压缩与 ``sync`` 请求的全量重写只在后台
      任务内、队列见底后执行，不可能与在队记录竞争。
    - 非行为：不做跨实例排序（跨 Agent / 跨命名空间无顺序承诺）；热路径
      （``submit`` / append）不做 fsync 策略承诺（机器级掉电耐受为演进位；
      ``sync`` 路径的文件 fsync 是原子性前提，不在此列）。

    :param path: 目标文件路径（父目录由属主管线保证存在）。
    :param merge_last_line: 同 key 连续写时就地重写末行（state.jsonl 开、
        tree.jsonl 关）。
    :param tombstone_threshold: 墓碑计数触发压缩的阈值（默认 256；仅
        tree.jsonl 有意义）。

    .. seealso:: :class:`RecordStore` （后端契约 Protocol）、
        :class:`StateView` （状态袋视图，经契约形态内嵌本类）。
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
        """绑定文件并准备落盘后端。

        构造点即后端换装点：Agent 侧在 ``Agent.__init__`` （经 ``_open_stores``），
        Runtime 侧在 ``Runtime.register_state``。写任务惰性启动于首个
        ``submit`` / ``sync`` / ``drain`` 调用——因此首次提交须在运行中的事件
        循环内。

        :param path: 目标文件路径（父目录由属主管线保证存在）。
        :param merge_last_line: 同 key 连续写时就地重写末行（state.jsonl 开、
            tree.jsonl 关）。
        :param tombstone_threshold: 墓碑计数触发压缩的阈值（默认 256；仅
            tree.jsonl 有意义）。
        """
        self._path = Path(path)
        self._merge_last_line = merge_last_line
        self._tombstone_threshold = tombstone_threshold
        self._poisoned: BaseException | None = None
        # FIFO 内存队列 + 唯一 drain 任务（write-behind；单写入者原则）
        # 队列项形态：("record", dict) / ("sync", list[dict]) / ("barrier", Event)
        self._queue: deque[tuple] = deque()
        # 写任务惰性启动于首个 submit / sync / drain 调用——Agent 创建期
        # 不做 await（写任务需要运行中的事件循环）；约束「首次提交须在
        # 运行中的事件循环内」。
        self._drain_task: asyncio.Task | None = None
        self._wakeup: asyncio.Event | None = None
        self._closed = False
        # 写侧状态（drain 任务私有，单写入者无需锁）
        self._file = None            # 二进制句柄（r+b / w+b），惰性打开
        self._write_offset = 0       # 当前文件末尾字节偏移
        self._last_line: tuple[int, str, str] | None = None  # 末行合并跟踪（offset, op, key）
        # 末行合并的截尾安全网：仅当本 session 已知文件从空开始、且被截尾的
        # set 是该 key 在文件中的唯一一行时，delete 才随之省略（否则截尾会
        # 复活更早的同名 set 行）。当前实现仅对「新建文件上的连续同 key
        # 序列」做截尾省行，跨交错序列一律保守补写 delete 行。
        self._fresh_start = False
        self._key_line_counts: dict[str, int] = {}
        self._tombstone_count = 0    # 墓碑计数（append 时 +1；replay 初始化加载时累计）
        # 打开存储时清扫孤儿 .tmp（原子重写统一规约）
        tmp = Path(str(self._path) + ".tmp")
        if tmp.exists():
            tmp.unlink()
    def submit(self, record: dict) -> None:
        """同步提交一条记录：检查未关闭与 poison 态后入队，立即返回。

        返回不代表已写盘（write-behind）；store 处于 poison 态时同步重抛首次
        落盘异常。记录必须 JSON 可序列化——校验在后台任务写入时执行，不可
        序列化即 poison 来源之一。

        :param record: 要落盘的记录（dict，必须 JSON 可序列化）。

        :raises RuntimeError: 已 ``close()`` 后仍调用。
        """
        if self._closed:
            raise RuntimeError(f"record store already closed: {self._path}")
        if self._poisoned is not None:
            raise self._poisoned  # poison 态同步重抛首次落盘异常（fail fast）
        self._queue.append(("record", record))
        self._ensure_drain_task()
        self._wakeup.set()
    def replay(self) -> Iterator[dict]:
        """按写入序逐条产出记录；撕裂末行截断丢弃，中间行损坏报警。

        实例方法（非 classmethod）：定位信息在构造时给出——这是非文件后端的
        演进缝（数据库后端无路径概念）。只读；低版本文件的迁移后回写是例外
        （借 ``sync`` 通道完成）。

        版本处理：首行 ``{"type": "meta", "format_version": ...}`` 不产出给
        调用方；版本低于当前 → 经 ``MIGRATIONS`` 迁移链逐段升级到当前版本后
        产出，并借 ``sync`` 把文件原子回写为新版本（一次性动作）；版本高于
        当前 → 抛 :class:`flowing.errors.FormatVersionError`；无版本首行的存量
        文件按版本 0 走同一迁移链。在无运行中事件循环的上下文消费本生成器时，
        迁移回写跳过（记录照常产出、下次 replay 重试），框架记一条警告日志。

        :return: 按写入顺序产出记录的迭代器（meta 首行不产出）。

        :raises flowing.errors.FormatVersionError: 文件版本高于当前支持版本。
        :raises flowing.errors.CorruptionError: 中间行 JSON 损坏。
        """
        records, file_version = self._read_records_with_version()
        if file_version > FORMAT_VERSION:
            raise FormatVersionError(self._path, file_version, FORMAT_VERSION)
        if file_version < FORMAT_VERSION:
            for v in range(file_version, FORMAT_VERSION):
                migrate = MIGRATIONS.get(v)
                if migrate is not None:
                    records = list(migrate(records))
                # 缺段 = 记录原样通过（v0 与 v1 行格式相同，v1 仅新增
                # meta 首行约定，无待迁移版本，不做特判、不虚构迁移函数）
            # 迁移后借 sync 通道原子回写为新版本（含新 meta 首行；
            # 一次性动作）。约束：须在运行中的事件循环内消费完本生成器
            # （sync 经队列提交，写任务惰性启动）；无 loop 时跳过回写仅
            # 告警——记录照常产出，下次 replay 重试迁移。
            try:
                self.sync(records)
            except RuntimeError:
                logger.warning(
                    "migration rewrite of %s deferred: no running event loop",
                    self._path,
                )
        # 墓碑计数在初始化加载（replay）时一并累计
        self._tombstone_count += sum(
            1 for r in records if r.get("type") == "tombstone"
        )
        yield from records
    async def drain(self) -> None:
        """排空屏障：返回时此前提交的记录已全部落盘。

        两个钉死调用点：属主收尾（经 ``close``）与整文件重写类维护之前。压缩
        由后台任务内部触发，无需外部调用本方法。poison 态下「返回即全部落盘」
        的承诺已被破坏——此时同步重抛首次落盘异常（不静默返回）。
        """
        if self._drain_task is None and not self._queue:
            # 从未提交（写任务惰性未启动）：无在队记录，写任务私有状态
            # 不可能被并发触碰，直接在本调用点做压缩检查——覆盖
            # 「replay 初始化加载累计墓碑后随即 drain / close」的路径
            self._maybe_compact_tombstones()
            return
        self._ensure_drain_task()
        if self._drain_task.done():
            # drain 任务已终结（poison / close），barrier 不会有人消费。
            # poison 态下「返回时此前记录已全部落盘」的承诺已被违反——
            # 同步重抛首次落盘异常（与 submit 同律，不静默返回）
            if self._poisoned is not None:
                raise self._poisoned
            return
        barrier = asyncio.Event()
        self._queue.append(("barrier", barrier))
        self._wakeup.set()
        await barrier.wait()
    async def close(self) -> None:
        """排空并停止写任务；幂等（重复调用安全）。

        正常关闭先把未写盘的内容写完（排空），再停止后台写任务。poison 态下
        不重复抛出已暴露过的首次落盘异常（收尾职责是清理，错误已在
        ``submit`` / ``drain`` 处爆出）。
        """
        if self._closed:
            return  # 幂等
        self._closed = True
        if self._drain_task is None:
            # 从未启动（无提交），无任务可停；但 replay 可能已累计墓碑
            # （最终销毁关闭时执行压缩）——此刻无写入者，直接检查
            self._maybe_compact_tombstones()
            return
        # 排空屏障（barrier 处含墓碑压缩检查，收尾时点）；
        # poison 态下 drain 重抛首次落盘异常——该异常已在 submit / drain
        # 暴露过，close 的职责是收尾清理，不再重复抛出
        try:
            await self.drain()
        except Exception as exc:
            if exc is not self._poisoned:
                raise
        self._drain_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._drain_task
    def sync(self, records: list[dict]) -> None:
        """契约动词实现：请求将本文件全量原子同步为给定终态记录。

        poison 检查后把维护请求（含终态记录）入队即返；后台任务在队列见底后
        取出执行原子重写。终态记录由调用方从其内存权威导出传入（如
        :class:`StateView` 从 ``_persisted`` 导出——非重放文件归约，见
        ``RecordStore.sync``），本类不解释记录内容。重写产物永远是当前版本
        格式（首行 ``meta`` 版本记录原样保留在最前）。

        :param records: 终态记录序列（不含 meta 首行；重写时自动写当前版本
            meta 首行）。

        :raises RuntimeError: 已 ``close()`` 后仍调用。
        """
        if self._closed:
            raise RuntimeError(f"record store already closed: {self._path}")
        if self._poisoned is not None:
            raise self._poisoned  # poison 检查同 submit（同步重抛首次落盘异常）
        # 维护标记入队；FIFO 序保证执行时此前记录均已落盘（排空前提的
        # 结构性兑现），drain 任务处理到本标记即执行原子重写
        self._queue.append(("sync", list(records)))
        self._ensure_drain_task()
        self._wakeup.set()

    # ------------------------------------------------------------------
    # 以下为内部实现（单写入者：写侧状态仅由 drain 任务触碰）
    # ------------------------------------------------------------------

    def _ensure_drain_task(self) -> None:
        """惰性启动唯一写任务（需在运行中的事件循环内调用）。"""
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
                    # 队列见底：墓碑压缩的自主触发点（只按计数触发）
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
            # poison = fail fast：记住首次落盘异常，唤醒全部排队 barrier
            # （drain 等待方不挂死），任务终结；此后 submit 同步重抛。
            # 异常不在任务内再抛（避免 unretrieved 任务告警）。
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
        logging.error 报警 + CorruptionError（数据事故，不容忍）。
        """
        if not self._path.exists():
            return [], FORMAT_VERSION  # 文件不存在 = 新建，视为当前版本
        raw = self._path.read_bytes()
        if not raw:
            return [], FORMAT_VERSION  # 空文件同上
        segments = raw.split(b"\n")
        segments.pop()  # 末段：文件以 \n 结尾时为 b""；否则即撕裂末行，丢弃
        version = 0  # 无 meta 首行的存量文件按版本 0 处理
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
                # 同 key 连续 set → 就地重写末行（seek + truncate 机制，
                # POSIX 语义。跨平台文件锁 / 偏移语义若出问题，演进方向
                # 是退化为 sync 式整文件重写——逻辑契约「透明性」不变）
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
        """原子重写统一规约：同目录 ``.tmp`` → fsync 临时文件 → ``os.replace``。

        调用方传入的终态记录不含 meta；本函数自动写当前版本 meta 首行再写
        records。重写后旧句柄指向旧 inode——关闭并惰性重开；末行合并跟踪随
        文件内容变化失效，重置为已知新起点。内部 API。
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

        只按计数触发；「Agent 空闲」条件由上层触发时机结构性保证，store 不
        感知 Agent。内部 API。
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
    """状态袋视图——写透持久化：读写都直接作用于落盘存储。

    .. rubric:: 功能介绍

    状态袋（state bag）是「该智能体 / 该命名空间的持久化状态」的视图：
    每个状态键就是一个特殊属性——读写经本视图走持久化通道（写透到对应的
    jsonl 文件）。Agent 侧经 ``agent.state`` （默认袋，``state.jsonl``）与
    ``agent.register_state(name)`` （命名袋，``<name>.jsonl``）暴露；Runtime
    侧经 ``runtime.register_state(namespace)`` / ``runtime.states``
    （``<namespace>.jsonl``）暴露。本类契约对两侧一致适用。

    访问形态（两种等价）::

        agent.state.count += 1        # 属性式（key 须为合法标识符）
        agent.state["weird-key"] = 1  # 字典式（任意字符串 key）
        del agent.state.jobs          # 删除持久值

    .. rubric:: 使用示例

    .. code-block:: python

        class MyAgent(Agent):
            async def setup(self, **args):
                self.state.register("tracker_count", 0)   # 登记键：缺省即写落盘

        # 钩子 / 工具代码中：
        agent.state.tracker_count += 1    # 写透落盘；不触发 watcher
        print(agent.state["tracker_count"])

    .. rubric:: 行为要点

    - 写透：set / delete 经后端 ``submit`` 同步提交、排队即返回——返回不
      代表已落盘（见模块 docstring「write-behind」）。值必须 JSON 可序列
      化，否则写入时立即报错（fail-fast，不留到恢复时爆雷）。
    - 无写闸门：写透不设管线时序拦截——``setup()`` 中写状态合法，且不被
      恢复重放覆盖（恢复管线重放先于 ``setup()`` 完成）。
    - 键登记（``register``）：``register(key, default)`` 幂等——键未持久化
      时把 ``default`` 写落盘并返回 ``default``；键已持久化时跳过、返回
      当前持久值。首次登记把默认值写落盘，之后该键的值以持久值为准，不再
      随代码里默认参数的变化而变化。
    - 无 schema：直接赋值即写入——不强制先 ``register``。恢复重放把全部
      持久化键直接装袋（无论是否登记过），不存在「未登记键」特例。
    - 崩溃容忍：崩溃窗口 = 已提交未写入的尾部记录（正常为毫秒级）；窗口
      内丢失至多对应最后一次修改，语义等价于断电。文件尾部残行（就地
      重写的撕裂产物）重放时截断丢弃。
    - 恢复一致性：重放后视图的逻辑内容与崩溃前一致（同键后写的值覆盖先写
      的值，delete 删除该键）；唯一例外是崩溃点尾部残行对应的至多最后一
      次修改。
    - 顺序性：同一视图的写操作按调用顺序生效；单线程语义，跨视图（跨
      Agent / 跨命名空间）无顺序承诺。
    - 维护透明：末行合并与压缩不改变任何逻辑内容——视图观察到的字典永远
      等于「全部操作的顺序重放结果」。
    - 自动压缩：追加行数达到阈值（默认 256）或属主收尾 / 恢复重放后，视图
      把当前全部持久值经 ``sync`` 提交整文件原子重写；逻辑内容不变。
    - fail fast：不可 JSON 序列化的值在写入时报错；读从未写入且未
      register 的键 → ``KeyError`` （字典式）/ ``AttributeError`` （属性式）。
    - watch 不察觉 state：状态写透不触发 watcher 通道——watcher 只管普通
      实例属性（``Agent.__setattr__`` 通道）。
    - 建议：只放恢复真正需要的状态；高频遥测数据属插件内存。插件键名按
      约定带插件名前缀（如 cron 插件的 ``cron_jobs``）；框架核心键在
      core 袋（框架私有，不对外暴露）。

    .. seealso::

        :attr:`flowing.agent.Agent.state`
        :meth:`flowing.agent.Agent.register_state`
        :meth:`flowing.runtime.Runtime.register_state`
        :class:`RecordStore` / :class:`FileRecordStore`
    """

    _store: RecordStore
    """内嵌落盘后端（每袋一个文件：Agent 侧 ``state.jsonl`` /
    ``<name>.jsonl``、Runtime 侧 ``<namespace>.jsonl``，均
    ``merge_last_line=True``）。内部 API，不属稳定契约。"""
    _persisted: dict[str, Any]
    """内存持久值表（写透与恢复重放时同步更新；以键在不在表中为准，
    ``None`` 值与缺席需区分）。唯一真值，无独立 defaults 表。内部 API，
    不属稳定契约。"""
    _compact_threshold: int
    """自动压缩的行数阈值（默认 256，构造参数可调）。内部 API。"""
    _lines_since_compact: int
    """自上次压缩以来的追加行数计数（``__setitem__`` / ``__delitem__`` 各
    +1；超阈值触发压缩请求，请求发出后归零）。内部 API。"""

    def __init__(
        self,
        store: RecordStore,
        compact_threshold: int = 256,
    ) -> None:
        """绑定落盘后端（内部 API，不属稳定契约）。

        构造点：Agent 侧在 ``Agent.__init__`` （经 ``_open_stores``），Runtime
        侧在 ``Runtime.register_state``。``store`` 标注契约形态
        :class:`RecordStore`——本类只用五动词，不依赖文件后端内部策略。

        :param store: 落盘后端（契约形态 ``RecordStore``）。
        :param compact_threshold: 自动压缩的行数阈值（默认 256）。
        """
        # 内部字段绕过状态通道（__setattr__ 拦截一切属性写，
        # 下划线前缀的内部字段必须走 object.__setattr__，否则自举死循环）
        object.__setattr__(self, "_store", store)
        object.__setattr__(self, "_persisted", {})
        object.__setattr__(self, "_compact_threshold", compact_threshold)
        object.__setattr__(self, "_lines_since_compact", 0)

    async def _close(self) -> None:
        """随属主收尾：转调内嵌 store 的 ``close()`` （内部 API）。"""
        await self._store.close()

    def register(self, key: str, default: Any = None) -> Any:
        """登记一个状态键并返回当前真正存在的值。

        缺省即写：键未持久化时把 ``default`` 写落盘并返回 ``default``；键已
        持久化时跳过（幂等，重复登记无副作用、不报错）并返回当前持久值。
        首次登记把默认值写落盘，之后该键的值以持久值为准，不再随代码里默认
        参数的变化而变化。

        :param key: 状态键名。
        :param default: 键未持久化时的初值（缺省 ``None``）。
        :return: 当前真正存在的值（已持久值，或本次写入的 ``default``）。
        """
        persisted = object.__getattribute__(self, "_persisted")
        if key in persisted:
            return persisted[key]   # 已持久：幂等跳过，返回真值
        self[key] = default   # 缺省即写：default 落盘（写透 + JSON 校验）
        return default

    def _maybe_compact(self, *, force: bool = False) -> None:
        """自动压缩触发符号（内部 API）：提交整文件重写请求。

        三时点：恢复重放后 / 属主 ``destroy`` 收尾（均 ``force=True``）/
        追加行数超阈值（``force=False``）。终态 = 当前全部持久值逐键生成的
        ``{"op": "set", "key", "value"}`` 行，经 ``_store.sync`` 提交；物理
        重写归后端后台任务。热路径开销为一次计数比较。
        """
        if not force and self._lines_since_compact < self._compact_threshold:
            return  # 未达阈值且非 force：热路径开销仅一次计数比较
        # 终态计算：仅 _persisted 持久值（无独立 defaults 表）
        terminal_records = [
            {"op": "set", "key": k, "value": v} for k, v in self._persisted.items()
        ]
        # 经 sync 动词提交（RecordStore 五动词契约）；物理重写归后端
        # drain 任务
        self._store.sync(terminal_records)
        object.__setattr__(self, "_lines_since_compact", 0)

    def __getattr__(self, key: str) -> Any:
        """属性式读（key 须为合法标识符）。

        ``self[key]`` 抛 ``KeyError`` 时改抛 ``AttributeError`` （与
        ``agent.xxx`` 的属性访问语义对齐，内省安全）。
        """
        try:
            return self[key]
        except KeyError as exc:
            raise AttributeError(key) from None   # 属性式读未注册键（与 agent.xxx 属性访问语义对齐）
    def __setattr__(self, key: str, value: Any) -> None:
        """属性式写（语义同 ``__setitem__``）。

        下划线前缀的内部字段（``_store`` 等）不经本通道——以
        ``object.__setattr__`` 直写（见 :meth:`__init__`）。
        """
        self[key] = value   # 语义同 __setitem__
    def __delattr__(self, key: str) -> None:
        """属性式删（语义同 ``__delitem__``）。"""
        del self[key]   # 语义同 __delitem__
    def __getitem__(self, key: str) -> Any:
        """读出：返回 ``key`` 的当前持久值；未持久化（未写且未 register）→
        ``KeyError``。"""
        # 读：仅 _persisted（无独立 defaults 表——register 缺省即写）。
        # 内部字段访问一律走 object.__getattribute__（__getattr__ 拦截
        # 一切缺失属性，直接 self._persisted 在 __init__ 完成前会递归
        # 回本方法——自举死循环护栏）
        persisted = object.__getattribute__(self, "_persisted")
        if key in persisted:
            return persisted[key]
        raise KeyError(key)
    def __setitem__(self, key: str, value: Any) -> None:
        """写透落盘：JSON 可序列化校验 → 更新内存持久值 → 同步排队写盘。

        watch 不察觉 state：状态写透不触发 watcher 通道。
        """
        json.dumps(value)  # JSON 可序列化校验 fail fast，原样抛 TypeError
        self._persisted[key] = value
        # 同步排队即返；末行合并等物理策略在 FileRecordStore 内部
        self._store.submit({"op": "set", "key": key, "value": value})
        object.__setattr__(self, "_lines_since_compact", self._lines_since_compact + 1)
        self._maybe_compact()  # 阈值判定，超阈即提交压缩请求
    def __delitem__(self, key: str) -> None:
        """删除持久值；之后读该键 → ``KeyError`` （register 的初值也被删——
        无 defaults 回退）。"""
        self._persisted.pop(key, None)
        # 末行 set k 紧跟 delete k 时的截尾优化在 FileRecordStore 内部
        self._store.submit({"op": "delete", "key": key})
        object.__setattr__(self, "_lines_since_compact", self._lines_since_compact + 1)
        self._maybe_compact()
        # 之后读未持久化键 → KeyError（register 的初值也被删——无 defaults 回退）
    def __contains__(self, key: str) -> bool:
        """key 有持久值即视为存在。"""
        try:
            self[key]   # 持久值命中即存在
        except KeyError:
            return False
        return True
    def get(self, key: str, default: Any = None) -> Any:
        """``__getitem__`` 的不抛错形式：键无持久值时返回 ``default``。"""
        try:
            return self[key]   # __getitem__ 的不抛错形式（注册 default 优先）
        except KeyError:
            return default
