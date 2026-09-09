"""Research pipeline: contracts, identity, and lifecycle.

Increment P0 of the backtest research pipeline. See
``docs/backtest-research-summaries/2026-09-09-backtest-pipeline-implementation-plan.md``.

This package defines the shared vocabulary that later increments depend on. It
deliberately contains no execution logic, so that independent workstreams can
build against stable data shapes in parallel.
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
from vibe.research_pipeline.store import (
    ImmutableRecordError,
    ResearchStore,
    StoreError,
)

__all__ = [
    "CANONICAL_ENCODING_VERSION",
    "FINGERPRINT_VERSION",
    "LEGAL_TRANSITIONS",
    "TERMINAL_STATES",
    "FeatureDeclaration",
    "FeatureKind",
    "IllegalTransitionError",
    "ImmutableRecordError",
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
    "SessionSegment",
    "Severity",
    "SplitManifest",
    "StoreError",
    "SurvivorshipBias",
    "UniverseSpec",
    "UniverseType",
    "UnsafeDatabaseLocationError",
    "ValidationCategory",
    "ValidationFinding",
    "assert_legal_transition",
    "canonical_json",
    "hash_file",
    "hash_object",
    "is_legal_transition",
    "is_terminal",
    "research_db_path",
    "sha256_hex",
]
