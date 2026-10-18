"""E7: every multi-run analysis must thread its execution model to the engine.

Before this was wired, research runners constructed ``BacktestEngine`` without
``execution_realism``. The engine defaults to ``legacy()``, so every sweep or
segment trial silently ran the optimistic model -- pinned stop-trigger entries,
no commission, no exit slippage, no buying-power enforcement.

These tests spy on engine construction rather than asserting on the runner's
own attribute, because the bug was never a missing attribute: it was an
attribute that existed and was never passed on.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from vibe.research_pipeline import parameter_sweep as sweep_mod
from vibe.backtester.core.execution_realism import (
    EntryFillPolicy,
    ExecutionRealismConfig,
)


class _EngineSpy:
    """Records the kwargs each runner builds its engine with."""

    calls: list[dict] = []

    def __init__(self, **kwargs):
        type(self).calls.append(kwargs)

    def run(self, **_kwargs):  # pragma: no cover - runners stop before this
        raise AssertionError("spy should not actually run a backtest")


@pytest.fixture
def engine_spy(monkeypatch):
    _EngineSpy.calls = []

    def install(module):
        monkeypatch.setattr(module, "BacktestEngine", _EngineSpy)

    return install, _EngineSpy


def _realism_of(call: dict) -> ExecutionRealismConfig:
    assert "execution_realism" in call, (
        "engine was constructed without execution_realism, so it silently "
        "fell back to the optimistic legacy() model"
    )
    return call["execution_realism"]


def test_parameter_sweep_defaults_to_the_realistic_model(tmp_path):
    ruleset = _write_min_ruleset(tmp_path)
    s = sweep_mod.ParameterSweep(
        base_ruleset_path=ruleset, data_dir=tmp_path, parameters=[]
    )
    assert s.execution_realism.entry_fill_policy is EntryFillPolicy.AT_NEXT_BAR_OPEN
    assert s.execution_realism.enforce_buying_power is True


def test_segment_executor_defaults_to_realistic_but_respects_an_override():
    from vibe.research_pipeline.optimization import RulesetSegmentExecutor

    default = RulesetSegmentExecutor(base_ruleset={}, parameter_paths={})
    realism = default._engine_kwargs["execution_realism"]
    assert realism.entry_fill_policy is EntryFillPolicy.AT_NEXT_BAR_OPEN

    legacy = ExecutionRealismConfig.legacy()
    overridden = RulesetSegmentExecutor(
        base_ruleset={},
        parameter_paths={},
        engine_kwargs={"execution_realism": legacy},
    )
    assert overridden._engine_kwargs["execution_realism"] is legacy


def test_sweep_passes_its_realism_to_the_engine(tmp_path, engine_spy):
    install, spy = engine_spy
    install(sweep_mod)
    ruleset = _write_min_ruleset(tmp_path)
    legacy = ExecutionRealismConfig.legacy()
    s = sweep_mod.ParameterSweep(
        base_ruleset_path=ruleset,
        data_dir=tmp_path,
        parameters=[],
        execution_realism=legacy,
    )
    s._build_engine(object())
    assert spy.calls, "sweep never constructed an engine"
    assert _realism_of(spy.calls[0]) is legacy


def test_sweep_cache_key_separates_execution_models(tmp_path):
    """A cached legacy() result must not be served to a realistic() sweep."""
    ruleset = _write_min_ruleset(tmp_path)
    common = dict(base_ruleset_path=ruleset, data_dir=tmp_path, parameters=[])
    args = ({"a": 1}, "QQQ", datetime(2022, 1, 1), datetime(2022, 12, 31))

    realistic_key = sweep_mod.ParameterSweep(**common)._cache_key(*args)
    legacy_key = sweep_mod.ParameterSweep(
        **common, execution_realism=ExecutionRealismConfig.legacy()
    )._cache_key(*args)

    assert realistic_key != legacy_key


def _write_min_ruleset(tmp_path: Path) -> Path:
    path = tmp_path / "ruleset.yaml"
    path.write_text("name: test\n")
    return path
