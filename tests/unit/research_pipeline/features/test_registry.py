"""Tests for the feature registry (P5).

The registry's job is to make an undeclared or leaky feature impossible to use
by accident. These tests pin the classification so a future fitted transform
cannot be added silently, which is the explicit requirement in section 9.
"""

from __future__ import annotations

import pytest

from vibe.research_pipeline.contracts import FeatureDeclaration, FeatureKind
from vibe.research_pipeline.features.registry import (
    FEATURE_REGISTRY,
    SWEEP_PRECOMPUTED_FEATURES,
    FeatureNotDeclaredError,
    LeakyFeatureInDecisionError,
    assert_decision_features_are_causal,
    causal_feature_names,
    declaration_for,
    diagnostic_feature_names,
    registry_hash,
)

#: A registry containing one synthetic leaky feature.
#:
#: Every feature that ships is now causal, so the decision guard cannot be made
#: to fire against the live registry. Rather than delete the tests that prove it
#: refuses leaky input - which would leave the guard completely unexercised -
#: they run against this injected registry instead.
_REGISTRY_WITH_LEAK = dict(FEATURE_REGISTRY) | {
    "leaky_probe": FeatureDeclaration(
        name="leaky_probe",
        kind=FeatureKind.DIAGNOSTIC,
        lookback_bars=1,
        lookahead_bars=77,
        description=(
            "Synthetic. Reproduces the construction that caused the original "
            "bug: a whole-session daily aggregate forward-filled onto intraday "
            "bars with no lag."
        ),
    )
}


class TestRegistryCoversFeatureEngine:
    """Every computable feature must carry a declaration."""

    def test_every_feature_engine_feature_is_declared(self):
        from vibe.backtester.analysis.regime_research.features import (
            _FEATURE_REGISTRY as ENGINE_FEATURES,
        )

        undeclared = sorted(set(ENGINE_FEATURES) - set(FEATURE_REGISTRY))
        assert not undeclared, (
            f"FeatureEngine can compute {undeclared} but they have no causality "
            f"declaration. Any feature the research path can produce must be "
            f"classified before it can reach a decision."
        )

    def test_registry_declares_nothing_that_cannot_be_computed(self):
        from vibe.backtester.analysis.regime_research.features import (
            _FEATURE_REGISTRY as ENGINE_FEATURES,
        )

        phantom = sorted(set(FEATURE_REGISTRY) - set(ENGINE_FEATURES))
        assert not phantom, (
            f"Registry declares {phantom}, which FeatureEngine cannot compute. "
            f"A stale declaration is a false assurance."
        )


class TestClassificationIsLocked:
    """Pin the measured classification so a silent reclassification fails CI."""

    def test_known_leaky_features_are_diagnostic(self):
        """The daily-resample leak is fixed, so nothing should remain leaky.

        adx_14, slope_20d and slope_50d were measured against real QQQ 5m bars
        to fail truncation equivalence at a mid-session bar. They were fixed by
        lagging the daily series one session on intraday frames and re-measured
        as causal, so the diagnostic set is now empty.

        This is deliberately asserted as an exact empty tuple rather than
        loosened: a new leaky feature appearing should be a conscious decision
        that updates this test, not something that slips in unnoticed.
        """
        assert diagnostic_feature_names() == ()
        for name in ("adx_14", "slope_20d", "slope_50d"):
            assert FEATURE_REGISTRY[name].kind is FeatureKind.CAUSAL
            assert FEATURE_REGISTRY[name].lookahead_bars == 0

    def test_atr_is_causal(self):
        decl = declaration_for("atr_14")
        assert decl.kind is FeatureKind.CAUSAL
        assert decl.lookahead_bars == 0

    def test_shifted_daily_features_are_causal(self):
        # These also resample to daily, but shift(1) first, so a session only
        # ever sees completed prior sessions. That shift is the whole
        # difference between these and adx_14.
        for name in (
            "prev_day_range",
            "prev_day_trend_pct",
            "prev_close_location",
            "inside_day",
        ):
            assert declaration_for(name).kind is FeatureKind.CAUSAL, name

    def test_rolling_percentiles_are_causal_not_full_sample(self):
        for name in ("atr_pctile", "vol_pctile", "open_vol_pctile"):
            decl = declaration_for(name)
            assert decl.kind is FeatureKind.CAUSAL, name
            assert decl.lookahead_bars == 0, name

    def test_causal_and_diagnostic_partition_the_registry(self):
        assert set(causal_feature_names()) | set(diagnostic_feature_names()) == set(
            FEATURE_REGISTRY
        )
        assert not set(causal_feature_names()) & set(diagnostic_feature_names())


class TestSweepPrecomputeIsContaminated:
    """The sweep's own feature list is the reason P5 exists.

    It originally requested five features, three of which leaked. Those three
    are fixed; these tests now guard against regression rather than documenting
    a live defect.
    """

    def test_sweep_list_matches_parameter_sweep_source(self):
        # If the sweep's list changes, this test must be updated deliberately
        # rather than the registry silently drifting out of sync with it.
        import inspect

        from vibe.backtester.analysis.parameter_sweep import ParameterSweep

        src = inspect.getsource(ParameterSweep._precompute_features)
        for name in SWEEP_PRECOMPUTED_FEATURES:
            assert f'"{name}"' in src, (
                f"{name} is listed in SWEEP_PRECOMPUTED_FEATURES but no longer "
                f"appears in _precompute_features."
            )

    def test_sweep_precompute_is_now_free_of_leaky_features(self):
        leaky = [
            n
            for n in SWEEP_PRECOMPUTED_FEATURES
            if FEATURE_REGISTRY[n].kind is FeatureKind.DIAGNOSTIC
        ]
        assert leaky == [], (
            f"The parameter sweep precomputes {leaky}, which are declared "
            f"diagnostic and are handed straight to the engine. adx_14, "
            f"slope_20d and slope_50d were exactly this problem before the "
            f"one-session lag fixed them."
        )

    def test_sweep_feature_set_is_accepted_by_the_decision_guard(self):
        # Previously this set was refused: three of its five members leaked.
        assert_decision_features_are_causal(
            SWEEP_PRECOMPUTED_FEATURES, context="parameter sweep"
        )


class TestDecisionGuard:
    """The guard must refuse, not warn."""

    def test_causal_features_pass(self):
        assert_decision_features_are_causal(["atr_14", "gap_pct", "prev_day_range"])

    def test_raw_columns_are_exempt(self):
        assert_decision_features_are_causal(
            ["open", "high", "low", "close", "volume", "timestamp", "symbol"]
        )

    def test_atr_14_alias_is_exempt(self):
        # The sweep adds an uppercase ATR_14 alias for the ORB strategy.
        assert_decision_features_are_causal(["ATR_14"])

    def test_diagnostic_feature_is_refused(self):
        """The guard must still fire, even though nothing shipped is leaky.

        Injecting a synthetic diagnostic keeps this test meaningful. Asserting
        against the live registry would silently become a no-op the moment the
        last diagnostic feature was fixed - which is exactly what just
        happened to adx_14, slope_20d and slope_50d.
        """
        with pytest.raises(LeakyFeatureInDecisionError):
            assert_decision_features_are_causal(
                ["atr_14", "leaky_probe"], registry=_REGISTRY_WITH_LEAK
            )

    def test_undeclared_feature_is_refused_not_assumed_safe(self):
        with pytest.raises(FeatureNotDeclaredError) as exc:
            assert_decision_features_are_causal(["atr_14", "my_new_alpha"])
        assert "my_new_alpha" in str(exc.value)

    def test_error_names_the_offending_feature_and_why(self):
        with pytest.raises(LeakyFeatureInDecisionError) as exc:
            assert_decision_features_are_causal(
                ["leaky_probe"], registry=_REGISTRY_WITH_LEAK
            )
        msg = str(exc.value)
        assert "leaky_probe" in msg
        assert "whole-session daily aggregate" in msg

    def test_empty_columns_pass(self):
        assert_decision_features_are_causal([])


class TestDeclarationContract:
    """FeatureDeclaration's own invariant must hold for every entry."""

    def test_causal_features_declare_zero_lookahead(self):
        for name in causal_feature_names():
            assert FEATURE_REGISTRY[name].lookahead_bars == 0, name

    def test_diagnostic_features_declare_nonzero_lookahead(self):
        for name in diagnostic_feature_names():
            assert FEATURE_REGISTRY[name].lookahead_bars > 0, (
                f"{name} is diagnostic but claims zero lookahead. If it truly "
                f"consumes no future data it should be causal; if it does, the "
                f"amount must be stated."
            )

    def test_cannot_declare_a_causal_feature_with_lookahead(self):
        with pytest.raises(ValueError, match="CAUSAL"):
            FeatureDeclaration(
                name="tomorrow_close",
                kind=FeatureKind.CAUSAL,
                lookback_bars=0,
                lookahead_bars=1,
                description="close.shift(-1) mislabelled as causal",
            )

    def test_every_declaration_has_a_description(self):
        for name, decl in FEATURE_REGISTRY.items():
            assert decl.description.strip(), name


class TestRegistryHash:
    """Reclassification must change run identity."""

    def test_hash_is_stable(self):
        assert registry_hash() == registry_hash()

    def test_hash_is_hex(self):
        h = registry_hash()
        assert len(h) == 64
        int(h, 16)

    def test_reclassification_changes_the_hash(self, monkeypatch):
        import vibe.research_pipeline.features.registry as reg

        before = reg.registry_hash()
        patched = dict(reg.FEATURE_REGISTRY)
        patched["adx_14"] = FeatureDeclaration(
            name="adx_14",
            kind=FeatureKind.CAUSAL,
            lookback_bars=14,
            lookahead_bars=0,
            description="wrongly promoted",
        )
        monkeypatch.setattr(reg, "FEATURE_REGISTRY", patched)
        assert reg.registry_hash() != before, (
            "Promoting a diagnostic feature to causal changes what the run was "
            "allowed to see, so it must change the run's identity."
        )


class TestRegistryIsImmutable:
    def test_registry_cannot_be_mutated_at_runtime(self):
        with pytest.raises(TypeError):
            FEATURE_REGISTRY["injected"] = declaration_for("atr_14")
