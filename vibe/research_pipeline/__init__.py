"""Research pipeline: contracts, identity, lifecycle, splits, and storage.

Increments P0 (contracts), P3 (splits), and P7 (storage) of the backtest
research pipeline. See
``docs/backtest-research-summaries/2026-09-09-backtest-pipeline-implementation-plan.md``.

This package defines the shared vocabulary that later increments depend on, plus
the two services built directly on it. Execution semantics live with the engine
in ``vibe.backtester.core.execution_realism``, since they describe how the
simulator fills orders rather than how research is recorded.
"""

from vibe.research_pipeline.contracts import (
    FeatureDeclaration,
    FeatureKind,
    MetricDefinition,
    MetricDirection,
    MetricUnit,
    RetentionProfile,
    RunEvidence,
    SegmentRole,
    SessionSegment,
    Severity,
    SplitManifest,
    SurvivorshipBias,
    UniverseSpec,
    UniverseType,
    ValidationCategory,
    ValidationFinding,
)
from vibe.research_pipeline.hashing import (
    CANONICAL_ENCODING_VERSION,
    NonCanonicalValueError,
    canonical_json,
    hash_file,
    hash_object,
    sha256_hex,
)
from vibe.research_pipeline.identity import FINGERPRINT_VERSION, RunFingerprint
from vibe.research_pipeline.lifecycle import (
    LEGAL_TRANSITIONS,
    TERMINAL_STATES,
    IllegalTransitionError,
    RunState,
    assert_legal_transition,
    is_legal_transition,
    is_terminal,
)
from vibe.research_pipeline.paths import (
    UnsafeDatabaseLocationError,
    research_db_path,
)
from vibe.research_pipeline.splits import (
    DEFAULT_CALENDAR,
    PLANNER_VERSION,
    SessionCalendar,
    SessionReconciliation,
    SplitPlan,
    SplitPlanError,
    SplitSpec,
    TemporalSplitPlanner,
    reconcile_sessions,
)
from vibe.research_pipeline.storage import (
    SCHEMA_VERSION,
    SqliteResearchStore,
    SqliteRunRecord,
)
from vibe.research_pipeline.store import (
    ImmutableRecordError,
    LeaseError,
    ResearchStore,
    StoreError,
    ValidationRequiredError,
)
from vibe.research_pipeline.validation import (
    AcceptanceDirection,
    AcceptanceRule,
    DataIntegritySummary,
    MetricValidator,
    ValidationInput,
    ValidationProfile,
    ValidationReport,
    ValidationScope,
)

__all__ = [
    "CANONICAL_ENCODING_VERSION",
    "DEFAULT_CALENDAR",
    "FINGERPRINT_VERSION",
    "LEGAL_TRANSITIONS",
    "PLANNER_VERSION",
    "SCHEMA_VERSION",
    "TERMINAL_STATES",
    "FeatureDeclaration",
    "FeatureKind",
    "IllegalTransitionError",
    "ImmutableRecordError",
    "LeaseError",
    "MetricDefinition",
    "MetricDirection",
    "MetricUnit",
    "NonCanonicalValueError",
    "ResearchStore",
    "RetentionProfile",
    "RunEvidence",
    "RunFingerprint",
    "RunState",
    "SegmentRole",
    "SessionCalendar",
    "SessionReconciliation",
    "SessionSegment",
    "Severity",
    "SplitManifest",
    "SplitPlan",
    "SplitPlanError",
    "SplitSpec",
    "SqliteResearchStore",
    "SqliteRunRecord",
    "StoreError",
    "SurvivorshipBias",
    "TemporalSplitPlanner",
    "UniverseSpec",
    "UniverseType",
    "UnsafeDatabaseLocationError",
    "ValidationCategory",
    "ValidationFinding",
    "ValidationRequiredError",
    "AcceptanceDirection",
    "AcceptanceRule",
    "DataIntegritySummary",
    "MetricValidator",
    "ValidationInput",
    "ValidationProfile",
    "ValidationReport",
    "ValidationScope",
    "assert_legal_transition",
    "canonical_json",
    "hash_file",
    "hash_object",
    "is_legal_transition",
    "is_terminal",
    "reconcile_sessions",
    "research_db_path",
    "sha256_hex",
]
