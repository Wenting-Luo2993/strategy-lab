from __future__ import annotations

from src.utils.snapshot_core import hash_config


def test_hash_config_invariance_order():
    a = {"alpha": 1, "beta": 2, "nested": {"z": 3, "a": 4}}
    b = {"beta": 2, "nested": {"a": 4, "z": 3}, "alpha": 1}
    assert hash_config(a) == hash_config(b)


def test_hash_config_multiple_concat():
    strategy_config = {"name": "orb", "param": 5}
    risk = {"max_risk_pct": 0.01, "stop_type": "atr"}
    h1 = hash_config(strategy_config, risk)
    h2 = hash_config(risk, strategy_config)  # different order should produce different hash to reflect ordering
    assert h1 != h2
    # But hashing combined canonical join order stable for identical arg ordering
    assert h1 == hash_config(strategy_config, risk)


def test_hash_config_type_error():
    try:
        hash_config({"a": 1}, [1,2,3])  # invalid second arg
    except TypeError:
        return
    assert False, "TypeError not raised for non-dict argument"
