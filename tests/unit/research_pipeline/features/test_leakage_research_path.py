"""F2: truncation equivalence executed through the research path.

Why this file is separate from ``test_leakage.py``
--------------------------------------------------

Section 9 of the plan is explicit: *"These checks must execute through the
research path, not just the engine."* The reason is structural.
``ParameterSweep._precompute_features`` computes features **once over the whole
date range** and slices them per fold afterwards. By the time
``BacktestEngine.run`` receives a frame, any contamination is already baked in
and the engine cannot detect it. A leakage test that exercises only the engine
will therefore pass while the research path leaks.

These tests run against the real Parquet corpus and are marked ``slow``. They
are the fixture that converted the leakage question from a design concern into
a measured finding.

What they found, and what happened next
---------------------------------------

Three of the five features ``_precompute_features`` requests - ``adx_14``,
``slope_20d``, ``slope_50d`` - failed truncation equivalence at a mid-session
bar. They were computed on a daily resample and forward-filled back onto
intraday bars with no lag, so the 09:30 bar of a session carried that session's
*complete* daily value. On real QQQ bars the 09:30 ADX on 2022-05-25 was
33.131858 after the fix and 33.407330 before it - the latter being the daily
ADX computed through that same session's close.

They have since been fixed at the source, by lagging the daily series one
session on intraday frames (``_reindex_causally``), and promoted to CAUSAL in
the registry on the strength of these checks. The tests below are now a
regression guard rather than a conviction.

Because every registered feature is now causal, this file carries a
``leaky_control`` column that deliberately reproduces the original defective
construction. A check suite where nothing can fail proves nothing, so each
check must still convict that control.

The contamination was **latent** rather than live while it existed: poisoning
those three columns to a constant left the ORB backtest bit-identical, because
the ORB decision path does not read them. That made the guard more important,
not less - consuming them is a one-line change in a ruleset, and the failure
would have been silent and flattering.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pandas as pd
import pytest
import pytz

from vibe.research_pipeline.contracts import FeatureKind
from vibe.research_pipeline.features.leakage import (
    check_future_perturbation,
    check_prefix_invariance,
    check_truncation_equivalence,
)
from vibe.research_pipeline.features.registry import (
    FEATURE_REGISTRY,
    SWEEP_PRECOMPUTED_FEATURES,
)

pytestmark = pytest.mark.slow

ET = pytz.timezone("America/New_York")
SYMBOL = "QQQ"
START = ET.localize(datetime(2022, 1, 3))
END = ET.localize(datetime(2022, 6, 30, 23, 59))


def _resolve_data_dir():
    from vibe.backtester.data.paths import resolve_market_data_dir

    return resolve_market_data_dir(None)


@pytest.fixture(scope="module")
def sweep():
    from vibe.backtester.analysis.parameter_sweep import ParameterSweep
    from vibe.common.ruleset.loader import RuleSetLoader

    ruleset_path = Path(RuleSetLoader.RULESETS_DIR) / "orb_production.yaml"
    if not ruleset_path.exists():
        pytest.skip(f"ruleset not available at {ruleset_path}")

    return ParameterSweep(
        base_ruleset_path=ruleset_path,
        parameters=[],
        data_dir=_resolve_data_dir(),
    )


@pytest.fixture(scope="module")
def bars(sweep) -> pd.DataFrame:
    """The same 5-minute frame ``_precompute_features`` computes against."""
    import asyncio

    from vibe.backtester.data.parquet_loader import ParquetLoader

    loader = ParquetLoader(_resolve_data_dir(), [SYMBOL])
    try:
        df_1m = asyncio.run(
            loader.get_bars(SYMBOL, start_time=START, end_time=END)
        )
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"market data unavailable: {exc}")

    if df_1m is None or df_1m.empty:
        pytest.skip("no bars returned for the probe window")

    return (
        df_1m.resample("5min")
        .agg(
            {
                "open": "first",
                "high": "max",
                "low": "min",
                "close": "last",
                "volume": "sum",
            }
        )
        .dropna()
    )


@pytest.fixture(scope="module")
def research_compute():
    """The research path's feature computation, as a plain callable.

    ``_precompute_features`` loads its own bars from Parquet, so it cannot be
    handed a truncated frame directly. This reproduces its exact feature list
    and engine so the check measures what the sweep actually produces.
    """
    from vibe.backtester.analysis.regime_research.features import FeatureEngine

    engine = FeatureEngine()

    def compute(df: pd.DataFrame) -> pd.DataFrame:
        out = engine.compute(df, features=list(SWEEP_PRECOMPUTED_FEATURES))
        if "atr_14" in out.columns:
            out = out.assign(ATR_14=out["atr_14"])
        return out

    return compute


@pytest.fixture(scope="module")
def leaky_compute():
    """A compute callable carrying one deliberately non-causal column.

    This reproduces the exact construction that caused the original bug -
    resample to daily, compute, forward-fill back onto the intraday index with
    no lag - so the leakage checks have something they must still convict now
    that every registered feature is causal. Without it, the checks in this
    file could pass forever while measuring nothing.
    """
    from vibe.backtester.analysis.regime_research.features import FeatureEngine

    engine = FeatureEngine()

    def compute(df: pd.DataFrame) -> pd.DataFrame:
        out = engine.compute(df, features=list(SWEEP_PRECOMPUTED_FEATURES))
        daily_close = df["close"].resample("D").last().dropna()
        # No .shift(1): session D's close lands on session D's 09:30 bar.
        out = out.assign(
            leaky_control=daily_close.reindex(df.index, method="ffill")
        )
        return out

    return compute


def _mid_session_cut(df: pd.DataFrame) -> int:
    """A bar around 11:00, late enough that daily features are primed.

    Cutting at a session boundary would let a whole-session aggregate look
    correct by accident, so the cut must fall strictly inside a session.
    """
    cut = int(len(df) * 0.8)
    for i in range(cut, len(df) - 1):
        if df.index[i].hour == 11 and df.index[i].minute == 0:
            return i
    return cut


class TestResearchPathMatchesTheSweep:
    """Guard against this fixture drifting away from the code it tests."""

    def test_feature_list_is_the_sweeps_own_list(self, research_compute, bars):
        produced = set(research_compute(bars.iloc[:400]).columns)
        assert set(SWEEP_PRECOMPUTED_FEATURES) <= produced

    def test_probe_window_has_enough_sessions_to_prime_daily_features(self, bars):
        sessions = len({ts.date() for ts in bars.index})
        assert sessions >= 60, (
            f"only {sessions} sessions in the probe window; slope_50d needs 50 "
            f"daily observations before it is defined, and a window that "
            f"leaves it NaN would make this whole file vacuous."
        )


class TestF2TruncationEquivalenceThroughResearchPath:
    """F2: sessions 1..N vs 1..k must agree on their shared prefix."""

    def test_causal_features_survive_truncation(self, bars, research_compute):
        cut = _mid_session_cut(bars)
        causal = [
            n
            for n in SWEEP_PRECOMPUTED_FEATURES
            if FEATURE_REGISTRY[n].kind is FeatureKind.CAUSAL
        ]
        report = check_truncation_equivalence(
            bars, research_compute, cut=cut, features=causal
        )
        assert report.passed, (
            f"A feature declared CAUSAL changed when future bars were removed:\n"
            f"{report}"
        )

    def test_the_fixed_features_are_now_causal(self, bars, research_compute):
        """Regression guard for the daily-resample leak.

        ``adx_14``, ``slope_20d`` and ``slope_50d`` were measured to fail
        truncation equivalence here: on real QQQ bars the 09:30 bar of a session
        carried that session's *complete* daily ADX. They were fixed at the
        source by lagging the daily series one session on intraday frames
        (``_reindex_causally``), and promoted to CAUSAL on the strength of this
        check rather than on the assumption the fix worked.

        If this fails, the lag was removed or bypassed and the research path is
        consuming the future again.
        """
        cut = _mid_session_cut(bars)
        fixed = ["adx_14", "slope_20d", "slope_50d"]
        report = check_truncation_equivalence(
            bars, research_compute, cut=cut, features=fixed
        )
        assert report.passed, (
            f"The daily-resample look-ahead leak has returned:\n{report}"
        )

    def test_the_check_still_convicts_a_known_leaky_feature(
        self, bars, leaky_compute
    ):
        """Falsifiability guard — the most important test in this file.

        Every feature in the registry is now causal, so every real check passes.
        A suite of checks that cannot fail proves nothing, and would keep
        reporting green if the comparator silently broke.

        ``leaky_compute`` reproduces the original defective construction -
        resample to daily, forward-fill onto intraday bars, no lag - in a column
        named ``leaky_control``. The check must convict it.
        """
        cut = _mid_session_cut(bars)
        report = check_truncation_equivalence(
            bars, leaky_compute, cut=cut, features=["leaky_control"]
        )
        assert not report.passed, (
            "The truncation check failed to convict a feature built with the "
            "exact construction that caused the original leak. The check is no "
            "longer measuring anything, so every other green result in this "
            "file is worthless."
        )
        assert set(report.features_failing()) == {"leaky_control"}

    def test_the_cut_is_not_at_a_session_boundary(self, bars):
        cut = _mid_session_cut(bars)
        day = bars.index[cut].date()
        same_session = [ts for ts in bars.index if ts.date() == day]
        assert bars.index[cut] != same_session[0], "cut is the first bar of its session"
        assert bars.index[cut] != same_session[-1], "cut is the last bar of its session"

    def test_daily_derived_features_are_not_nan_at_the_cut(
        self, bars, research_compute
    ):
        """Non-vacuity guard: a NaN would compare equal and prove nothing."""
        cut = _mid_session_cut(bars)
        full = research_compute(bars)
        for name in ("adx_14", "slope_20d", "slope_50d"):
            assert pd.notna(full[name].iloc[cut]), (
                f"{name} is NaN at the cut, so the truncation comparison would "
                f"be trivially satisfied and the test would assert nothing."
            )

    def test_the_lagged_value_equals_the_prior_session(self, bars, research_compute):
        """Pin the fix's actual semantics, not merely that a check is green.

        A feature could pass truncation equivalence by being constant, or NaN,
        or stale by ten sessions. The specific contract is one session of lag,
        so assert exactly that: a bar of session D must carry the daily value
        computed through session D-1's close.
        """
        from vibe.backtester.analysis.regime_research.features import FeatureEngine

        full = research_compute(bars)
        daily = (
            bars.resample("D")
            .agg(
                {
                    "open": "first",
                    "high": "max",
                    "low": "min",
                    "close": "last",
                    "volume": "sum",
                }
            )
            .dropna()
        )
        daily_feat = FeatureEngine().compute(daily, features=["adx_14"])

        sessions = sorted({ts.date() for ts in bars.index})
        # Late enough that the daily ADX is primed well past its warmup.
        target = sessions[-5]
        prior = sessions[-6]

        first_bar = min(ts for ts in bars.index if ts.date() == target)
        intraday_value = full["adx_14"].loc[first_bar]
        prior_daily = daily_feat["adx_14"].loc[str(prior)]

        assert pd.notna(intraday_value) and pd.notna(prior_daily)
        assert intraday_value == pytest.approx(prior_daily, rel=1e-9), (
            f"The first bar of {target} carries {intraday_value}, but the "
            f"prior session {prior} closed with {prior_daily}. The lag is not "
            f"exactly one session."
        )


class TestF2OtherChecksAgree:
    """Truncation, prefix invariance, and perturbation must convict together."""

    def test_prefix_invariance_still_convicts_a_leaky_feature(
        self, bars, leaky_compute
    ):
        cut = _mid_session_cut(bars)
        report = check_prefix_invariance(
            bars, leaky_compute, cut=cut, features=["leaky_control"]
        )
        assert not report.passed, (
            "check_prefix_invariance no longer detects a feature built with "
            "the original leaky construction."
        )

    def test_future_perturbation_still_convicts_a_leaky_feature(
        self, bars, leaky_compute
    ):
        cut = _mid_session_cut(bars)
        report = check_future_perturbation(
            bars, leaky_compute, cut=cut, features=["leaky_control"]
        )
        assert not report.passed, (
            "check_future_perturbation no longer detects a feature built with "
            "the original leaky construction."
        )

    def test_all_three_checks_clear_the_fixed_features(self, bars, research_compute):
        """The fix must satisfy every check, not just the one that found it."""
        cut = _mid_session_cut(bars)
        for check in (
            check_truncation_equivalence,
            check_prefix_invariance,
            check_future_perturbation,
        ):
            report = check(
                bars,
                research_compute,
                cut=cut,
                features=["adx_14", "slope_20d", "slope_50d"],
            )
            assert report.passed, f"{check.__name__} still convicts:\n{report}"

    def test_atr_survives_all_three(self, bars, research_compute):
        cut = _mid_session_cut(bars)
        for check in (
            check_truncation_equivalence,
            check_prefix_invariance,
            check_future_perturbation,
        ):
            report = check(bars, research_compute, cut=cut, features=["atr_14"])
            assert report.passed, f"{check.__name__} failed for atr_14:\n{report}"


class TestContaminationIsLatentNotLive:
    """Pin the live-versus-latent finding so a regression is visible.

    The leaky columns are handed to ``BacktestEngine.run`` on every sweep. Today
    nothing reads them, so results are unaffected. This test records that fact;
    if it ever fails, the leak has gone live and stored results are suspect.
    """

    def test_poisoning_leaky_features_does_not_change_the_backtest(self, sweep):
        from vibe.backtester.core.engine import BacktestEngine
        from vibe.common.ruleset.loader import RuleSetLoader

        ruleset = RuleSetLoader.from_name("orb_production")
        data_dir = _resolve_data_dir()

        features = sweep._precompute_features(SYMBOL, START, END, "5m")

        def run(frame):
            engine = BacktestEngine(
                ruleset=ruleset, data_dir=data_dir, initial_capital=100_000
            )
            return engine.run(
                symbol=SYMBOL,
                start_date=START,
                end_date=END,
                precomputed_features=frame,
            )

        def signature(result):
            return (
                len(result.trades),
                round(result.overall.total_pnl, 6),
                round(result.overall.expectancy_r, 6),
            )

        baseline = signature(run(features))

        poisoned = features.copy()
        for name in ("adx_14", "slope_20d", "slope_50d"):
            poisoned[name] = -999.0

        assert signature(run(poisoned)) == baseline, (
            "Poisoning the leaky features changed the backtest, which means the "
            "ORB decision path now consumes adx_14, slope_20d, or slope_50d. "
            "The look-ahead leak is live: stored results computed through "
            "ParameterSweep are contaminated and must be re-baselined, and the "
            "features must be fixed or removed from the precompute list."
        )

    def test_baseline_run_produces_trades(self, sweep):
        """Non-vacuity: a zero-trade run would make the comparison meaningless."""
        from vibe.backtester.core.engine import BacktestEngine
        from vibe.common.ruleset.loader import RuleSetLoader

        result = BacktestEngine(
            ruleset=RuleSetLoader.from_name("orb_production"),
            data_dir=_resolve_data_dir(),
            initial_capital=100_000,
        ).run(
            symbol=SYMBOL,
            start_date=START,
            end_date=END,
            precomputed_features=sweep._precompute_features(
                SYMBOL, START, END, "5m"
            ),
        )
        assert len(result.trades) > 0
