"""Read-only run_summary reports over a research SQLite connection.

Kept free of :class:`SqliteResearchStore` so that dry runs can use a
``mode=ro`` connection: opening the store migrates the schema and backfills
publications, which would mutate the database a dry run must not touch.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Optional

from vibe.research_pipeline.lifecycle import TERMINAL_STATES, RunState


def connect_read_only(path: Path | str) -> sqlite3.Connection:
    uri = Path(path).resolve().as_uri() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def member_run_summary_eligibility(
    conn: sqlite3.Connection, run_id: str
) -> Optional[str]:
    """``None`` if a member run can get a summary, else why it cannot."""
    row = conn.execute("SELECT state FROM runs WHERE run_id = ?", (run_id,)).fetchone()
    if row is None:
        return "run row missing from local DB"
    state = RunState(row["state"])
    if state not in TERMINAL_STATES and state is not RunState.VALIDATING:
        return f"run state {state.value!r} has no recorded results"
    has_evidence = conn.execute(
        "SELECT 1 FROM run_evidence WHERE run_id = ? LIMIT 1", (run_id,)
    ).fetchone()
    if has_evidence is None:
        return "no run_evidence row (no $ totals / trade ledger hash)"
    return None


def member_run_summary_status(
    conn: sqlite3.Connection, experiment_id: Optional[str] = None
) -> list[dict[str, Any]]:
    """Experiment member runs lacking any run_summary outbox row."""
    query = """
        SELECT DISTINCT m.experiment_id, m.run_id, e.title
        FROM research_experiment_runs m
        JOIN research_experiments e ON e.experiment_id = m.experiment_id
        WHERE NOT EXISTS (
            SELECT 1 FROM publication_outbox o
            WHERE o.aggregate_type = 'run_summary'
              AND o.aggregate_id = m.run_id
        )
    """
    parameters: tuple[Any, ...] = ()
    if experiment_id is not None:
        query += " AND m.experiment_id = ?"
        parameters = (experiment_id,)
    query += " ORDER BY m.experiment_id, m.run_id"
    out: list[dict[str, Any]] = []
    for row in conn.execute(query, parameters).fetchall():
        reason = member_run_summary_eligibility(conn, row["run_id"])
        out.append(
            {
                "experiment_id": row["experiment_id"],
                "title": row["title"],
                "run_id": row["run_id"],
                "eligible": reason is None,
                "reason": reason,
            }
        )
    return out


def orphan_run_summaries(conn: sqlite3.Connection) -> dict[str, Any]:
    """run_summary outbox rows not referenced by any experiment membership.

    Two kinds are reported, grouped by ``strategy_id / methodology_version``
    (the producer, e.g. ``orb / nested_walk_forward/v1``):

    * ``no_local_membership`` -- the run is in no experiment at all.
    * ``membership_not_in_outbox`` -- the run *is* an experiment member
      locally, but no ``experiment_membership`` publication was ever queued
      (experiments predating schema v16), so the remote read model sees an
      orphan. ``publish_research.py backfill-experiments`` fixes these.
    """
    rows = conn.execute(
        """
        SELECT o.aggregate_id AS run_id, o.status,
               r.strategy_id, r.methodology_version, r.created_at,
               EXISTS (
                   SELECT 1 FROM research_experiment_runs m
                   WHERE m.run_id = o.aggregate_id
               ) AS is_member,
               EXISTS (
                   SELECT 1 FROM publication_outbox mo
                   WHERE mo.aggregate_type = 'experiment_membership'
                     AND json_extract(mo.payload_json, '$.membership.run_id')
                         = o.aggregate_id
               ) AS membership_queued
        FROM publication_outbox o
        LEFT JOIN runs r ON r.run_id = o.aggregate_id
        WHERE o.aggregate_type = 'run_summary'
        ORDER BY r.methodology_version, r.created_at, o.aggregate_id
        """
    ).fetchall()
    groups: dict[tuple[str, str], dict[str, Any]] = {}
    seen: set[tuple[str, str]] = set()
    for row in rows:
        if row["membership_queued"]:
            continue
        kind = "membership_not_in_outbox" if row["is_member"] else "no_local_membership"
        if (kind, row["run_id"]) in seen:
            continue
        seen.add((kind, row["run_id"]))
        source = f"{row['strategy_id'] or '?'} / {row['methodology_version'] or '?'}"
        group = groups.setdefault(
            (kind, source),
            {
                "kind": kind,
                "source": source,
                "count": 0,
                "by_status": {},
                "first_created_at": None,
                "last_created_at": None,
                "run_ids": [],
            },
        )
        group["count"] += 1
        status = row["status"]
        group["by_status"][status] = group["by_status"].get(status, 0) + 1
        created = row["created_at"]
        if created:
            if group["first_created_at"] is None or created < group["first_created_at"]:
                group["first_created_at"] = created
            if group["last_created_at"] is None or created > group["last_created_at"]:
                group["last_created_at"] = created
        group["run_ids"].append(row["run_id"])
    ordered = sorted(groups.values(), key=lambda g: (g["kind"], -g["count"], g["source"]))
    return {
        "total": sum(g["count"] for g in ordered),
        "by_kind": {
            kind: sum(g["count"] for g in ordered if g["kind"] == kind)
            for kind in ("no_local_membership", "membership_not_in_outbox")
        },
        "groups": ordered,
    }


# Run in the Supabase SQL editor (read-only). Lists remote run_summary rows not
# referenced by any experiment_membership publication, grouped by source.
REMOTE_ORPHAN_SQL = """\
SELECT COALESCE(p.payload->'identity'->>'strategy_id', '?') || ' / ' ||
       COALESCE(p.payload->'identity'->>'methodology_version', '?') AS source,
       COUNT(DISTINCT p.aggregate_id) AS orphan_runs,
       MIN(p.payload->'timestamps'->>'created_at') AS first_created_at,
       MAX(p.payload->'timestamps'->>'created_at') AS last_created_at
FROM public.research_publications p
WHERE p.aggregate_type = 'run_summary'
  AND NOT EXISTS (
      SELECT 1 FROM public.research_publications m
      WHERE m.aggregate_type = 'experiment_membership'
        AND m.payload->'membership'->>'run_id' = p.aggregate_id
  )
GROUP BY 1
ORDER BY orphan_runs DESC;"""
