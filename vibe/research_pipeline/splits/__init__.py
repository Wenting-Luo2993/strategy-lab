"""Session calendar and split manifest planning (increment P3).

Public API for enumerating exchange sessions and building deterministic,
session-based train/validation/test split manifests.
"""

from __future__ import annotations

from vibe.research_pipeline.splits.calendar import (
    DEFAULT_CALENDAR,
    SessionCalendar,
    SessionReconciliation,
    reconcile_sessions,
)
from vibe.research_pipeline.splits.planner import (
    PLANNER_VERSION,
    SplitPlan,
    SplitPlanError,
    SplitSpec,
    TemporalSplitPlanner,
)

__all__ = [
    "DEFAULT_CALENDAR",
    "SessionCalendar",
    "SessionReconciliation",
    "reconcile_sessions",
    "PLANNER_VERSION",
    "SplitPlan",
    "SplitPlanError",
    "SplitSpec",
    "TemporalSplitPlanner",
]
