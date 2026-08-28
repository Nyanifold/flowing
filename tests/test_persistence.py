"""persistence 模块测试（简报测试清单 S1–S25）。

S1–S15 覆盖 FileRecordStore；S16–S25 覆盖 StateView（stub RecordStore /
stub owner 驱动，阶段边界：不接真 Agent / hooks）。
"""

import asyncio
import json
import logging
from pathlib import Path

import pytest

from flowing.errors import CorruptionError, FormatVersionError
from flowing.persistence import (
    FORMAT_VERSION,
    MIGRATIONS,
    FileRecordStore,
    RecordStore,
    StateView,
)

# ---------------------------------------------------------------------------
# FileRecordStore（S1–S15）
# ---------------------------------------------------------------------------


def _read_lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines()


async def test_w19_constants_and_protocol():
    """W19：FORMAT_VERSION=1 / MIGRATIONS 空表 / RecordStore 五动词签名。"""
    assert FORMAT_VERSION == 1
    assert MIGRATIONS == {}
    for verb in ("submit", "replay", "drain", "close", "sync"):
        assert callable(getattr(RecordStore, verb))


async def test_s1_fifo_order(tmp_path):
    """S1：连续 submit 100 条 → close 后 replay 顺序一致。"""
    store = FileRecordStore(tmp_path / "s.jsonl")
    records = [{"op": "set", "key": f"k{i}", "value": i} for i in range(100)]
    for r in records:
        store.submit(r)
    await store.close()
    assert list(store.replay()) == records


async def test_s2_drain_on_close(tmp_path):
    """S2：submit 后立即 close → replay 可见全部记录。"""
    store = FileRecordStore(tmp_path / "s.jsonl")
    store.submit({"op": "set", "key": "a", "value": 1})
    await store.close()
    assert list(store.replay()) == [{"op": "set", "key": "a", "value": 1}]


async def test_s3_crash_window(tmp_path):
    """S3：submit 后不 drain 直接弃内存 → replay 不含该记录，文件自洽无半行。"""
    path = tmp_path / "s.jsonl"
    store = FileRecordStore(path)
    store.submit({"op": "set", "key": "a", "value": 1})
    # 模拟崩溃：取消未跑的 drain 任务（不 drain、不 close），弃内存
    task = store._drain_task
    assert task is not None
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    del store
    # 新实例重放：不含未 drain 记录；文件不存在或自洽（无半行）
    store2 = FileRecordStore(path)
    assert list(store2.replay()) == []
    if path.exists():
        raw = path.read_bytes()
        assert raw == b"" or raw.endswith(b"\n")  # 无半行
    await store2.close()


async def test_s4_poison_reraise(tmp_path):
    """S4：drain 落盘异常 → 后续 submit/drain 同步重抛同一异常，store 拒新记录。"""
    store = FileRecordStore(tmp_path / "s.jsonl")
    store.submit({"op": "set", "key": "ok", "value": 1})
    store.submit({"op": "set", "key": "bad", "value": object()})  # 不可 JSON 序列化
    # drain 任务遇序列化错误 → poison；等待中的 drain() 经 barrier 正常返回
    # （异常在 poison 时刻尚未发生——barrier 先于失败行被消费时不携带异常）
    await store.drain()
    with pytest.raises(TypeError):
        store.submit({"op": "set", "key": "after", "value": 2})
    assert isinstance(store._poisoned, TypeError)
    # poison 态下 drain 的「返回即全部落盘」承诺不可静默违反：任务已终结
    # 时同步重抛首次落盘异常（契约③ 与 submit 同律）
    with pytest.raises(TypeError):
        await store.drain()
    # close 的职责是收尾清理：poison 已暴露，不重复抛出且幂等
    await store.close()
    await store.close()


async def test_close_compacts_tombstones_without_submissions(copy_fixture):
    """X16 收尾时点：replay 累计墓碑 ≥ 阈值后直接 close（无 submit）→ 压缩发生。

    回归基线：close 的「drain 任务从未启动」快捷分支不得跳过墓碑压缩。
    """
    path = copy_fixture("persistence/tree-with-tombstones.jsonl")
    store = FileRecordStore(path, tombstone_threshold=256)
    list(store.replay())  # 累计墓碑计数，drain 任务从未启动
    assert store._drain_task is None
    await store.close()
    lines = _read_lines(path)
    assert len(lines) == 4  # meta + 3 条存活消息
    assert json.loads(lines[0]) == {"type": "meta", "format_version": FORMAT_VERSION}
    store2 = FileRecordStore(path)
    assert [r["id"] for r in store2.replay()] == ["alive-1", "alive-2", "alive-3"]
    await store2.close()


async def test_s5_torn_tail_truncated(copy_fixture):
    """S5：撕裂末行截断丢弃，之前完整行全部产出。"""
    store = FileRecordStore(copy_fixture("persistence/tree-torn-tail.jsonl"))
    records = list(store.replay())
    assert [r["id"] for r in records] == ["m1", "m2"]
    await store.close()


async def test_s6_corrupt_middle_line(copy_fixture, caplog):
    """S6：中间行损坏 → logging.error 报警 + CorruptionError（X7 具名类）。"""
    store = FileRecordStore(copy_fixture("persistence/state-corrupt-mid.jsonl"))
    with caplog.at_level(logging.ERROR):
        with pytest.raises(CorruptionError) as exc_info:
            list(store.replay())
    assert exc_info.value.lineno == 3  # meta=1、好行=2、损坏行=3
    await store.close()


async def test_s7_meta_not_yielded_and_future_version(copy_fixture, tmp_path):
    """S7：meta 首行不产出；版本高于当前 → FormatVersionError（X6 具名类）。"""
    store = FileRecordStore(copy_fixture("persistence/tree-ok.jsonl"))
    records = list(store.replay())
    assert len(records) == 3
    assert all(r.get("type") == "message" for r in records)  # meta 不产出
    await store.close()
    # 版本 99（高于当前）→ 报错
    future = tmp_path / "future.jsonl"
    future.write_text(
        json.dumps({"type": "meta", "format_version": 99}) + "\n", encoding="utf-8"
    )
    store2 = FileRecordStore(future)
    with pytest.raises(FormatVersionError) as exc_info:
        list(store2.replay())
    assert exc_info.value.found == 99
    assert exc_info.value.supported == FORMAT_VERSION
    await store2.close()


async def test_s8_v0_no_meta_file_migrates(copy_fixture):
    """S8：无版本首行的存量文件按版本 0 处理，全部记录照常产出（X5 澄清：无特判）。"""
    store = FileRecordStore(copy_fixture("persistence/state-v0-no-meta.jsonl"))
    records = list(store.replay())
    assert records == [
        {"op": "set", "key": "n", "value": 1},
        {"op": "set", "key": "s", "value": "x"},
        {"op": "delete", "key": "s"},
        {"op": "set", "key": "n", "value": 5},
    ]
    await store.close()


async def test_s9_migration_rewrites_file_via_sync(copy_fixture):
    """S9：低版本文件 replay 后借 sync 通道原子回写：新 meta 首行 + 记录不变。"""
    path = copy_fixture("persistence/state-v0-no-meta.jsonl")
    store = FileRecordStore(path)
    records = list(store.replay())  # 触发迁移回写（排队）
    await store.drain()  # 执行回写
    lines = _read_lines(path)
    assert json.loads(lines[0]) == {"type": "meta", "format_version": FORMAT_VERSION}
    store2 = FileRecordStore(path)
    assert list(store2.replay()) == records  # 重开：记录不变
    await store.close()
    await store2.close()


async def test_s10_merge_last_line(tmp_path):
    """S10：merge_last_line=True，同 key 连续 set 1000 次 → 该 key 仅 1 行。"""
    path = tmp_path / "state.jsonl"
    store = FileRecordStore(path, merge_last_line=True)
    for i in range(1000):
        store.submit({"op": "set", "key": "n", "value": i})
    await store.drain()
    lines = _read_lines(path)
    key_lines = [l for l in lines if json.loads(l).get("key") == "n"]
    assert len(key_lines) == 1
    assert json.loads(key_lines[0])["value"] == 999  # 末次写入
    assert len(lines) == 2  # meta + 1 行；总行数不随次数增长
    await store.close()


async def test_s11_set_then_delete_truncates(tmp_path):
    """S11：末行 set k 紧跟 delete k → 截尾，文件中无 k 的行。"""
    path = tmp_path / "state.jsonl"
    store = FileRecordStore(path, merge_last_line=True)
    store.submit({"op": "set", "key": "k", "value": 1})
    store.submit({"op": "delete", "key": "k"})
    await store.drain()
    lines = _read_lines(path)
    assert all(json.loads(l).get("key") != "k" for l in lines)
    assert list(store.replay()) == []  # 逻辑内容同样为空
    await store.close()


async def test_s12_tombstone_compaction(copy_fixture):
    """S12：墓碑计数 ≥ 阈值（256）→ drain 见底时整文件原子重写，逻辑结果不变。"""
    path = copy_fixture("persistence/tree-with-tombstones.jsonl")
    before_lines = len(_read_lines(path))
    store = FileRecordStore(path, tombstone_threshold=256)
    expected = list(store.replay())  # replay 计数墓碑（X16：初始化加载时执行）
    assert len(expected) == 265  # 5 条消息 + 260 条墓碑（重放原样产出原始行）
    # 逻辑结果（权威链）= 应用墓碑后的消息序列
    tombstoned = {r["id"] for r in expected if r.get("type") == "tombstone"}
    logical = [
        r for r in expected
        if r.get("type") == "message" and r["id"] not in tombstoned
    ]
    assert store._tombstone_count >= 256
    await store.drain()  # 队列见底 → 触发压缩
    after_lines = len(_read_lines(path))
    assert after_lines < before_lines
    store2 = FileRecordStore(path)
    kept = list(store2.replay())
    # 物理清除墓碑行与被标记删除的消息行后，权威链不变
    assert [r["id"] for r in kept] == [r["id"] for r in logical] == [
        "alive-1", "alive-2", "alive-3",
    ]
    assert store2._tombstone_count == 0  # 压缩后计数归零
    await store.close()
    await store2.close()


async def test_s13_sync_atomic_rewrite(tmp_path):
    """S13：sync 后文件 = meta 首行 + 终态记录序列；无残留 .tmp（X12）。"""
    path = tmp_path / "state.jsonl"
    store = FileRecordStore(path)
    store.submit({"op": "set", "key": "stale", "value": 0})
    await store.drain()
    terminal = [{"op": "set", "key": "n", "value": 5}]
    store.sync(terminal)
    await store.drain()
    lines = _read_lines(path)
    assert json.loads(lines[0]) == {"type": "meta", "format_version": FORMAT_VERSION}
    assert [json.loads(l) for l in lines[1:]] == terminal
    assert not (tmp_path / "state.jsonl.tmp").exists()  # 无残留 .tmp
    await store.close()


async def test_s14_orphan_tmp_swept_on_open(tmp_path):
    """S14：打开存储时清扫孤儿 .tmp。"""
    path = tmp_path / "s.jsonl"
    path.write_text("", encoding="utf-8")
    orphan = tmp_path / "s.jsonl.tmp"
    orphan.write_text("partial", encoding="utf-8")
    store = FileRecordStore(path)
    assert not orphan.exists()
    await store.close()


async def test_s15_close_idempotent(tmp_path):
    """S15：close 幂等，不泄漏任务。"""
    store = FileRecordStore(tmp_path / "s.jsonl")
    store.submit({"op": "set", "key": "a", "value": 1})
    await store.close()
    await store.close()  # 不抛
    assert store._drain_task is not None and store._drain_task.done()


# ---------------------------------------------------------------------------
# StateView（S16–S25，stub RecordStore / stub owner 驱动）
# ---------------------------------------------------------------------------


class StubStore:
    """RecordStore 契约的内存 stub（五动词记录器）。"""

    def __init__(self):
        self.submitted: list[dict] = []
        self.synced: list[list[dict]] = []
        self.closed = 0

    def submit(self, record: dict) -> None:
        self.submitted.append(record)

    def replay(self):
        return iter([])

    async def drain(self) -> None:
        pass

    async def close(self) -> None:
        self.closed += 1

    def sync(self, records: list[dict]) -> None:
        self.synced.append(list(records))


class StubHooks:
    """watcher 通道记录器（真 _notify_watch 归 L1 hooks，S24 只验证调用点）。"""

    def __init__(self):
        self.calls: list[tuple] = []

    def _notify_watch(self, owner, value) -> None:
        self.calls.append((owner, value))


class StubOwner:
    def __init__(self):
        self.hooks = StubHooks()


def _open_view(store=None, defaults=None, owner=None, **kw) -> StateView:
    """构造并解锁写闸门（解锁归管线完成点；测试经 object.__setattr__ 模拟）。"""
    view = StateView(store or StubStore(), defaults=defaults, owner=owner, **kw)
    object.__setattr__(view, "_write_gate_open", True)
    return view


def test_s16_attr_and_dict_access_equivalent():
    """S16：属性式与字典式产生相同落盘记录与读出值。"""
    v1 = _open_view()
    v1.n = 0
    v1.n += 1
    v2 = _open_view()
    v2["n"] = 0
    v2["n"] += 1
    assert v1._store.submitted == v2._store.submitted == [
        {"op": "set", "key": "n", "value": 0},
        {"op": "set", "key": "n", "value": 1},
    ]
    assert v1.n == v2["n"] == 1


def test_s17_defaults_fallback_not_persisted():
    """S17：defaults 回退——默认值不落盘；写入覆盖；del 后回退。"""
    view = _open_view()
    view._register("n", 0)
    assert view.n == 0  # 读出回退 default
    assert view._store.submitted == []  # 默认值不落盘
    view.n = 5
    assert view.n == 5
    assert view._store.submitted == [{"op": "set", "key": "n", "value": 5}]
    del view.n
    assert view.n == 0  # 删除持久值后回退 default


def test_s18_undeclared_key_and_contains():
    """S18：未声明未写键 → KeyError / get 回退；__contains__ 双命中。"""
    view = _open_view()
    view._register("n", 0)
    with pytest.raises(KeyError):
        view["typo"]
    assert view.get("typo", "d") == "d"
    assert view.get("typo") is None
    assert "n" in view  # 注册 default 命中
    view["persisted_key"] = 1
    assert "persisted_key" in view  # 持久值命中
    assert "typo" not in view


def test_s19_write_gate():
    """S19：写闸门——未解锁写抛 RuntimeError（X10）、读仅见 defaults；解锁后正常。"""
    store = StubStore()
    view = StateView(store, defaults={"n": 0})
    view._persisted["hidden"] = 1  # 模拟 recover 重放装袋（闸门未开）
    with pytest.raises(RuntimeError):
        view["x"] = 1
    with pytest.raises(RuntimeError):
        view.n = 1
    with pytest.raises(KeyError):
        view["hidden"]  # 读仅见 defaults 不见持久值
    assert view.n == 0
    object.__setattr__(view, "_write_gate_open", True)  # 管线完成点解锁
    view["x"] = 1
    assert view["x"] == 1
    assert view["hidden"] == 1  # 解锁后可见持久值


def test_s20_non_serializable_fails_fast():
    """S20：不可 JSON 序列化值写入时立即抛 TypeError（X11），不产生记录。"""
    view = _open_view()
    with pytest.raises(TypeError):
        view["x"] = object()
    assert view._store.submitted == []
    assert "x" not in view._persisted


def test_s21_register_duplicate_raises():
    """S21：_register 重复声明同 key → ValueError。"""
    view = _open_view()
    view._register("n", 0)
    with pytest.raises(ValueError):
        view._register("n", 1)


def test_s22_write_through_record_format():
    """S22：写透行格式 {"op":"set",...} / {"op":"delete",...}。"""
    view = _open_view()
    view["n"] = 1
    del view["n"]
    assert view._store.submitted == [
        {"op": "set", "key": "n", "value": 1},
        {"op": "delete", "key": "n"},
    ]


def test_s23_maybe_compact():
    """S23：_maybe_compact 三时点决策：阈值触发 / force / 未达不触发。"""
    store = StubStore()
    view = _open_view(store, compact_threshold=3)
    view["a"] = 1
    view["b"] = 2
    assert store.synced == []  # 未达阈值
    view["c"] = 3  # 达阈值 → 触发
    assert len(store.synced) == 1
    assert store.synced[0] == [
        {"op": "set", "key": "a", "value": 1},
        {"op": "set", "key": "b", "value": 2},
        {"op": "set", "key": "c", "value": 3},
    ]  # 负载为 _persisted 逐键 set 行
    assert view._lines_since_compact == 0  # 计数归零
    view._maybe_compact()  # 未达阈值且非 force → 不触发
    assert len(store.synced) == 1
    view._maybe_compact(force=True)  # force 立即触发
    assert len(store.synced) == 2


async def test_s24_watcher_channel(tmp_path):
    """S24：watcher 调用点预留——写透后 _notify_watch 一次、old 为写前读值。"""
    owner = StubOwner()
    view = _open_view(owner=owner)
    view._register("n", 0)
    view.n = 5
    assert len(owner.hooks.calls) == 1
    called_owner, fu = owner.hooks.calls[0]
    assert called_owner is owner
    assert (fu.name, fu.old, fu.new) == ("n", 0, 5)  # old 为写前读值
    del view.n  # del 不触发
    assert len(owner.hooks.calls) == 1


def test_s24_watcher_skipped_without_owner_or_loop():
    """S24：owner 为 None 或无运行中 loop → 静默跳过且写照常。"""
    view = _open_view()  # owner=None
    view["n"] = 1
    assert view["n"] == 1
    # 有 owner 但无运行中 loop（本测试为同步函数）
    owner = StubOwner()
    view2 = _open_view(owner=owner)
    view2["n"] = 1
    assert view2["n"] == 1
    assert owner.hooks.calls == []  # 静默跳过


async def test_state_view_close_delegates():
    """W26：_close 转调内嵌 store 的 close。"""
    store = StubStore()
    view = _open_view(store)
    await view._close()
    assert store.closed == 1


async def test_s25_replay_baselines(copy_fixture):
    """S25：基线语料 replay 产出与预写内容逐条一致。"""
    store = FileRecordStore(copy_fixture("persistence/tree-ok.jsonl"))
    tree_records = list(store.replay())
    assert [r["id"] for r in tree_records] == ["m1", "m2", "m3"]
    assert tree_records[1]["turn_end"] is True
    await store.close()
    store2 = FileRecordStore(copy_fixture("persistence/state-ok.jsonl"))
    assert list(store2.replay()) == [
        {"op": "set", "key": "n", "value": 1},
        {"op": "set", "key": "s", "value": "x"},
        {"op": "delete", "key": "s"},
        {"op": "set", "key": "n", "value": 5},
    ]
    await store2.close()


async def test_s16_real_store_roundtrip(tmp_path):
    """S16 补充：StateView 写透经真实 FileRecordStore 落盘并可恢复（合并语义）。"""
    path = tmp_path / "state.jsonl"
    store = FileRecordStore(path, merge_last_line=True)
    view = _open_view(store)
    view._register("n", 0)
    view.n = 1
    view.n += 1
    await view._close()
    # 重放恢复（模拟 recover 装袋）
    store2 = FileRecordStore(path, merge_last_line=True)
    records = list(store2.replay())
    assert records == [{"op": "set", "key": "n", "value": 2}]  # 末行合并生效
    view2 = _open_view(store2)
    view2._register("n", 0)
    for r in records:
        view2._persisted[r["key"]] = r["value"]
    assert view2.n == 2
    await view2._close()
