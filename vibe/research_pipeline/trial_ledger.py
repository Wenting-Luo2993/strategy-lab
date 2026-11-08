"""Manually recorded external / legacy trials for a programme's trial ledger.

Trials examined outside the current research DB -- before a rebuild, or by
older tooling on the same data -- still inflate the multiplicity a programme
must correct for. They are recorded by hand in
``config/research/trial_ledger_<programme>.yaml`` and combined here with the
DB-derived counts from ``SqliteResearchStore.trial_counts``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

import yaml

DEFAULT_LEDGER_DIR = Path(__file__).resolve().parents[2] / "config" / "research"


@dataclass(frozen=True)
class ExternalTrialEntry:
    id: str
    source: str
    phase: str
    family: str
    trials: int
    counts_toward_total: bool = True
    experiment_ids: tuple[str, ...] = ()
    note: str = ""
    runs: Mapping[str, int] = field(default_factory=dict)


def default_ledger_path(programme: str) -> Path:
    return DEFAULT_LEDGER_DIR / f"trial_ledger_{programme}.yaml"


def load_external_trials(path: Path | str, *, programme: str) -> list[ExternalTrialEntry]:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    if not isinstance(data, Mapping):
        raise ValueError(f"{path}: trial ledger must be a mapping")
    if data.get("programme") != programme:
        raise ValueError(
            f"{path}: ledger is for programme {data.get('programme')!r}, "
            f"not {programme!r}"
        )
    entries: list[ExternalTrialEntry] = []
    seen: set[str] = set()
    for raw in data.get("entries") or []:
        entry_id = str(raw["id"])
        if entry_id in seen:
            raise ValueError(f"{path}: duplicate entry id {entry_id!r}")
        seen.add(entry_id)
        trials = raw["trials"]
        if isinstance(trials, bool) or not isinstance(trials, int) or trials < 0:
            raise ValueError(f"{path}: entry {entry_id!r} trials must be an int >= 0")
        entries.append(
            ExternalTrialEntry(
                id=entry_id,
                source=str(raw["source"]),
                phase=str(raw.get("phase", "external")),
                family=str(raw.get("family", "external")),
                trials=trials,
                counts_toward_total=bool(raw.get("counts_toward_total", True)),
                experiment_ids=tuple(str(x) for x in raw.get("experiment_ids") or ()),
                note=" ".join(str(raw.get("note", "")).split()),
                runs=dict(raw.get("runs") or {}),
            )
        )
    return entries


def combine_ledger(
    db_ledger: Mapping[str, Any], external: Sequence[ExternalTrialEntry]
) -> dict[str, Any]:
    """DB counts plus external entries; entries already in the DB are skipped."""
    in_db = set(db_ledger.get("experiment_ids") or ())
    lines: list[dict[str, Any]] = []
    external_total = 0
    for entry in external:
        duplicated = sorted(set(entry.experiment_ids) & in_db)
        counted = entry.counts_toward_total and not duplicated
        if counted:
            external_total += entry.trials
        lines.append(
            {
                "id": entry.id,
                "source": entry.source,
                "phase": entry.phase,
                "family": entry.family,
                "trials": entry.trials,
                "counted": counted,
                "skipped_reason": (
                    f"already in research DB ({', '.join(duplicated)})"
                    if duplicated
                    else (None if entry.counts_toward_total else "counts_toward_total: false")
                ),
                "runs": dict(entry.runs),
                "note": entry.note,
            }
        )
    db_total = int(db_ledger.get("total_trials", 0))
    return {
        "programme": db_ledger.get("programme"),
        "db_trials": db_total,
        "external_trials": external_total,
        "total_trials": db_total + external_total,
        "external": lines,
    }


def resolve_ledger_file(
    programme: str, explicit: Optional[Path], *, disabled: bool = False
) -> Optional[Path]:
    if disabled:
        return None
    if explicit is not None:
        return explicit
    candidate = default_ledger_path(programme)
    return candidate if candidate.is_file() else None
