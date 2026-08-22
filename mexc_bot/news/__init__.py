"""Fatal-class news monitor (delist / hack / closure / scam)."""

from .watcher import NewsWatcher
from .classify import classify_headline, evaluate_headline, should_alarm

__all__ = ["NewsWatcher", "classify_headline", "evaluate_headline", "should_alarm"]
