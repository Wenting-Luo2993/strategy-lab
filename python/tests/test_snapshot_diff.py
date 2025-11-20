from __future__ import annotations

import pandas as pd

import pytest
from src.utils.snapshot_core import compare_snapshots, render_diff_report


def _df(rows):
    return pd.DataFrame(rows)


def test_compare_snapshots_exact_match():
    expected = _df([
        {"timestamp": 1, "ticker": "A", "value": 10.0},
        {"timestamp": 2, "ticker": "A", "value": 11.0},
    ])
    actual = expected.copy()
    diff = compare_snapshots(expected, actual, sort_keys=["timestamp", "ticker"])
    assert diff.within_tolerance
    report = render_diff_report(diff)
    assert "PASS" in report


def test_compare_snapshots_numeric_tolerance():
    expected = _df([
        {"timestamp": 1, "ticker": "A", "value": 10.0000001},
    ])
    actual = _df([
        {"timestamp": 1, "ticker": "A", "value": 10.0000002},
    ])
    diff = compare_snapshots(expected, actual, sort_keys=["timestamp", "ticker"])
    assert diff.within_tolerance, "Difference should be within tolerance"


def test_compare_snapshots_exceeds_tolerance():
    expected = _df([
        {"timestamp": 1, "ticker": "A", "value": 10.0},
    ])
    actual = _df([
        {"timestamp": 1, "ticker": "A", "value": 10.01},
    ])
    diff = compare_snapshots(expected, actual, sort_keys=["timestamp", "ticker"], numeric_tol=1e-4)
    assert not diff.within_tolerance
    assert diff.differing_cells
    report = render_diff_report(diff)
    assert "FAIL" in report


def test_compare_snapshots_missing_row_raises():
    expected = _df([
        {"timestamp": 1, "ticker": "A", "value": 10.0},
        {"timestamp": 2, "ticker": "A", "value": 11.0},
    ])
    actual = _df([
        {"timestamp": 1, "ticker": "A", "value": 10.0},
    ])
    with pytest.raises(AssertionError):
        compare_snapshots(expected, actual)


def test_compare_snapshots_extra_row_raises():
    expected = _df([
        {"timestamp": 1, "ticker": "A", "value": 10.0},
    ])
    actual = _df([
        {"timestamp": 1, "ticker": "A", "value": 10.0},
        {"timestamp": 2, "ticker": "A", "value": 11.0},
    ])
    with pytest.raises(AssertionError):
        compare_snapshots(expected, actual)
