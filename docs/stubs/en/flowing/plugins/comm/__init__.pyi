"""The in-process communication extension package.

This package re-exports its public API from one place:
``CommPlugin`` for Runtime installation, ``use_comm`` for per-Agent setup,
``Communication`` for the bus, ``CommHandle`` for an endpoint,
``SignalEnvelope`` and ``EventEnvelope`` for the two envelope models, and
``communication_key`` for provide/inject access to the bus.

.. seealso:: :mod:`flowing.plugins.comm.comm` for activation and registration
    details, and :mod:`flowing.plugins.comm.models` for the envelope models.
"""
from .comm import CommHandle, CommPlugin, Communication, communication_key, use_comm
from .models import EventEnvelope, SignalEnvelope

__all__: list[str] = [
    "CommPlugin",
    "Communication",
    "CommHandle",
    "SignalEnvelope",
    "EventEnvelope",
    "communication_key",
    "use_comm",
]
