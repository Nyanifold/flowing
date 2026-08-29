"""阶段 4 cron 数据对象测试（W16–W18）：测试清单 T35–T36。"""

from __future__ import annotations

from datetime import datetime

import pytest

from flowing.plugins.cron import CronAction, CronJob


class TestT35ActionShape:
    """T35：CronAction.__post_init__ 形状校验。"""

    def test_message_requires_prompt(self):
        with pytest.raises(ValueError):
            CronAction(kind="message")
        with pytest.raises(ValueError):
            CronAction(kind="message", prompt="")

    def test_tool_call_requires_tool(self):
        with pytest.raises(ValueError):
            CronAction(kind="tool_call")
        with pytest.raises(ValueError):
            CronAction(kind="tool_call", tool="")

    def test_custom_kind_passes(self):
        action = CronAction(kind="daily-report")  # 自定义 kind 形状校验放行
        assert action.kind == "daily-report"
        assert CronAction(kind="message", prompt="x").prompt == "x"
        assert CronAction(kind="tool_call", tool="t").tool == "t"


class TestT36RoundTrip:
    """T36：CronAction / CronJob 的 to_dict / from_dict 往返与向前兼容。"""

    def test_action_roundtrip_writes_empty_defaults(self):
        action = CronAction(kind="message", prompt="沉淀")
        data = action.to_dict()
        # 空默认字段照常写出（落盘结构自描述）
        assert data == {"kind": "message", "prompt": "沉淀",
                        "tool": "", "args": {}}
        assert CronAction.from_dict(data) == action

    def test_job_roundtrip(self):
        job = CronJob(
            id="j1", node_id="agent-1", cron="*/5 * * * *",
            action=CronAction(kind="tool_call", tool="check",
                              args={"folder": "INBOX"}),
            source="tick", created_at=datetime(2026, 8, 30, 2, 3, 4),
            recurring=False, last_fired_at=datetime(2026, 8, 30, 3, 0, 0),
        )
        data = job.to_dict()
        assert data["created_at"] == "2026-08-30T02:03:04"   # ISO 8601
        assert data["last_fired_at"] == "2026-08-30T03:00:00"
        rebuilt = CronJob.from_dict(data)
        assert rebuilt == job
        assert rebuilt.created_at == datetime(2026, 8, 30, 2, 3, 4)

    def test_from_dict_forward_compat(self):
        data = {
            "id": "j2", "node_id": "agent-1", "cron": "* * * * *",
            "action": {"kind": "message", "prompt": "x"},
            "created_at": "2026-08-30T00:00:00",
            # 缺 recurring / last_fired_at / source —— 向前兼容旧版落盘数据
        }
        job = CronJob.from_dict(data)
        assert job.recurring is True
        assert job.last_fired_at is None
        assert job.source == "cron:j2"   # source 缺省回填

    def test_from_dict_broken_action_fails_fast(self):
        data = {
            "id": "j3", "node_id": "agent-1", "cron": "* * * * *",
            "action": {"kind": "message", "prompt": ""},   # 形状损坏
            "created_at": "2026-08-30T00:00:00",
        }
        with pytest.raises(ValueError):
            CronJob.from_dict(data)
