"""cron 数据对象测试：CronJob / CronFireContext。"""

from datetime import datetime

from flowing.plugins.cron import CronFireContext, CronJob


def test_cronjob_dict_roundtrip():
    job = CronJob(
        id="j1", cron="*/5 * * * *", content="提醒 {{current_time}}",
        source="tick", created_at=datetime(2026, 9, 3, 7, 0, 0),
        last_fired_at=datetime(2026, 9, 3, 7, 5, 0))
    data = job.to_dict()
    assert data == {
        "id": "j1", "cron": "*/5 * * * *",
        "content": "提醒 {{current_time}}", "source": "tick",
        "recurring": True,
        "created_at": "2026-09-03T07:00:00",
        "last_fired_at": "2026-09-03T07:05:00",
    }
    assert CronJob.from_dict(data) == job


def test_cronjob_defaults_never_fired():
    """last_fired_at=None、source 缺省空串、recurring 缺省 → 按默认重建。"""
    job = CronJob(id="j2", cron="* * * * *", content="x",
                  created_at=datetime(2026, 9, 3, 7, 0, 0))
    data = job.to_dict()
    assert data["last_fired_at"] is None and data["source"] == ""
    data.pop("source")      # 缺 source 键：按 "" 回填
    data.pop("recurring")   # 缺 recurring 键：按 True 重建
    rebuilt = CronJob.from_dict(data)
    assert rebuilt.source == "" and rebuilt.recurring is True
    assert rebuilt.last_fired_at is None
    assert rebuilt.created_at == datetime(2026, 9, 3, 7, 0, 0)


def test_cronfire_context_plain_dataclass():
    ctx = CronFireContext(
        id="j", source="daily", cron="0 3 * * *", content="沉淀",
        recurring=True,
        scheduled_at=datetime(2026, 9, 3, 3, 0, 0),
        fired_at=datetime(2026, 9, 3, 3, 0, 5),
        coalesced_count=1, last_fired_at=None)
    assert ctx.shortcut is None          # 初值 None（非 None 即短路门控）
    ctx.content = "改写"                  # handler 可改写字段（普通 dataclass）
    assert ctx.content == "改写"
