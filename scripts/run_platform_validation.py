"""Run the platform validation suites (V6 null calibration, V7 planted edge).

Example:
    python scripts/run_platform_validation.py --suite all --studies 200 \
        --sessions 500 --seed 1 --research-db path/to/research.sqlite

Each suite is recorded as one experiment with ``evidence_role`` of
``platform_validation``; the report is in its result. These runs drive the real
backtest engine on synthetic data and are slow at full size.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

DEFAULT_RULESET = "vibe/rulesets/orb_production.yaml"
METHODOLOGY_VERSION = "platform_validation/v1"


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--suite", choices=("v6", "v7", "all"), default="all")
    p.add_argument("--studies", type=int, default=200)
    p.add_argument("--sessions", type=int, default=500)
    p.add_argument("--seed", type=int, default=1)
    p.add_argument("--ruleset", default=DEFAULT_RULESET)
    p.add_argument("--up-probability", type=float, default=0.502)
    p.add_argument("--truth-sessions", type=int, default=20000)
    p.add_argument("--confidence-level", type=float, default=0.95)
    p.add_argument("--research-db", type=Path, default=None)
    p.add_argument("--no-record", action="store_true")
    return p.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    from vibe.research_pipeline.validation_suite.suite import (
        run_null_calibration,
        run_planted_edge,
    )

    reports: list[tuple[str, str, dict[str, Any]]] = []
    if args.suite in ("v6", "all"):
        report = run_null_calibration(
            args.studies, args.sessions, args.seed, args.ruleset, args.confidence_level
        )
        reports.append(("V6 null calibration", "v6", report))
    if args.suite in ("v7", "all"):
        report = run_planted_edge(
            args.studies, args.sessions, args.seed, args.ruleset,
            up_probability_with_trend=args.up_probability,
            truth_sessions=args.truth_sessions,
            confidence_level=args.confidence_level,
        )
        reports.append(("V7 planted edge", "v7", report))

    for title, _, report in reports:
        print(f"\n{title}\n{json.dumps(report, indent=2)}")

    if args.no_record:
        return 0

    from vibe.research_pipeline.storage.sqlite_store import SqliteResearchStore
    from vibe.research_pipeline.paths import research_db_path

    store = SqliteResearchStore(args.research_db or research_db_path(create_parents=True))
    try:
        for title, suite, report in reports:
            definition = {
                "kind": "platform_validation",
                "evidence_role": "platform_validation",
                "suite": suite,
                "methodology_version": METHODOLOGY_VERSION,
                "ruleset": args.ruleset,
                "seed": args.seed,
            }
            experiment_id = store.start_experiment(title, definition)
            store.finish_experiment(
                experiment_id, "completed", {"summary": report, "evidence_role": "platform_validation"}
            )
            print(f"Experiment: {experiment_id}")
    finally:
        store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
