"""Bounded JSON bridge from the local Node viewer to the SQLite analysis adapter."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from vibe.research_pipeline.analysis import (
    AnalysisCostExceeded,
    AnalysisQueryError,
    SqliteAnalysisAdapter,
)


def _current_only(query: dict[str, Any]) -> dict[str, Any]:
    population = query.get("population")
    if not isinstance(population, dict):
        population = {}
    states = population.get("trustStates")
    if not isinstance(states, list):
        states = ["current_validated"]
    current_states = [
        state
        for state in states
        if state in {"current_validated", "validation_attention"}
    ]
    query["population"] = {
        "includeLegacy": False,
        "trustStates": current_states or ["current_validated"],
    }
    return query


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument(
        "--action",
        required=True,
        choices=("catalog", "estimate", "query"),
    )
    args = parser.parse_args()
    request = json.load(sys.stdin) if args.action != "catalog" else {}
    if not isinstance(request, dict):
        raise AnalysisQueryError("Analysis query must be an object")

    adapter = SqliteAnalysisAdapter(args.db)
    try:
        if args.action == "catalog":
            result: object = adapter.field_catalog()
        elif args.action == "estimate":
            result = adapter.estimate(_current_only(request))
        else:
            result = adapter.query(_current_only(request))
    finally:
        adapter.close()
    json.dump(result, sys.stdout, separators=(",", ":"))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (AnalysisQueryError, AnalysisCostExceeded, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(2) from None
