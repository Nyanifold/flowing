"""``flowing.plugins.comm`` —— 通信扩展子包。

本包承载进程内通信扩展的全部公开符号：:class:`CommPlugin` （阶段一
插件）、:func:`use_comm` （阶段二启用）、:class:`Communication` （通信
总线）、:class:`CommHandle` （端点句柄）、:class:`SignalEnvelope` /
:class:`EventEnvelope` （两种信封模型）以及 provide 注入键
:data:`communication_key`；对外 API 经本 ``__init__`` 统一再导出。

.. seealso:: :mod:`flowing.plugins.comm.comm` （插件主模块：启用方式与
    注册面清单）、:mod:`flowing.plugins.comm.models` （信封模型）
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
