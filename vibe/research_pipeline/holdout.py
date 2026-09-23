"""The final out-of-sample holdout lock.

A test set is out-of-sample only while it remains untouched, and asserting that
it is untouched is not a mechanism. Any script that calls the engine with
explicit dates bypasses a manifest audit, which is how every script in this
repository currently works. This module makes the boundary a property of the
system instead.

Four parts, matching section 10 of the implementation plan:

1. The range is committed to ``config/final_holdout.yaml`` *before* use, so it
   cannot be chosen after seeing results.
2. A loader guard refuses bars after ``dev_end`` without an explicit unlock
   token.
3. Every unlock appends a row to an access log recording who, when, why, and
   the run it belonged to.
4. The acceptance rule is pre-registered alongside the range, so the threshold
   is fixed before the answer is known.

**This is tamper-evident, not tamper-proof, and the distinction is the point.**
A single developer owns the file and can edit it. What the lock prevents is
redefining the holdout *accidentally* or *silently*: the file is version
controlled, so a change is a visible diff, and ``lock_hash`` is stamped on every
run, so a result produced under a different definition is permanently
distinguishable from one produced under this one. Claiming more than that would
itself be the kind of overstatement this pipeline exists to prevent.

Why the boundary sits at 2024-12-31 is recorded in the config file and is worth
repeating: it is where contamination actually ends. Every record in
``research/`` was produced over 2018-01-01..2024-12-31, so no promise about any
earlier session could be true. The honest holdout is the largest untouched
suffix of the data, not the largest convenient one.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence

import yaml

from vibe.research_pipeline.hashing import hash_object
from vibe.research_pipeline.paths import default_data_dir

__all__ = [
    "HOLDOUT_LOCK_VERSION",
    "ENV_HOLDOUT_CONFIG",
    "ENV_HOLDOUT_ACCESS_LOG",
    "AcceptanceRule",
    "HoldoutLock",
    "HoldoutViolation",
    "UnlockToken",
    "load_lock",
    "default_config_path",
    "access_log_path",
    "record_access",
    "read_access_log",
    "touch_count",
]

HOLDOUT_LOCK_VERSION = 1

ENV_HOLDOUT_CONFIG = "RESEARCH__HOLDOUT_CONFIG"
ENV_HOLDOUT_ACCESS_LOG = "RESEARCH__HOLDOUT_ACCESS_LOG"

ACCESS_LOG_FILENAME = "oos_access_log.jsonl"


class HoldoutViolation(PermissionError):
    """An attempt to read final-holdout data without an unlock token.

    ``PermissionError`` rather than ``ValueError`` because the request is
    well-formed and the data exists -- what is missing is authorization. The
    caller is not meant to catch this and retry; they are meant to stop.
    """


def default_config_path() -> Path:
    """Locate ``config/final_holdout.yaml``.

    Honours ``RESEARCH__HOLDOUT_CONFIG`` so tests can point at a fixture
    without mutating the committed lock.
    """
    override = os.environ.get(ENV_HOLDOUT_CONFIG)
    if override:
        return Path(override).expanduser()
    # holdout.py -> research_pipeline -> vibe -> repo root
    return Path(__file__).resolve().parents[2] / "config" / "final_holdout.yaml"


def access_log_path(*, create_parents: bool = False) -> Path:
    """Where unlock events are appended.

    Lives outside the repository for the same reason the research database
    does: it is per-user state, not source. It is also deliberately *not*
    inside the repo because an access log that can be reverted with
    ``git checkout`` is not an access log.
    """
    override = os.environ.get(ENV_HOLDOUT_ACCESS_LOG)
    path = (
        Path(override).expanduser()
        if override
        else default_data_dir() / ACCESS_LOG_FILENAME
    )
    if create_parents:
        path.parent.mkdir(parents=True, exist_ok=True)
    return path


@dataclass(frozen=True)
class AcceptanceRule:
    """The success criterion, fixed before the holdout is read.

    Recorded in the lock rather than supplied at evaluation time. With a few
    hundred trades the standard error on expectancy is wide, so a threshold
    chosen after seeing the result is indistinguishable from choosing the
    result.
    """

    metric: str
    comparison: str
    threshold: float
    min_trades: int
    rationale: str = ""

    def __post_init__(self) -> None:
        if self.comparison not in (">=", "<=", ">", "<"):
            raise ValueError(
                f"Unsupported comparison {self.comparison!r}; "
                "use one of >=, <=, >, <"
            )
        if self.min_trades < 0:
            raise ValueError("min_trades must be non-negative")

    def evaluate(self, value: float, n_trades: int) -> "AcceptanceOutcome":
        """Apply the rule. Too few trades is inconclusive, not a failure.

        A thin sample carries no information either way, and recording it as a
        rejection would burn the holdout on a result that never had the power
        to say anything.
        """
        if n_trades < self.min_trades:
            return AcceptanceOutcome(
                passed=False,
                conclusive=False,
                detail=(
                    f"{n_trades} trades is below the pre-registered minimum of "
                    f"{self.min_trades}; the sample cannot support a verdict"
                ),
            )
        ops = {
            ">=": lambda a, b: a >= b,
            "<=": lambda a, b: a <= b,
            ">": lambda a, b: a > b,
            "<": lambda a, b: a < b,
        }
        passed = ops[self.comparison](value, self.threshold)
        return AcceptanceOutcome(
            passed=passed,
            conclusive=True,
            detail=(
                f"{self.metric} = {value:.4f}, pre-registered rule is "
                f"{self.comparison} {self.threshold} over >= "
                f"{self.min_trades} trades ({n_trades} observed)"
            ),
        )


@dataclass(frozen=True)
class AcceptanceOutcome:
    passed: bool
    conclusive: bool
    detail: str


@dataclass(frozen=True)
class UnlockToken:
    """Explicit, attributed authorization to read the holdout once.

    Every field is required because the point of the token is accountability,
    not convenience. A token that could be constructed with no reason attached
    would be a keyword argument, not a control.
    """

    reason: str
    requested_by: str
    run_id: str

    def __post_init__(self) -> None:
        for name in ("reason", "requested_by", "run_id"):
            value = getattr(self, name)
            if not value or not str(value).strip():
                raise ValueError(
                    f"UnlockToken.{name} is required. An unlock with no "
                    f"{name} cannot be audited, which defeats the log."
                )
        if len(self.reason.strip()) < 10:
            raise ValueError(
                "UnlockToken.reason must be a real explanation "
                "(at least 10 characters), not a placeholder."
            )


@dataclass(frozen=True)
class HoldoutLock:
    """The committed definition of the final out-of-sample period."""

    dev_end: date
    oos_start: date
    oos_end: date
    symbols: tuple[str, ...]
    declared_at: date
    declared_by: str
    acceptance: AcceptanceRule
    version: int = HOLDOUT_LOCK_VERSION

    def __post_init__(self) -> None:
        if self.oos_start <= self.dev_end:
            raise ValueError(
                f"oos_start {self.oos_start} must be after dev_end "
                f"{self.dev_end}; overlapping ranges mean the holdout is "
                "already contaminated by development data."
            )
        if self.oos_end < self.oos_start:
            raise ValueError(
                f"oos_end {self.oos_end} precedes oos_start {self.oos_start}"
            )
        if not self.symbols:
            raise ValueError("A lock covering no symbols guards nothing.")

    @property
    def lock_hash(self) -> str:
        """Canonical hash of the lock, for stamping onto every run.

        Includes the acceptance rule. Moving the threshold is as much a
        redefinition of the test as moving the dates, and a lock whose hash
        ignored it would let the criterion drift invisibly.
        """
        return hash_object(
            {
                "version": self.version,
                "dev_end": self.dev_end,
                "oos_start": self.oos_start,
                "oos_end": self.oos_end,
                "symbols": sorted(self.symbols),
                "acceptance": {
                    "metric": self.acceptance.metric,
                    "comparison": self.acceptance.comparison,
                    "threshold": self.acceptance.threshold,
                    "min_trades": self.acceptance.min_trades,
                },
            }
        )

    def covers(self, symbol: str) -> bool:
        return symbol.upper() in {s.upper() for s in self.symbols}

    def intersects_holdout(self, start: date, end: date) -> bool:
        """Does ``[start, end]`` include any session after ``dev_end``?

        Only the upper bound matters. A request running from 2019 to 2026
        touches the holdout just as surely as one confined to 2025, and a
        check on ``start`` alone would wave the first one through -- which is
        exactly the shape of request that would otherwise leak.
        """
        return end > self.dev_end and start <= self.oos_end

    def assert_within_dev(
        self,
        *,
        symbol: str,
        start: date,
        end: date,
        token: Optional[UnlockToken] = None,
        log: bool = True,
    ) -> None:
        """Refuse a data request that reaches past ``dev_end`` without a token.

        Symbols outside the lock pass through untouched. The lock makes a
        promise about the instruments it names, and silently blocking others
        would be an unrelated restriction wearing this one's clothes.
        """
        if not self.covers(symbol):
            return
        if not self.intersects_holdout(start, end):
            return
        if token is None:
            raise HoldoutViolation(
                f"Refusing to load {symbol} data through {end}: the final "
                f"holdout begins {self.oos_start} (dev_end {self.dev_end}). "
                f"This period is reserved for a single, final evaluation of a "
                f"frozen methodology. If that is what this is, pass an "
                f"UnlockToken with a reason, a requester, and a run id; the "
                f"unlock will be logged. If it is not, end the request on or "
                f"before {self.dev_end}."
            )
        if log:
            record_access(
                lock=self, token=token, symbol=symbol, start=start, end=end
            )


def _as_date(value: Any, field_name: str) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        return date.fromisoformat(value)
    raise ValueError(f"{field_name} must be a date, got {value!r}")


def load_lock(path: Optional[Path] = None) -> HoldoutLock:
    """Read and validate the committed lock."""
    path = path or default_config_path()
    if not path.exists():
        raise FileNotFoundError(
            f"No holdout lock at {path}. The final out-of-sample range must be "
            "committed before it is used; running without one means the range "
            "can be chosen after seeing results."
        )
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}

    acceptance_raw = raw.get("acceptance") or {}
    acceptance = AcceptanceRule(
        metric=acceptance_raw["metric"],
        comparison=acceptance_raw.get("comparison", ">="),
        threshold=float(acceptance_raw["threshold"]),
        min_trades=int(acceptance_raw.get("min_trades", 0)),
        rationale=acceptance_raw.get("rationale", ""),
    )

    return HoldoutLock(
        dev_end=_as_date(raw["dev_end"], "dev_end"),
        oos_start=_as_date(raw["oos_start"], "oos_start"),
        oos_end=_as_date(raw["oos_end"], "oos_end"),
        symbols=tuple(raw.get("symbols", ())),
        declared_at=_as_date(raw["declared_at"], "declared_at"),
        declared_by=str(raw.get("declared_by", "")),
        acceptance=acceptance,
        version=int(raw.get("version", HOLDOUT_LOCK_VERSION)),
    )


def record_access(
    *,
    lock: HoldoutLock,
    token: UnlockToken,
    symbol: str,
    start: date,
    end: date,
    path: Optional[Path] = None,
) -> None:
    """Append one unlock to the access log.

    Append-only JSONL rather than a rewritable document, so a prior access
    cannot be edited away. The ``lock_hash`` is recorded on every row: if the
    lock is later changed, old accesses remain attributable to the definition
    that was actually in force when they happened.
    """
    path = path or access_log_path(create_parents=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "lock_hash": lock.lock_hash,
        "symbol": symbol,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "reason": token.reason,
        "requested_by": token.requested_by,
        "run_id": token.run_id,
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, sort_keys=True) + "\n")


def read_access_log(path: Optional[Path] = None) -> list[dict[str, Any]]:
    """Return every recorded unlock, oldest first."""
    path = path or access_log_path()
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def touch_count(
    symbol: Optional[str] = None,
    *,
    lock_hash: Optional[str] = None,
    path: Optional[Path] = None,
) -> int:
    """How many times the holdout has been unlocked.

    Promotion is blocked above one. Filterable by ``lock_hash`` so that
    redefining the lock does not quietly reset the counter -- the count under
    the *current* definition and the count over all time are different
    questions, and conflating them would let a rename launder a used holdout.
    """
    rows: Iterable[dict[str, Any]] = read_access_log(path)
    if symbol is not None:
        rows = [r for r in rows if r.get("symbol", "").upper() == symbol.upper()]
    if lock_hash is not None:
        rows = [r for r in rows if r.get("lock_hash") == lock_hash]
    return len(list(rows))
