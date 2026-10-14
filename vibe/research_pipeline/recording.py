"""Record a completed backtest into the research store.

Everything needed to persist a run has existed for several increments --
``RunFingerprint``, ``register_run``, ``record_metrics``, ``record_evidence``,
and now ``record_trades``/``record_equity_curve`` -- but nothing in the
production path ever called any of it. ``register_run`` appeared only in its
own definition and in tests, and ``scripts/run_backtest.py`` wrote an HTML
report and a CSV and then dropped the run on the floor. The research database
was never created, so every dashboard reading from it had nothing to read.

This module is the missing producer. It does not compute anything new: the
metrics, the evidence, and the digests all come from code that already exists.
Its only job is to make a real run leave a durable, identified trace.

Two decisions are deliberate and worth stating, because both could reasonably
have gone the other way:

**The backtest runs inside the lifecycle, not before it.** The run is
registered and moved to ``RUNNING`` *before* the engine is invoked, so a crash
mid-simulation leaves an ``EXECUTION_FAILED`` row rather than no row at all. A
recorder that only persisted successful runs would quietly bias the record
toward configurations that happen not to crash.

**Recording stops at ``VALIDATING`` and does not finalize.** A terminal state
is only legitimate via ``finalize_validation``, which requires a real
``LeakageReport`` and a ``DataIntegritySummary`` measured from the bars the run
actually consumed. Neither is cheap, and neither can be fabricated: a
synthesized "passing" leakage report would mark unvalidated runs ``COMPLETED``
and destroy the meaning of the state. Runs therefore come to rest in
``VALIDATING``, which is non-terminal and truthfully says "recorded, not yet
judged". ``sweep_stale_leases`` only reaps ``RUNNING``, so resting there is
safe.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Mapping, Optional

from vibe.research_pipeline.contracts import (
    RetentionProfile,
    RunEvidence,
    SegmentRole,
    SessionSegment,
    SplitManifest,
    SurvivorshipBias,
    UniverseSpec,
    UniverseType,
)
from vibe.research_pipeline.evidence import equity_curve_digest, trade_ledger_digest
from vibe.research_pipeline.features.registry import FEATURE_SET_VERSION
from vibe.research_pipeline.identity import RunFingerprint
from vibe.research_pipeline.lifecycle import RunState

__all__ = [
    "RecordingError",
    "RunRecorder",
    "build_run_evidence",
    "build_run_fingerprint",
    "git_state",
]


class RecordingError(RuntimeError):
    """Raised when a run cannot be recorded honestly."""


@dataclass(frozen=True)
class GitState:
    commit: str
    dirty: bool


def _run_git(args: list[str], repo_root: Path) -> str:
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:  # pragma: no cover
        raise RecordingError(
            f"Could not read git state from {repo_root}: {exc}. A run that "
            f"cannot name the code that produced it is not reproducible, and "
            f"substituting a placeholder commit would record that lie as fact. "
            f"Pass code_commit/code_dirty explicitly if this is a non-git "
            f"checkout."
        ) from exc
    return completed.stdout.strip()


def git_state(repo_root: Path | str) -> GitState:
    """Return the commit and working-tree cleanliness of ``repo_root``.

    Compiled bytecode is excluded from the dirty check. This repository tracks
    ``__pycache__/*.pyc``, and those files are rewritten by virtually every
    test run, so including them would mark *every* run ``code_dirty=True``.
    A flag that is always set carries no information and would mask the case it
    exists to catch -- uncommitted *source* changes. Bytecode cannot diverge
    from its source behaviourally: CPython invalidates a ``.pyc`` whose source
    hash or mtime disagrees, so a stale one is recompiled rather than executed.
    """
    root = Path(repo_root)
    commit = _run_git(["rev-parse", "HEAD"], root)
    if not commit:
        raise RecordingError(f"No HEAD commit in {root}")

    porcelain = _run_git(["status", "--porcelain"], root)
    dirty = False
    for line in porcelain.splitlines():
        path = line[3:].strip().strip('"')
        if not path:
            continue
        if path.endswith(".pyc") or "__pycache__" in path:
            continue
        dirty = True
        break
    return GitState(commit=commit, dirty=dirty)


def _single_symbol_universe(symbol: str) -> UniverseSpec:
    return UniverseSpec(
        universe_type=UniverseType.SINGLE_SYMBOL,
        symbols=(symbol,),
        survivorship_bias=SurvivorshipBias.NOT_APPLICABLE,
        selection_rationale=(
            f"Single instrument {symbol.upper()} chosen directly by the "
            f"operator; no screen was applied, so no survivorship filter "
            f"exists to disclose."
        ),
    )


def _contiguous_split(
    *, first_session: date, last_session: date, session_count: int
) -> SplitManifest:
    """Describe a single uninterrupted evaluation window.

    Purge and embargo are derived as zero because the strategies recorded
    through this path are flat at every close: with no position held overnight,
    no label extends past the session that produced it, so no sessions need to
    be dropped between adjacent segments. If a strategy with a multi-session
    holding period is ever recorded here, this derivation becomes wrong and
    must be computed from that strategy's label horizon instead.
    """
    return SplitManifest(
        calendar_name="XNYS",
        segments=(
            SessionSegment(
                role=SegmentRole.TEST,
                start_session=first_session,
                end_session=last_session,
                session_count=session_count,
            ),
        ),
        purge_sessions_derived=0,
        purge_sessions_applied=0,
        embargo_sessions_derived=0,
        embargo_sessions_applied=0,
    )


def _as_date(value: Any) -> Optional[date]:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value)
    try:
        return datetime.fromisoformat(text).date()
    except ValueError:
        try:
            return datetime.strptime(text[:10], "%Y-%m-%d").date()
        except ValueError:
            return None


def build_run_fingerprint(
    *,
    strategy_id: str,
    ruleset_path: Path | str,
    parameters: Mapping[str, Any],
    symbol: str,
    first_session: date,
    last_session: date,
    session_count: int,
    data_snapshot_id: str,
    execution_model_version: int,
    metric_calculation_version: int,
    repo_root: Path | str,
    code_commit: Optional[str] = None,
    code_dirty: Optional[bool] = None,
    random_seed: Optional[int] = None,
) -> RunFingerprint:
    """Assemble the full identity of a run from its actual inputs."""
    if code_commit is None or code_dirty is None:
        state = git_state(repo_root)
        code_commit = code_commit if code_commit is not None else state.commit
        code_dirty = code_dirty if code_dirty is not None else state.dirty

    return RunFingerprint(
        strategy_id=strategy_id,
        ruleset_content_sha256=RunFingerprint.hash_ruleset(ruleset_path),
        parameters=dict(parameters),
        universe_hash=_single_symbol_universe(symbol).universe_hash,
        split_manifest_hash=_contiguous_split(
            first_session=first_session,
            last_session=last_session,
            session_count=session_count,
        ).manifest_hash,
        data_snapshot_id=data_snapshot_id,
        feature_set_version=FEATURE_SET_VERSION,
        execution_model_version=execution_model_version,
        metric_calculation_version=metric_calculation_version,
        code_commit=code_commit,
        code_dirty=code_dirty,
        random_seed=random_seed,
    )


def build_run_evidence(
    result: Any, *, starting_equity: float, retention: RetentionProfile
) -> RunEvidence:
    """Summarize what a finished run produced, with checksums.

    ``starting_equity`` is the configured initial capital and is passed in
    rather than read back off the equity curve. ``RunEvidence`` asserts that
    ``ending_equity - starting_equity`` equals net P&L; deriving the start from
    the same curve that supplies the end would make that identity hold by
    construction and assert nothing about the engine's accounting.
    """
    overall = result.overall
    curve = result.equity.equity_curve
    diagnostics = result.execution_diagnostics or {}

    if len(curve) == 0:
        raise RecordingError(
            "Refusing to record a run with an empty equity curve: there is "
            "nothing to attest to, and a fabricated end point would make the "
            "P&L reconciliation vacuous."
        )

    return RunEvidence(
        trade_count=len(result.trades),
        trade_ledger_sha256=trade_ledger_digest(result.trades),
        equity_curve_sha256=equity_curve_digest(curve),
        equity_points=len(curve),
        first_session=_as_date(overall.first_date),
        last_session=_as_date(overall.last_date),
        starting_equity=float(starting_equity),
        ending_equity=float(curve.iloc[-1]),
        gross_pnl=float(overall.gross_pnl),
        total_costs=float(overall.total_costs),
        net_pnl=float(overall.total_pnl),
        max_observed_leverage=float(diagnostics.get("max_gross_exposure_ratio", 0.0)),
        min_cash=float(diagnostics.get("min_cash", starting_equity)),
        retention_profile=retention,
    )


class RunRecorder:
    """Drive one backtest through the research-run lifecycle.

    Usage::

        recorder = RunRecorder(store, fingerprint=fp, methodology_version="v1",
                               retention=RetentionProfile.FULL)
        with recorder:
            result = engine.run(...)
            recorder.record(result, starting_equity=capital)

    Leaving the block without a successful ``record`` marks the run
    ``EXECUTION_FAILED``, so an abandoned or crashed run is visible as such
    instead of silently absent.
    """

    def __init__(
        self,
        store: Any,
        *,
        fingerprint: RunFingerprint,
        methodology_version: str,
        retention: RetentionProfile = RetentionProfile.SUMMARY,
        owner: str = "run_backtest",
        lease_seconds: int = 3600,
        notes: Optional[str] = None,
    ) -> None:
        self._store = store
        self._fingerprint = fingerprint
        self._methodology_version = methodology_version
        self._retention = retention
        self._owner = owner
        self._lease_seconds = lease_seconds
        self._notes = notes

        self.run_id: Optional[str] = None
        self._lease_token: Optional[str] = None
        self._recorded = False

    def __enter__(self) -> "RunRecorder":
        self.run_id = self._store.register_run(
            fingerprint=self._fingerprint,
            methodology_version=self._methodology_version,
            notes=self._notes,
        )
        self._lease_token = self._store.start_run(
            self.run_id, owner=self._owner, lease_seconds=self._lease_seconds
        )
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        if self._recorded:
            return False
        if self.run_id is None:
            return False
        reason = (
            f"{exc_type.__name__}: {exc}"
            if exc_type is not None
            else "Run ended without recording a result"
        )
        try:
            self._store.transition(
                self.run_id,
                target=RunState.EXECUTION_FAILED,
                reason=reason[:500],
                lease_token=self._lease_token,
            )
        except Exception:  # pragma: no cover - never mask the original failure
            pass
        return False

    def record(self, result: Any, *, starting_equity: float) -> str:
        """Persist metrics, evidence, and -- if retained -- row-level detail."""
        if self.run_id is None:
            raise RecordingError("RunRecorder.record called outside its context")

        # Imported here so the recorder does not pull the backtester into any
        # caller that only reads the store.
        from vibe.research_pipeline.validation import metric_values

        self._store.transition(
            self.run_id,
            target=RunState.VALIDATING,
            reason="Simulation complete",
            lease_token=self._lease_token,
        )

        metrics = metric_values(result)
        self._store.record_metrics(
            self.run_id,
            metrics,
            calculation_version=int(result.overall.calculation_version),
        )

        evidence = build_run_evidence(
            result, starting_equity=starting_equity, retention=self._retention
        )
        self._store.record_evidence(self.run_id, evidence)

        if self._retention in (RetentionProfile.DIAGNOSTIC, RetentionProfile.FULL):
            self._store.record_trades(self.run_id, result.trades)
        if self._retention is RetentionProfile.FULL:
            self._store.record_equity_curve(self.run_id, result.equity.equity_curve)

        self._recorded = True
        return self.run_id
