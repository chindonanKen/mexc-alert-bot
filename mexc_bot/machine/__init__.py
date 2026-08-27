"""Isolated AD Machine (week-1). Separate book + UI. Default OFF.

Never writes alerts, positions, leftover-avg, or learning_lessons.
Never sends live MEXC orders. Not the rejected student_decide / student_paper path.
"""

from .settings import FEATURE_AD_MACHINE, feature_ad_machine

__all__ = ["FEATURE_AD_MACHINE", "feature_ad_machine"]
