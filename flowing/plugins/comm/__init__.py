"""``flowing.plugins.comm`` —— 通信扩展子包（信封模型 / 总线 / 插件 / 组合件）。

本包由 N-02 拆分而来，对外 API 经 __init__ 再导出不变。
"""

from .comm import CommHandle, CommPlugin, Communication, communication_key, use_comm
from .models import EventEnvelope, SignalEnvelope

__all__ = [
    "CommPlugin",
    "Communication",
    "CommHandle",
    "SignalEnvelope",
    "EventEnvelope",
    "communication_key",
    "use_comm",
]
