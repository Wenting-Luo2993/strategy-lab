"""V5: lookahead canaries."""

import numpy as np
import pandas as pd
import pytest

from vibe.research_pipeline.contracts import FeatureDeclaration, FeatureKind
from vibe.research_pipeline.features.registry import (
    FEATURE_REGISTRY,
    LeakyFeatureInDecisionError,
    assert_decision_features_are_causal,
)
from vibe.research_pipeline.inference import Observation, estimate_mean
from vibe.research_pipeline.session_features import build_session_features
from vibe.research_pipeline.validation_suite.synthetic import tick_walk_sessions


def test_registry_rejects_diagnostic_feature():
    # Every shipped feature is causal, so inject a leaky one to keep the guard exercised.
    leaky = FeatureDeclaration(
        name="leaky_probe",
        kind=FeatureKind.DIAGNOSTIC,
        lookback_bars=1,
        lookahead_bars=77,
        description="Synthetic whole-session aggregate forward-filled without lag.",
    )
    registry = dict(FEATURE_REGISTRY) | {"leaky_probe": leaky}
    with pytest.raises(LeakyFeatureInDecisionError):
        assert_decision_features_are_causal(["atr_14", "leaky_probe"], registry=registry)
    assert_decision_features_are_causal(["atr_14"])


def test_session_features_ignore_data_after_the_opening_range():
    bars = tick_walk_sessions(n_sessions=60, seed=3)
    full = build_session_features(bars)
    rng = np.random.default_rng(5)
    for pos in rng.choice(len(full) - 1, size=8, replace=False):
        session = full.index[pos]
        cut = pd.Timestamp(session + " 09:35", tz="America/New_York")
        corrupted = bars.copy()
        mask = corrupted.index >= cut
        corrupted.loc[mask, ["open", "high", "low", "close"]] = rng.uniform(
            1, 500, size=(int(mask.sum()), 4)
        )
        again = build_session_features(corrupted)
        pd.testing.assert_series_equal(full.loc[session], again.loc[session], check_names=False)


def test_cheating_strategy_is_detected_on_null_data():
    """A rule that peeks at the session's final close must light up the estimator."""
    bars = tick_walk_sessions(n_sessions=150, seed=11)
    observations = []
    for day, session in bars.groupby(bars.index.date):
        or_bar = session.iloc[0]
        entry = session.iloc[1]["close"]
        peek = session.iloc[-1]["close"]
        direction = 1.0 if peek > entry else -1.0
        stop = or_bar["low"] if direction > 0 else or_bar["high"]
        risk = abs(entry - stop)
        if risk <= 0 or peek == entry:
            continue
        observations.append(Observation(direction * (peek - entry) / risk, cluster=day))
    est = estimate_mean(observations, confidence_level=0.95)
    assert est.mean_r > 0.5
    assert est.ci_low_r > 0
