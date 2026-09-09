"""Tests for the P0 contract models."""

from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError

from vibe.research_pipeline.contracts import (
    FeatureDeclaration,
    FeatureKind,
    MetricDefinition,
    MetricUnit,
    RetentionProfile,
    RunEvidence,
    SegmentRole,
    SessionSegment,
    SplitManifest,
    SurvivorshipBias,
    UniverseSpec,
    UniverseType,
)

ZERO_HASH = "0" * 64


def _segment(role, start, end, count=1):
    return SessionSegment(
        role=role, start_session=start, end_session=end, session_count=count
    )


def _manifest(**overrides):
    defaults = dict(
        calendar_name="XNYS",
        segments=(
            _segment(SegmentRole.TRAIN, date(2018, 1, 2), date(2020, 12, 31)),
            _segment(SegmentRole.VALIDATION, date(2021, 1, 4), date(2021, 6, 30)),
            _segment(SegmentRole.TEST, date(2021, 7, 1), date(2021, 12, 31)),
        ),
        purge_sessions_derived=0,
        purge_sessions_applied=1,
        embargo_sessions_derived=0,
        embargo_sessions_applied=1,
    )
    defaults.update(overrides)
    return SplitManifest(**defaults)


def _evidence(**overrides):
    defaults = dict(
        trade_count=10,
        trade_ledger_sha256=ZERO_HASH,
        equity_curve_sha256="a" * 64,
        equity_points=100,
        starting_equity=100_000.0,
        ending_equity=101_000.0,
        gross_pnl=1_050.0,
        total_costs=50.0,
        net_pnl=1_000.0,
        max_observed_leverage=1.2,
        min_cash=45_000.0,
    )
    defaults.update(overrides)
    return RunEvidence(**defaults)


# --------------------------------------------------------------------------
# MetricDefinition
# --------------------------------------------------------------------------


def test_metric_key_must_be_snake_case():
    with pytest.raises(ValidationError, match="snake_case"):
        MetricDefinition(
            key="WinRate",
            display_name="Win Rate",
            unit=MetricUnit.FRACTION,
            description="wins / trades",
            calculation_version=1,
        )


def test_metric_definition_records_exclusions():
    metric = MetricDefinition(
        key="total_pnl",
        display_name="Total P&L",
        unit=MetricUnit.CURRENCY,
        description="Sum of net P&L across all trades",
        calculation_version=2,
        excludes=("trades with non-positive initial_risk",),
    )
    assert metric.excludes == ("trades with non-positive initial_risk",)


def test_metric_definition_is_frozen():
    metric = MetricDefinition(
        key="win_rate",
        display_name="Win Rate",
        unit=MetricUnit.FRACTION,
        description="wins / trades",
        calculation_version=1,
    )
    with pytest.raises(ValidationError):
        metric.calculation_version = 3


# --------------------------------------------------------------------------
# FeatureDeclaration
# --------------------------------------------------------------------------


def test_causal_feature_cannot_look_ahead():
    with pytest.raises(ValidationError, match="declared CAUSAL"):
        FeatureDeclaration(
            name="future_return",
            kind=FeatureKind.CAUSAL,
            lookahead_bars=1,
            description="Return over the next bar",
        )


def test_diagnostic_feature_may_look_ahead():
    feature = FeatureDeclaration(
        name="forward_return_5",
        kind=FeatureKind.DIAGNOSTIC,
        lookahead_bars=5,
        description="Labelling target, analysis only",
    )
    assert feature.lookahead_bars == 5


# --------------------------------------------------------------------------
# UniverseSpec
# --------------------------------------------------------------------------


def test_single_symbol_universe():
    spec = UniverseSpec(
        universe_type=UniverseType.SINGLE_SYMBOL,
        symbols=("qqq",),
        survivorship_bias=SurvivorshipBias.NOT_APPLICABLE,
        selection_rationale="Primary research instrument",
    )
    assert spec.symbols == ("QQQ",)
    assert len(spec.universe_hash) == 64


def test_symbol_order_does_not_change_universe_hash():
    kwargs = dict(
        universe_type=UniverseType.STATIC_DECLARED,
        survivorship_bias=SurvivorshipBias.PRESENT,
        selection_rationale="Liquid large caps held constant for the study",
    )
    a = UniverseSpec(symbols=("AAPL", "MSFT", "NVDA"), **kwargs)
    b = UniverseSpec(symbols=("NVDA", "AAPL", "MSFT"), **kwargs)
    assert a.universe_hash == b.universe_hash


def test_duplicate_symbols_rejected():
    with pytest.raises(ValidationError, match="Duplicate symbols"):
        UniverseSpec(
            universe_type=UniverseType.STATIC_DECLARED,
            symbols=("AAPL", "aapl"),
            survivorship_bias=SurvivorshipBias.PRESENT,
            selection_rationale="test",
        )


def test_static_universe_must_disclose_survivorship_bias():
    """The set was chosen knowing who survived; claiming otherwise is the bias."""
    with pytest.raises(ValidationError, match="survivorship_bias=present"):
        UniverseSpec(
            universe_type=UniverseType.STATIC_DECLARED,
            symbols=("AAPL", "MSFT"),
            survivorship_bias=SurvivorshipBias.ABSENT,
            selection_rationale="test",
        )


def test_single_symbol_universe_rejects_multiple_symbols():
    with pytest.raises(ValidationError, match="exactly one symbol"):
        UniverseSpec(
            universe_type=UniverseType.SINGLE_SYMBOL,
            symbols=("AAPL", "MSFT"),
            survivorship_bias=SurvivorshipBias.NOT_APPLICABLE,
            selection_rationale="test",
        )


def test_point_in_time_screening_is_blocked_with_explanation():
    with pytest.raises(ValidationError, match="delisted-symbol history"):
        UniverseSpec(
            universe_type=UniverseType.POINT_IN_TIME_SCREENED,
            symbols=("AAPL",),
            survivorship_bias=SurvivorshipBias.ABSENT,
            selection_rationale="Liquidity screen",
        )


# --------------------------------------------------------------------------
# SplitManifest
# --------------------------------------------------------------------------


def test_manifest_hash_is_stable():
    assert _manifest().manifest_hash == _manifest().manifest_hash


def test_manifest_hash_changes_with_boundaries():
    other = _manifest(
        segments=(
            _segment(SegmentRole.TRAIN, date(2018, 1, 2), date(2020, 12, 30)),
            _segment(SegmentRole.VALIDATION, date(2021, 1, 4), date(2021, 6, 30)),
            _segment(SegmentRole.TEST, date(2021, 7, 1), date(2021, 12, 31)),
        )
    )
    assert other.manifest_hash != _manifest().manifest_hash


def test_overlapping_evaluation_segments_rejected():
    with pytest.raises(ValidationError, match="overlap"):
        _manifest(
            segments=(
                _segment(SegmentRole.TRAIN, date(2018, 1, 2), date(2021, 3, 1)),
                _segment(SegmentRole.TEST, date(2021, 1, 4), date(2021, 12, 31)),
            )
        )


def test_warmup_may_overlap_earlier_data():
    """Priming indicators on already-seen sessions is context, not leakage."""
    manifest = _manifest(
        segments=(
            _segment(SegmentRole.WARMUP, date(2020, 12, 1), date(2020, 12, 31), 20),
            _segment(SegmentRole.TRAIN, date(2018, 1, 2), date(2020, 12, 31)),
            _segment(SegmentRole.TEST, date(2021, 1, 4), date(2021, 12, 31)),
        )
    )
    assert manifest.warmup_sessions == 0


def test_applied_purge_below_derived_is_rejected():
    with pytest.raises(ValidationError, match="below the derived requirement"):
        _manifest(purge_sessions_derived=5, purge_sessions_applied=1)


def test_extra_purge_padding_is_allowed():
    manifest = _manifest(purge_sessions_derived=0, purge_sessions_applied=3)
    assert manifest.purge_sessions_applied == 3


def test_applied_embargo_below_derived_is_rejected():
    with pytest.raises(ValidationError, match="below the derived requirement"):
        _manifest(embargo_sessions_derived=4, embargo_sessions_applied=0)


def test_segment_ending_before_start_rejected():
    with pytest.raises(ValidationError, match="ends"):
        _segment(SegmentRole.TRAIN, date(2021, 5, 1), date(2021, 1, 1))


# --------------------------------------------------------------------------
# RunEvidence
# --------------------------------------------------------------------------


def test_evidence_accepts_reconciling_numbers():
    assert _evidence().net_pnl == 1_000.0


def test_evidence_rejects_costless_pnl_mismatch():
    with pytest.raises(ValidationError, match="does not reconcile"):
        _evidence(gross_pnl=1_000.0, total_costs=50.0, net_pnl=1_000.0)


def test_evidence_rejects_equity_not_matching_net_pnl():
    """Catches money created or destroyed outside the trade ledger."""
    with pytest.raises(ValidationError, match="Money was created or destroyed"):
        _evidence(ending_equity=150_000.0)


def test_evidence_rejects_negative_costs():
    with pytest.raises(ValidationError):
        _evidence(total_costs=-1.0)


def test_evidence_rejects_non_hex_checksum():
    with pytest.raises(ValidationError, match="hex SHA-256"):
        _evidence(trade_ledger_sha256="z" * 64)


def test_evidence_rejects_wrong_length_checksum():
    with pytest.raises(ValidationError):
        _evidence(trade_ledger_sha256="abc")


def test_evidence_defaults_to_summary_retention():
    """Summary is the default, and is the only profile published remotely."""
    assert _evidence().retention_profile is RetentionProfile.SUMMARY


def test_evidence_records_leverage_and_min_cash():
    """Undeclared leverage is indistinguishable from edge."""
    evidence = _evidence(max_observed_leverage=4.7, min_cash=-20_000.0)
    assert evidence.max_observed_leverage == 4.7
    assert evidence.min_cash == -20_000.0
