"""阶段 1 snapshot.py 测试（T-91 ~ T-93）：Info 视图与 Snapshot 的可构造性
与可序列化不变量。本期只建类不做收集（收集入口属阶段 2/3）。"""

import dataclasses
import json
from datetime import datetime, timezone

from flowing.snapshot import (
    AgentInfo,
    AgentSnapshot,
    EntryInfo,
    ExecutionInfo,
    MessageQueueInfo,
    MessageTreeInfo,
    ModelInfo,
    NodeInfo,
    RuntimeSnapshot,
    TurnContextInfo,
)

NOW = datetime(2026, 8, 29, 8, 0, 0, tzinfo=timezone.utc)


def _full_runtime_snapshot() -> RuntimeSnapshot:
    return RuntimeSnapshot(
        nodes={"runtime-0": NodeInfo(type="runtime", parent_id=None),
               "agent-1": NodeInfo(type="agent", parent_id="runtime-0")},
        plugins=["comm", "cron"],
        agents={"agent-1": AgentInfo(agent_type="OrderAgent", parent_agent_id="runtime-0",
                                     created_at=NOW, loaded=True)},
        providers=["deepseek-personal"],
        config_overrides={"limits": {"turns": 10}},
        resources=["order-agent"],
    )


def _full_agent_snapshot() -> AgentSnapshot:
    return AgentSnapshot(
        node_id="agent-1",
        parent_id="runtime-0",
        agent_type="OrderAgent",
        paused=False,
        messages=MessageTreeInfo(count=7, head_id="msg-7"),
        current_head_id="msg-7",
        current_turn=TurnContextInfo(started_at=NOW, finished_at=None, message_count=2, aborted=False),
        executions={"exec-1": ExecutionInfo(kind="tool", tags=["io"], started_at=NOW)},
        message_queue=MessageQueueInfo(size=1, pending=0),
        model=ModelInfo(model="deepseek-chat", provider="deepseek-personal", model_tag="fast",
                        context_window=64000, max_output_tokens=8192, thinking_budget=None),
        tool_entries=[EntryInfo(alias="read-file", enabled=True, agent_type=None)],
        subagent_entries=[EntryInfo(alias="pay", enabled=False, agent_type="PayAgent")],
        context_usage=None,
    )


def test_t91_all_views_constructible():
    # 八个 Info 视图 + 两个 Snapshot 均可按 docstring 字段集构造（字段为
    # JSON 标量 / datetime / list / dict / 嵌套 Info）
    snap_r = _full_runtime_snapshot()
    snap_a = _full_agent_snapshot()
    assert snap_r.nodes["agent-1"].parent_id == "runtime-0"
    assert snap_a.current_turn.message_count == 2
    assert snap_a.subagent_entries[0].agent_type == "PayAgent"
    # 空值形态同样可构造
    assert TurnContextInfo(started_at=NOW, finished_at=None, message_count=0, aborted=False).finished_at is None
    assert ModelInfo(model=None, provider=None, model_tag=None, context_window=None,
                     max_output_tokens=None, thinking_budget=None).model is None


def _to_jsonable(obj):
    """快照编码约定（R-9）：dataclass → dict 递归，datetime → ISO 字符串。"""
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {f.name: _to_jsonable(getattr(obj, f.name)) for f in dataclasses.fields(obj)}
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {k: _to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_to_jsonable(v) for v in obj]
    return obj


def test_t92_json_round_trip():
    for snap in (_full_runtime_snapshot(), _full_agent_snapshot()):
        encoded = _to_jsonable(snap)
        decoded = json.loads(json.dumps(encoded, ensure_ascii=False))
        assert decoded == encoded  # 序列化往返字段值不丢
    # datetime 字段按 ISO 字符串编码
    decoded_a = json.loads(json.dumps(_to_jsonable(_full_agent_snapshot())))
    assert decoded_a["current_turn"]["started_at"] == NOW.isoformat()
    decoded_r = json.loads(json.dumps(_to_jsonable(_full_runtime_snapshot())))
    assert decoded_r["agents"]["agent-1"]["created_at"] == NOW.isoformat()


def test_t93_field_set_reflection():
    # RuntimeSnapshot 无 status 字段（M-77）
    assert "status" not in {f.name for f in dataclasses.fields(RuntimeSnapshot)}
    # AgentSnapshot 无控制信号类字段（无 asyncio.Event/Task/Future）
    for f in dataclasses.fields(AgentSnapshot):
        assert "Event" not in str(f.type) and "Task" not in str(f.type) and "Future" not in str(f.type)
    # TurnContextInfo.finished_at 恒 None 的语义以类型 datetime | None 承载
    types = {f.name: str(f.type) for f in dataclasses.fields(TurnContextInfo)}
    assert "None" in types["finished_at"]
