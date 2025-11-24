"""Stage 2 snapshot core utilities.

Provides config hashing, snapshot comparison, and textual diff reporting.
"""
from __future__ import annotations

import json
import hashlib
from dataclasses import dataclass
from typing import Any, Dict, List, Sequence

import pandas as pd

from .snapshot_utils import NUMERIC_ABS_TOL, normalize_numeric


def _canonical_json(obj: Any) -> str:
    """Return deterministic JSON string (sorted keys, no whitespace)."""

    return json.dumps(obj, sort_keys=True, separators=(",", ":"))

def hash_config(*configs: Dict[str, Any]) -> str:
    """Hash one or more config dicts producing a stable SHA256.

    Each dict is normalized via canonical JSON and concatenated with a newline
    delimiter to minimize accidental collisions from adjacency.
    """
    parts = []
    for config in configs:
        if not isinstance(config, dict):
            raise TypeError("hash_config expects dict arguments")
        parts.append(_canonical_json(config))
    blob = "\n".join(parts).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


@dataclass
class SnapshotDiff:
    """Structured diff result between expected and actual snapshots.

    expected_df / actual_df retain the (post-sort, pre-diff) DataFrames used for
    comparison and enable downstream visualization (e.g., overlay signals on
    OHLCV). They are the normalized versions (numeric rounding applied) to
    ensure consistency with tolerance decisions.
    """
    row_count_equal: bool
    expected_rows: int
    actual_rows: int
    missing_rows: int
    extra_rows: int
    differing_cells: List[Dict[str, Any]]  # each: {row_index, column, expected, actual, abs_diff}
    columns_equal: bool
    within_tolerance: bool
    expected_df: pd.DataFrame
    actual_df: pd.DataFrame
    is_equal: bool  # strict overall equality (schema, counts, values within tolerance)


def compare_snapshots(
    expected: pd.DataFrame,
    actual: pd.DataFrame,
    numeric_tol: float = NUMERIC_ABS_TOL,
    max_diffs: int = 50,
    compare_columns: Sequence[str] | None = None,
) -> SnapshotDiff:
    """Compare two snapshot DataFrames with optional column filtering.

    Args:
        expected: Expected DataFrame from snapshot file.
        actual: Actual DataFrame from test execution.
        numeric_tol: Absolute tolerance for numeric comparisons.
        max_diffs: Maximum number of differences to collect.
        compare_columns: If specified, only compare these columns. Otherwise compare all.
    """
    # Normalize but do NOT reorder; rely on caller producing identical ordering.
    expected_work = normalize_numeric(expected.copy())
    actual_work = normalize_numeric(actual.copy())

    # Normalize indices: convert datetime indices to comparable format
    if isinstance(expected_work.index, pd.DatetimeIndex) and isinstance(actual_work.index, pd.DatetimeIndex):
        # Remove timezone info for comparison if both have it (or one is naive)
        expected_work.index = pd.DatetimeIndex(expected_work.index.tz_localize(None) if expected_work.index.tz else expected_work.index)
        actual_work.index = pd.DatetimeIndex(actual_work.index.tz_localize(None) if actual_work.index.tz else actual_work.index)

    # Structural checks first: length and index must match exactly.
    if len(expected_work) != len(actual_work):
        raise AssertionError(f"Snapshot length mismatch: expected {len(expected_work)} rows, actual {len(actual_work)} rows")
    if not expected_work.index.equals(actual_work.index):
        raise AssertionError("Snapshot index mismatch: indices differ")

    cols_equal = list(expected_work.columns) == list(actual_work.columns)
    # Determine columns to compare
    if compare_columns:
        # Filter to specified columns that exist in both DataFrames
        compare_cols = [c for c in compare_columns if c in expected_work.columns and c in actual_work.columns]
    else:
        # Use intersection for cell comparison if schema drift occurred
        compare_cols = [c for c in expected_work.columns if c in actual_work.columns]

    differing_cells: List[Dict[str, Any]] = []
    within_tolerance = True
    for i in range(len(expected_work)):
        erow = expected_work.iloc[i]
        arow = actual_work.iloc[i]
        for col in compare_cols:
            ev = erow[col]
            av = arow[col]
            if pd.isna(ev) and pd.isna(av):
                continue
            if ev == av:
                continue
            # Numeric tolerance check
            if isinstance(ev, (int, float)) and isinstance(av, (int, float)):
                diff = abs(ev - av)
                if diff <= numeric_tol:
                    continue
                else:
                    within_tolerance = False
                    differing_cells.append({
                        "row_index": expected_work.index[i],
                        "column": col,
                        "expected": ev,
                        "actual": av,
                        "abs_diff": diff,
                    })
            else:
                within_tolerance = False
                differing_cells.append({
                    "row_index": expected_work.index[i],
                    "column": col,
                    "expected": ev,
                    "actual": av,
                    "abs_diff": None,
                })
            if len(differing_cells) >= max_diffs:
                break
        if len(differing_cells) >= max_diffs:
            break

    missing_rows = max(0, len(expected_work) - len(actual_work))
    extra_rows = max(0, len(actual_work) - len(expected_work))
    base_within = within_tolerance and not differing_cells and missing_rows == 0 and extra_rows == 0
    is_equal = base_within and cols_equal
    return SnapshotDiff(
        row_count_equal=len(expected_work) == len(actual_work),
        expected_rows=len(expected_work),
        actual_rows=len(actual_work),
        missing_rows=missing_rows,
        extra_rows=extra_rows,
        differing_cells=differing_cells,
        columns_equal=cols_equal,
        within_tolerance=base_within,
        expected_df=expected,
        actual_df=actual,
        is_equal=is_equal,
    )


def render_diff_report(diff: SnapshotDiff, title: str = "Snapshot Diff", limit: int = 10) -> str:
    lines = [f"=== {title} ==="]
    lines.append(
        f"Rows expected={diff.expected_rows} actual={diff.actual_rows} missing={diff.missing_rows} extra={diff.extra_rows}"
    )
    if not diff.columns_equal:
        lines.append("Column sets differ.")
    if diff.differing_cells:
        lines.append("First discrepancies:")
        for entry in diff.differing_cells[:limit]:
            lines.append(
                f"row={entry['row_index']} col={entry['column']} expected={entry['expected']} actual={entry['actual']} diff={entry['abs_diff']}"
            )
    else:
        lines.append("No cell-level discrepancies beyond tolerance.")
    if diff.within_tolerance:
        lines.append("Result: PASS (within tolerance and counts match)")
    else:
        lines.append("Result: FAIL (differences detected; run --update-snapshots if intentional)")
    return "\n".join(lines)


__all__ = ["hash_config", "compare_snapshots", "render_diff_report", "SnapshotDiff"]
