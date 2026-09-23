"""The automated leakage checks from section 9 of the plan.

Each check answers one question and reports evidence rather than a bare
boolean, because "leakage detected" is only actionable if it names the feature
and the timestamp.

The design constraint that shapes this module
---------------------------------------------

``ParameterSweep._precompute_features`` computes features **once over the whole
date range** and slices them per fold afterwards. A truncation-equivalence test
that only exercises ``BacktestEngine.run`` therefore proves nothing about the
path research actually uses: the engine receives an already-contaminated frame
and has no way to tell. :func:`check_truncation_equivalence` accepts a
``compute`` callable precisely so it can be pointed at the sweep's precompute
step, and :func:`run_leakage_suite` does exactly that.

A check that cannot fail is worse than no check, because it converts an open
question into a false assurance. Every check here is paired with a fixture in
``tests/unit/research_pipeline/features/`` that makes it fail on purpose.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Mapping, Sequence

import numpy as np
import pandas as pd

from vibe.research_pipeline.contracts import FeatureKind
from vibe.research_pipeline.features.registry import (
    FEATURE_REGISTRY,
    declaration_for,
)

#: Comparison tolerance. Feature recomputation is deterministic, so anything
#: above floating-point noise is a real difference, not a rounding artifact.
_RTOL = 1e-9
_ATOL = 1e-12


class LeakageViolation(AssertionError):
    """Raised when a leakage check fails and the caller asked to enforce."""


@dataclass(frozen=True)
class LeakageFinding:
    """One feature's result under one check."""

    check: str
    feature: str
    passed: bool
    detail: str
    first_divergence: pd.Timestamp | None = None
    full_value: float | None = None
    restricted_value: float | None = None

    def __str__(self) -> str:  # pragma: no cover - formatting only
        status = "PASS" if self.passed else "FAIL"
        return f"[{status}] {self.check}/{self.feature}: {self.detail}"


@dataclass(frozen=True)
class LeakageReport:
    """Aggregated findings, with enough detail to act on a failure."""

    findings: tuple[LeakageFinding, ...] = field(default_factory=tuple)

    @property
    def passed(self) -> bool:
        return all(f.passed for f in self.findings)

    @property
    def failures(self) -> tuple[LeakageFinding, ...]:
        return tuple(f for f in self.findings if not f.passed)

    def features_failing(self) -> tuple[str, ...]:
        return tuple(sorted({f.feature for f in self.failures}))

    def raise_if_failed(self) -> None:
        """Raise :class:`LeakageViolation` naming every failure."""
        if self.passed:
            return
        lines = [str(f) for f in self.failures]
        raise LeakageViolation(
            f"{len(self.failures)} leakage check(s) failed:\n  "
            + "\n  ".join(lines)
        )

    def __str__(self) -> str:  # pragma: no cover - formatting only
        return "\n".join(str(f) for f in self.findings)


def _compare_at(
    check: str,
    feature: str,
    full: pd.Series,
    restricted: pd.Series,
    index: pd.Index,
    detail_ok: str,
) -> LeakageFinding:
    """Compare two computations of one feature over a shared index."""
    a = full.reindex(index)
    b = restricted.reindex(index)

    both_nan = a.isna() & b.isna()
    close = pd.Series(False, index=index)
    valid = a.notna() & b.notna()
    if valid.any():
        close.loc[valid] = np.isclose(
            a[valid].to_numpy(dtype=float),
            b[valid].to_numpy(dtype=float),
            rtol=_RTOL,
            atol=_ATOL,
        )

    same = both_nan | close
    if bool(same.all()):
        return LeakageFinding(check, feature, True, detail_ok)

    first = index[~same][0]
    return LeakageFinding(
        check,
        feature,
        False,
        (
            f"value at {first} depends on data after that timestamp "
            f"({a.loc[first]!r} with future bars present, {b.loc[first]!r} "
            f"without). The feature is not observable when it claims to be."
        ),
        first_divergence=first,
        full_value=None if pd.isna(a.loc[first]) else float(a.loc[first]),
        restricted_value=None if pd.isna(b.loc[first]) else float(b.loc[first]),
    )


def check_truncation_equivalence(
    df: pd.DataFrame,
    compute: Callable[[pd.DataFrame], pd.DataFrame],
    *,
    cut: int,
    features: Sequence[str] | None = None,
) -> LeakageReport:
    """A run truncated at ``t`` must match the prefix of a full run through ``t``.

    This is check 3 in section 9, and it is the one that catches the
    daily-resample-then-forward-fill construction, because that construction
    only differs when bars after ``t`` exist.

    Args:
        df: Full bar frame, indexed by timestamp.
        compute: Feature computation to test. Point this at the *research*
            path's precompute step, not at the engine.
        cut: Positional index of the timestamp to compare at. Choose a
            mid-session bar; comparing at a session boundary can hide the leak.
        features: Restrict to these columns. Defaults to everything ``compute``
            returns.

    Returns:
        A report with one finding per feature.
    """
    if not 0 <= cut < len(df):
        raise ValueError(f"cut={cut} is outside the frame (len={len(df)})")

    full = compute(df)
    restricted = compute(df.iloc[: cut + 1])

    names = list(features) if features is not None else list(full.columns)
    at = df.index[[cut]]

    findings = []
    for name in names:
        if name not in full.columns or name not in restricted.columns:
            continue
        findings.append(
            _compare_at(
                "truncation_equivalence",
                name,
                full[name],
                restricted[name],
                at,
                f"identical at {df.index[cut]} with and without future bars",
            )
        )
    return LeakageReport(tuple(findings))


def check_prefix_invariance(
    df: pd.DataFrame,
    compute: Callable[[pd.DataFrame], pd.DataFrame],
    *,
    cut: int,
    features: Sequence[str] | None = None,
) -> LeakageReport:
    """Changing bars after ``t`` cannot alter features at or before ``t``.

    This is check 1 in section 9. It differs from truncation equivalence in an
    important way: the later bars still *exist*, they are merely different. A
    feature that reads a whole-session aggregate will move even though the
    frame's shape is unchanged, so this catches leaks that a length-sensitive
    implementation might survive.
    """
    if not 0 <= cut < len(df):
        raise ValueError(f"cut={cut} is outside the frame (len={len(df)})")

    baseline = compute(df)

    mutated = df.copy()
    tail = mutated.index[cut + 1 :]
    if len(tail) == 0:
        raise ValueError(
            "cut leaves no bars after it, so prefix invariance is vacuous. "
            "Choose a cut with future bars available."
        )
    for col in ("open", "high", "low", "close"):
        if col in mutated.columns:
            mutated.loc[tail, col] = mutated.loc[tail, col] * 3.0
    if "volume" in mutated.columns:
        mutated.loc[tail, "volume"] = mutated.loc[tail, "volume"] * 7.0

    perturbed = compute(mutated)

    names = list(features) if features is not None else list(baseline.columns)
    prefix = df.index[: cut + 1]

    findings = []
    for name in names:
        if name not in baseline.columns or name not in perturbed.columns:
            continue
        findings.append(
            _compare_at(
                "prefix_invariance",
                name,
                baseline[name],
                perturbed[name],
                prefix,
                f"unchanged through {df.index[cut]} when later bars were scaled",
            )
        )
    return LeakageReport(tuple(findings))


def check_future_perturbation(
    df: pd.DataFrame,
    compute: Callable[[pd.DataFrame], pd.DataFrame],
    *,
    cut: int,
    features: Sequence[str] | None = None,
    extreme: float = 1e6,
) -> LeakageReport:
    """Replace future values with extremes; prior values must be identical.

    This is check 2 in section 9. It is the sharpest of the three because an
    extreme value makes even a small contamination numerically obvious, and it
    is the check that would catch a feature that happens to be insensitive to
    the modest scaling used by prefix invariance.
    """
    if not 0 <= cut < len(df):
        raise ValueError(f"cut={cut} is outside the frame (len={len(df)})")

    baseline = compute(df)

    mutated = df.copy()
    tail = mutated.index[cut + 1 :]
    if len(tail) == 0:
        raise ValueError(
            "cut leaves no bars after it, so future perturbation is vacuous."
        )
    for col in ("open", "close"):
        if col in mutated.columns:
            mutated.loc[tail, col] = extreme
    if "high" in mutated.columns:
        mutated.loc[tail, "high"] = extreme * 1.01
    if "low" in mutated.columns:
        mutated.loc[tail, "low"] = extreme * 0.99
    if "volume" in mutated.columns:
        mutated.loc[tail, "volume"] = extreme

    perturbed = compute(mutated)

    names = list(features) if features is not None else list(baseline.columns)
    prefix = df.index[: cut + 1]

    findings = []
    for name in names:
        if name not in baseline.columns or name not in perturbed.columns:
            continue
        findings.append(
            _compare_at(
                "future_perturbation",
                name,
                baseline[name],
                perturbed[name],
                prefix,
                f"unchanged through {df.index[cut]} under extreme future values",
            )
        )
    return LeakageReport(tuple(findings))


def audit_feature_availability(
    columns: Sequence[str], *, decision_context: str = "signal generation"
) -> LeakageReport:
    """Reject causal-looking columns whose declaration says otherwise.

    This is check 4 in section 9, and it is a pure declaration audit: it does
    not compute anything. It exists so that a feature the measured checks have
    already convicted cannot quietly reappear in a decision path later.
    """
    findings = []
    for name in columns:
        try:
            decl = declaration_for(name)
        except KeyError:
            findings.append(
                LeakageFinding(
                    "feature_availability",
                    name,
                    False,
                    (
                        f"undeclared, so it may not enter {decision_context}. "
                        f"Declare it causal or diagnostic first."
                    ),
                )
            )
            continue

        if decl.kind is FeatureKind.DIAGNOSTIC:
            findings.append(
                LeakageFinding(
                    "feature_availability",
                    name,
                    False,
                    (
                        f"declared DIAGNOSTIC ({decl.lookahead_bars} bars of "
                        f"lookahead) and may not enter {decision_context}. "
                        f"{decl.description}"
                    ),
                )
            )
        else:
            findings.append(
                LeakageFinding(
                    "feature_availability",
                    name,
                    True,
                    f"declared CAUSAL with {decl.lookback_bars} bars of lookback",
                )
            )
    return LeakageReport(tuple(findings))


def check_orb_boundary(
    bars: pd.DataFrame,
    or_high: float,
    or_low: float,
    *,
    session_open: pd.Timestamp,
    or_end: pd.Timestamp,
    breakout_time: pd.Timestamp,
) -> LeakageReport:
    """The breakout bar must not contribute to the range it is tested against.

    This is check 5 in section 9. The failure it guards against is subtle and
    self-fulfilling: if the breakout bar's own high is folded into the opening
    range, the range expands to contain it and the breakout can never be
    detected — or, with the comparison inverted, is always detected. Either way
    the signal is measuring itself.
    """
    findings = []

    if breakout_time < or_end:
        findings.append(
            LeakageFinding(
                "orb_boundary",
                "breakout_time",
                False,
                (
                    f"breakout at {breakout_time} falls inside the opening "
                    f"range window (ends {or_end}), so the bar that triggers "
                    f"the signal is part of the range it is compared to."
                ),
                first_divergence=breakout_time,
            )
        )
    else:
        findings.append(
            LeakageFinding(
                "orb_boundary",
                "breakout_time",
                True,
                f"breakout at {breakout_time} is at or after the range close {or_end}",
            )
        )

    window = bars.loc[(bars.index >= session_open) & (bars.index < or_end)]
    if window.empty:
        findings.append(
            LeakageFinding(
                "orb_boundary",
                "or_window",
                False,
                (
                    f"no bars between {session_open} and {or_end}; the opening "
                    f"range is undefined and any breakout against it is "
                    f"meaningless rather than merely wrong."
                ),
            )
        )
        return LeakageReport(tuple(findings))

    expected_high = float(window["high"].max())
    expected_low = float(window["low"].min())

    if not np.isclose(or_high, expected_high, rtol=_RTOL, atol=_ATOL):
        findings.append(
            LeakageFinding(
                "orb_boundary",
                "or_high",
                False,
                (
                    f"declared or_high={or_high!r} but bars strictly inside the "
                    f"window give {expected_high!r}. The range includes at "
                    f"least one bar it should not."
                ),
                full_value=or_high,
                restricted_value=expected_high,
            )
        )
    else:
        findings.append(
            LeakageFinding(
                "orb_boundary", "or_high", True, "matches bars inside the window only"
            )
        )

    if not np.isclose(or_low, expected_low, rtol=_RTOL, atol=_ATOL):
        findings.append(
            LeakageFinding(
                "orb_boundary",
                "or_low",
                False,
                (
                    f"declared or_low={or_low!r} but bars strictly inside the "
                    f"window give {expected_low!r}."
                ),
                full_value=or_low,
                restricted_value=expected_low,
            )
        )
    else:
        findings.append(
            LeakageFinding(
                "orb_boundary", "or_low", True, "matches bars inside the window only"
            )
        )

    return LeakageReport(tuple(findings))


def check_split_contamination(
    *,
    train_sessions: Sequence[pd.Timestamp] | Sequence[object],
    test_sessions: Sequence[pd.Timestamp] | Sequence[object],
    selection_sessions: Sequence[pd.Timestamp] | Sequence[object] | None = None,
) -> LeakageReport:
    """Test sessions must be absent from optimization and selection inputs.

    This is check 6 in section 9. The plan requires naming the *specific*
    overlapping session, not merely reporting that an overlap exists, because a
    single shared session and a wholly duplicated split need very different
    responses.
    """
    train = set(train_sessions)
    test = set(test_sessions)

    findings = []

    overlap = sorted(train & test, key=str)
    if overlap:
        shown = ", ".join(str(s) for s in overlap[:5])
        more = f" (and {len(overlap) - 5} more)" if len(overlap) > 5 else ""
        findings.append(
            LeakageFinding(
                "split_contamination",
                "train_vs_test",
                False,
                (
                    f"{len(overlap)} session(s) appear in both train and test: "
                    f"{shown}{more}. Optimization can see the sessions it is "
                    f"scored on."
                ),
            )
        )
    else:
        findings.append(
            LeakageFinding(
                "split_contamination",
                "train_vs_test",
                True,
                f"{len(train)} train and {len(test)} test sessions are disjoint",
            )
        )

    if selection_sessions is not None:
        selection = set(selection_sessions)
        sel_overlap = sorted(selection & test, key=str)
        if sel_overlap:
            shown = ", ".join(str(s) for s in sel_overlap[:5])
            more = f" (and {len(sel_overlap) - 5} more)" if len(sel_overlap) > 5 else ""
            findings.append(
                LeakageFinding(
                    "split_contamination",
                    "selection_vs_test",
                    False,
                    (
                        f"{len(sel_overlap)} session(s) appear in both the "
                        f"selection input and test: {shown}{more}. The selector "
                        f"can see the sessions it is judged on, which is the "
                        f"leak that makes walk-forward results unreproducible "
                        f"out of sample."
                    ),
                )
            )
        else:
            findings.append(
                LeakageFinding(
                    "split_contamination",
                    "selection_vs_test",
                    True,
                    f"{len(selection)} selection sessions never touch test",
                )
            )

    return LeakageReport(tuple(findings))


def run_leakage_suite(
    df: pd.DataFrame,
    compute: Callable[[pd.DataFrame], pd.DataFrame],
    *,
    cut: int,
    features: Sequence[str] | None = None,
) -> LeakageReport:
    """Run the three measured checks and the declaration audit together.

    ``compute`` should be the research path's precompute step. Passing the
    engine's own feature computation instead will produce a green report that
    means nothing, because the engine is downstream of the contamination.
    """
    findings: list[LeakageFinding] = []
    findings.extend(
        check_truncation_equivalence(df, compute, cut=cut, features=features).findings
    )
    findings.extend(
        check_prefix_invariance(df, compute, cut=cut, features=features).findings
    )
    findings.extend(
        check_future_perturbation(df, compute, cut=cut, features=features).findings
    )

    declared = list(features) if features is not None else list(compute(df).columns)
    findings.extend(audit_feature_availability(declared).findings)

    return LeakageReport(tuple(findings))
