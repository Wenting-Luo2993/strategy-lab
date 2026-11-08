"""Core contract models for the research pipeline.

These are the shared vocabulary that later increments build on. They are
deliberately declared up front and kept immutable so that independent
workstreams (splits, execution realism, validation, storage, publishing) can
proceed in parallel without renegotiating data shapes.

All models are frozen. A run's identity payload must not be mutable after it
has been hashed.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from vibe.research_pipeline.hashing import hash_object

__all__ = [
    "Severity",
    "ValidationCategory",
    "MetricDirection",
    "MetricUnit",
    "FeatureKind",
    "UniverseType",
    "SurvivorshipBias",
    "SegmentRole",
    "RetentionProfile",
    "MetricDefinition",
    "ValidationFinding",
    "FeatureDeclaration",
    "UniverseSpec",
    "SessionSegment",
    "SplitManifest",
    "RunEvidence",
]


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


# --------------------------------------------------------------------------
# Enumerations
# --------------------------------------------------------------------------


class Severity(str, Enum):
    """How a validation finding affects the run's terminal state."""

    BLOCKING = "blocking"
    """Hard invariant or accounting failure -> VALIDATION_FAILED."""

    REVIEW = "review"
    """Implausible but not impossible -> REVIEW_REQUIRED, blocks promotion."""

    ADVISORY = "advisory"
    """Recorded, does not change state. Research-acceptance signals live here:
    a strategy being unprofitable is a finding about the strategy, not a defect
    in the run, and must never be conflated with one."""


class ValidationCategory(str, Enum):
    """What kind of problem a finding describes."""

    HARD_INVARIANT = "hard_invariant"
    ACCOUNTING = "accounting"
    EXECUTION_REALISM = "execution_realism"
    DATA_INTEGRITY = "data_integrity"
    SPLIT_INTEGRITY = "split_integrity"
    LOOK_AHEAD = "look_ahead"
    DATASET_SUFFICIENCY = "dataset_sufficiency"
    PLAUSIBILITY = "plausibility"
    RESEARCH_ACCEPTANCE = "research_acceptance"


class MetricDirection(str, Enum):
    """Which direction is 'better' for a metric."""

    HIGHER_IS_BETTER = "higher_is_better"
    LOWER_IS_BETTER = "lower_is_better"
    NEUTRAL = "neutral"


class MetricUnit(str, Enum):
    """Explicit units.

    Units are declared because the existing code mixes them silently: max
    drawdown is computed as a negative fraction but rendered as dollars in the
    sweep report. Naming the unit makes that class of bug impossible to repeat.
    """

    CURRENCY = "currency"
    FRACTION = "fraction"
    PERCENT = "percent"
    R_MULTIPLE = "r_multiple"
    RATIO = "ratio"
    COUNT = "count"
    SESSIONS = "sessions"
    BARS = "bars"


class FeatureKind(str, Enum):
    """Whether a feature may influence trading decisions."""

    CAUSAL = "causal"
    """Uses only information available at or before the decision bar. May drive
    entries, exits, filters, and ranking."""

    DIAGNOSTIC = "diagnostic"
    """May use future information. Analysis and labelling only. Must never be
    reachable from a trading decision."""


class UniverseType(str, Enum):
    """How the traded symbol set was chosen."""

    SINGLE_SYMBOL = "single_symbol"
    STATIC_DECLARED = "static_declared"
    POINT_IN_TIME_SCREENED = "point_in_time_screened"


class SurvivorshipBias(str, Enum):
    NOT_APPLICABLE = "not_applicable"
    PRESENT = "present"
    ABSENT = "absent"


class SegmentRole(str, Enum):
    """Role of a contiguous block of sessions."""

    WARMUP = "warmup"
    """Context only. Indicators are primed; no trading, no metrics."""

    TRAIN = "train"
    VALIDATION = "validation"
    TEST = "test"
    FINAL_OOS = "final_oos"


class RetentionProfile(str, Enum):
    """How much per-run detail is kept."""

    SUMMARY = "summary"
    """Parameters and metrics only. The default and the only profile ever
    published remotely."""

    DIAGNOSTIC = "diagnostic"
    """Adds the local trade ledger. Opt-in, for debugging suspect metrics."""

    FULL = "full"
    """Adds the local equity curve. Opt-in, local only."""


# --------------------------------------------------------------------------
# Contracts
# --------------------------------------------------------------------------


class MetricDefinition(_Frozen):
    """A single, unambiguous definition of one metric.

    Existing code disagrees with itself about what a metric means: winners are
    ``r > 0`` in one module and losers are ``r < 0`` in another, so exactly-zero
    trades vanish and ``wins + losses == trades`` fails spuriously. A metric
    without a definition record cannot be validated or compared across runs.
    """

    key: str = Field(..., min_length=1, description="Stable machine identifier")
    display_name: str = Field(..., min_length=1)
    unit: MetricUnit
    direction: MetricDirection = MetricDirection.NEUTRAL
    description: str = Field(..., min_length=1, description="Exact formula in prose")
    calculation_version: int = Field(
        ...,
        ge=1,
        description=(
            "Bumped whenever the formula changes. Metrics with different "
            "calculation versions are not comparable and must not be plotted "
            "on the same axis."
        ),
    )
    excludes: tuple[str, ...] = Field(
        default=(),
        description=(
            "Explicit list of what is dropped from the calculation, e.g. "
            "'trades with non-positive initial_risk'. Silent exclusions are "
            "how a metric quietly stops describing the run."
        ),
    )

    @field_validator("key")
    @classmethod
    def _key_is_snake_case(cls, value: str) -> str:
        if not value.replace("_", "").isalnum() or value != value.lower():
            raise ValueError(f"Metric key must be lower snake_case, got {value!r}")
        return value


class ValidationFinding(_Frozen):
    """One issue raised by a validation gate.

    Findings are collected in a single pass. The first blocking failure does not
    short-circuit the rest, because a partial diagnosis sends the investigation
    down the wrong path.
    """

    code: str = Field(..., min_length=1, description="Stable identifier, e.g. 'ACC-003'")
    category: ValidationCategory
    severity: Severity
    message: str = Field(..., min_length=1)
    metric_key: Optional[str] = Field(
        None, description="Metric implicated, when the finding is metric-scoped"
    )
    observed: Optional[Any] = None
    expected: Optional[str] = Field(
        None, description="Human-readable statement of what was expected"
    )
    context: dict[str, Any] = Field(default_factory=dict)


class FeatureDeclaration(_Frozen):
    """Registration of a computed feature and its causality class."""

    name: str = Field(..., min_length=1)
    kind: FeatureKind
    lookback_bars: int = Field(
        0,
        ge=0,
        description="Bars of history required before the value is defined",
    )
    lookahead_bars: int = Field(
        0,
        ge=0,
        description=(
            "Bars of future information consumed. Must be 0 for CAUSAL "
            "features; this is the field the leakage harness asserts on."
        ),
    )
    description: str = Field(..., min_length=1)

    @model_validator(mode="after")
    def _causal_features_cannot_look_ahead(self) -> "FeatureDeclaration":
        if self.kind is FeatureKind.CAUSAL and self.lookahead_bars != 0:
            raise ValueError(
                f"Feature {self.name!r} is declared CAUSAL but consumes "
                f"{self.lookahead_bars} future bars. Either fix the feature or "
                f"reclassify it as DIAGNOSTIC (which bars it from trading "
                f"decisions)."
            )
        return self


class UniverseSpec(_Frozen):
    """The traded symbol set and its bias disclosure."""

    universe_type: UniverseType
    symbols: tuple[str, ...] = Field(..., min_length=1)
    survivorship_bias: SurvivorshipBias
    selection_rationale: str = Field(
        ...,
        min_length=1,
        description="How members were chosen. Required, so bias is never implicit.",
    )

    @field_validator("symbols")
    @classmethod
    def _normalize_symbols(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        cleaned = [s.strip().upper() for s in value]
        if any(not s for s in cleaned):
            raise ValueError("Symbols must be non-empty")
        if len(set(cleaned)) != len(cleaned):
            raise ValueError(f"Duplicate symbols in universe: {cleaned}")
        # Sorted so member order cannot split the hash.
        return tuple(sorted(cleaned))

    @model_validator(mode="after")
    def _bias_matches_type(self) -> "UniverseSpec":
        if self.universe_type is UniverseType.SINGLE_SYMBOL:
            if len(self.symbols) != 1:
                raise ValueError(
                    f"universe_type=single_symbol requires exactly one symbol, "
                    f"got {len(self.symbols)}"
                )
            if self.survivorship_bias is not SurvivorshipBias.NOT_APPLICABLE:
                raise ValueError(
                    "A single-symbol universe must declare "
                    "survivorship_bias=not_applicable"
                )
        elif self.universe_type is UniverseType.STATIC_DECLARED:
            if self.survivorship_bias is not SurvivorshipBias.PRESENT:
                raise ValueError(
                    "A static_declared universe was chosen with knowledge of "
                    "which names survived, so it must declare "
                    "survivorship_bias=present. Claiming otherwise is the bias."
                )
        elif self.universe_type is UniverseType.POINT_IN_TIME_SCREENED:
            raise ValueError(
                "universe_type=point_in_time_screened is not supported: the "
                "repository has no delisted-symbol history, so a point-in-time "
                "screen cannot be reconstructed without survivorship bias. "
                "Use static_declared and disclose the bias."
            )
        return self

    @property
    def universe_hash(self) -> str:
        return hash_object(
            {
                "universe_type": self.universe_type,
                "symbols": list(self.symbols),
                "survivorship_bias": self.survivorship_bias,
            }
        )


class SessionSegment(_Frozen):
    """A contiguous, inclusive block of exchange sessions with a single role."""

    role: SegmentRole
    start_session: date
    end_session: date
    session_count: int = Field(..., ge=0)

    @model_validator(mode="after")
    def _ordered(self) -> "SessionSegment":
        if self.end_session < self.start_session:
            raise ValueError(
                f"Segment {self.role.value} ends ({self.end_session}) before it "
                f"starts ({self.start_session})"
            )
        return self


class SplitManifest(_Frozen):
    """An explicit, hashable description of how data was partitioned.

    Splits are enumerated in exchange sessions rather than calendar days.
    Calendar arithmetic (the existing walk-forward uses ``months * 30``) drifts
    against holidays and half-days, so nominally equal folds contain different
    numbers of trading opportunities and are not comparable.
    """

    manifest_version: int = Field(1, ge=1)
    calendar_name: str = Field(
        ..., min_length=1, description="Exchange calendar, e.g. 'XNYS'"
    )
    segments: tuple[SessionSegment, ...] = Field(..., min_length=1)

    purge_sessions_derived: int = Field(
        ...,
        ge=0,
        description=(
            "Sessions that must be dropped between adjacent segments, derived "
            "from the strategy's maximum label horizon. Zero for a strategy "
            "that is always flat at the close."
        ),
    )
    purge_sessions_applied: int = Field(..., ge=0)
    embargo_sessions_derived: int = Field(..., ge=0)
    embargo_sessions_applied: int = Field(..., ge=0)
    warmup_sessions: int = Field(
        0,
        ge=0,
        description=(
            "Sessions prepended for indicator priming. Context, not leakage: "
            "no trades are taken and no metrics are attributed to them."
        ),
    )

    @model_validator(mode="after")
    def _applied_not_below_derived(self) -> "SplitManifest":
        if self.purge_sessions_applied < self.purge_sessions_derived:
            raise ValueError(
                f"Applied purge ({self.purge_sessions_applied}) is below the "
                f"derived requirement ({self.purge_sessions_derived}). Extra "
                f"padding is allowed; less than required is leakage."
            )
        if self.embargo_sessions_applied < self.embargo_sessions_derived:
            raise ValueError(
                f"Applied embargo ({self.embargo_sessions_applied}) is below "
                f"the derived requirement ({self.embargo_sessions_derived})."
            )
        return self

    @model_validator(mode="after")
    def _evaluation_segments_do_not_overlap(self) -> "SplitManifest":
        """Warmup may overlap earlier data; evaluation segments may not.

        Warmup is deliberately exempt: priming indicators on sessions that also
        belong to an earlier training segment is legitimate, because no trade or
        metric is attributed to warmup bars.
        """
        graded = [s for s in self.segments if s.role is not SegmentRole.WARMUP]
        ordered = sorted(graded, key=lambda s: s.start_session)
        for earlier, later in zip(ordered, ordered[1:]):
            if later.start_session <= earlier.end_session:
                raise ValueError(
                    f"Segments {earlier.role.value} "
                    f"({earlier.start_session}..{earlier.end_session}) and "
                    f"{later.role.value} ({later.start_session}.."
                    f"{later.end_session}) overlap. Overlapping evaluation "
                    f"segments mean the test set is not out-of-sample."
                )
        return self

    @property
    def manifest_hash(self) -> str:
        return hash_object(self)


class RunEvidence(_Frozen):
    """Tamper-evident proof of what a run actually produced.

    Recorded under *every* retention profile, including ``summary``. When trades
    are not retained, the checksum is still computed before they are discarded,
    so a summary-only run remains verifiable: re-running from the fingerprint
    must reproduce the same ledger checksum.
    """

    trade_count: int = Field(..., ge=0)
    trade_ledger_sha256: str = Field(..., min_length=64, max_length=64)
    equity_curve_sha256: str = Field(..., min_length=64, max_length=64)
    equity_points: int = Field(..., ge=0)

    first_session: Optional[date] = None
    last_session: Optional[date] = None

    starting_equity: float
    ending_equity: float
    gross_pnl: float
    total_costs: float = Field(
        ..., ge=0.0, description="Commission plus slippage plus fees, exit side included"
    )
    net_pnl: float

    max_observed_leverage: float = Field(
        ...,
        ge=0.0,
        description=(
            "Peak gross exposure divided by equity. Published because position "
            "sizing currently has no buying-power bound, and undeclared "
            "leverage is indistinguishable from edge."
        ),
    )
    min_cash: float = Field(
        ...,
        description="Lowest cash balance reached. Negative means implicit margin.",
    )

    retention_profile: RetentionProfile = RetentionProfile.SUMMARY

    @field_validator("trade_ledger_sha256", "equity_curve_sha256")
    @classmethod
    def _is_hex_digest(cls, value: str) -> str:
        lowered = value.lower()
        if any(c not in "0123456789abcdef" for c in lowered):
            raise ValueError("Checksum must be a lowercase hex SHA-256 digest")
        return lowered

    @model_validator(mode="after")
    def _pnl_reconciles(self) -> "RunEvidence":
        """Net P&L must equal gross minus costs, and equity must move by net.

        This replaces the previous acceptance criterion
        ``equity == cash + sum(mark_to_market)``, which was tautological:
        ``Portfolio.update_equity`` computes equity that way, so the assertion
        could never fail and provided no protection whatsoever.
        """
        tolerance = 0.01
        if abs((self.gross_pnl - self.total_costs) - self.net_pnl) > tolerance:
            raise ValueError(
                f"P&L does not reconcile: gross ({self.gross_pnl}) - costs "
                f"({self.total_costs}) != net ({self.net_pnl})"
            )
        equity_delta = self.ending_equity - self.starting_equity
        if abs(equity_delta - self.net_pnl) > tolerance:
            raise ValueError(
                f"Equity change ({equity_delta}) does not match net P&L "
                f"({self.net_pnl}). Money was created or destroyed outside the "
                f"trade ledger."
            )
        return self
