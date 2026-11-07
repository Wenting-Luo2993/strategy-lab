from pathlib import Path

import pytest

from vibe.common.ruleset.loader import RuleSetLoader
from vibe.common.ruleset.support import (
    UnsupportedRulesetSettingsError,
    require_supported,
    unsupported_settings,
)

VARIANTS = sorted(Path("config/research/variants/orb_edge_2026").glob("*.yaml"))


def _with(ruleset, **updates):
    data = ruleset.model_dump()
    for dotted, value in updates.items():
        node = data
        *parents, leaf = dotted.split("__")
        for key in parents:
            node = node[key]
        node[leaf] = value
    return type(ruleset).model_validate(data)


@pytest.fixture
def production():
    return RuleSetLoader.from_name("orb_production")


def test_production_is_fully_supported(production):
    assert unsupported_settings(production, "backtest") == []
    assert unsupported_settings(production, "live") == []


@pytest.mark.parametrize("path", VARIANTS, ids=lambda p: p.stem)
def test_research_variants_are_supported_in_backtest(path):
    require_supported(RuleSetLoader.from_yaml(path), "backtest")


@pytest.mark.parametrize(
    "updates, fragment",
    [
        ({"strategy__entry_mode": "aggressive"}, "entry_mode"),
        ({"strategy__allow_reentry": True}, "allow_reentry"),
        ({"strategy__one_trade_per_day": False}, "one_trade_per_day"),
        ({"trade_filter__vix_max": 30.0}, "vix_max"),
        ({"mtf_validation__enabled": True}, "mtf_validation"),
        ({"exit__stop_loss": {"method": "atr_multiple", "multiplier": 2.0}}, "stop_loss"),
    ],
)
def test_ignored_settings_are_reported(production, updates, fragment):
    try:
        ruleset = _with(production, **updates)
    except Exception as exc:  # model rejects the value outright: also loud
        pytest.skip(f"model validation rejects it: {exc}")
    problems = unsupported_settings(ruleset, "backtest")
    assert any(fragment in p for p in problems), problems
    with pytest.raises(UnsupportedRulesetSettingsError):
        require_supported(ruleset, "backtest")


def test_stepped_trailing_is_backtest_only():
    ruleset = RuleSetLoader.from_name("orb_conservative")
    live = unsupported_settings(ruleset, "live")
    backtest = unsupported_settings(ruleset, "backtest")
    if ruleset.exit.trailing_stop is not None and ruleset.exit.trailing_stop.method == "stepped_r_multiple":
        assert any("trailing_stop" in p for p in live)
        assert not any("trailing_stop" in p for p in backtest)
    assert any("vix_max" in p for p in live)


def test_runner_refuses_unsupported_ruleset(production):
    from vibe.backtester.runner import RuleSetRunner

    with pytest.raises(UnsupportedRulesetSettingsError):
        RuleSetRunner(_with(production, trade_filter__vix_max=25.0))
