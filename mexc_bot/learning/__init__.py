"""Learning layer: event log, labels, outcomes, journal, engagement.

Isolated from target-price alerts — never deletes or rewrites the alerts table.
"""

from .engagement import EngagementBridge, infer_engagement
from .outcomes import OutcomePoller
from .store import EventStore

__all__ = [
    "EventStore",
    "OutcomePoller",
    "EngagementBridge",
    "infer_engagement",
]
