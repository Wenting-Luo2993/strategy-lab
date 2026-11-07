"""Print how many trials a research programme has examined, by phase and family.

Example:
    python scripts/show_trial_ledger.py --programme orb_edge_2026
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--programme", required=True)
    p.add_argument("--research-db", type=Path, default=None)
    p.add_argument(
        "--ledger-file",
        type=Path,
        default=None,
        help="External/legacy trials YAML (default: "
        "config/research/trial_ledger_<programme>.yaml if it exists)",
    )
    p.add_argument(
        "--no-external",
        action="store_true",
        help="Count only experiments recorded in the research DB",
    )
    args = p.parse_args(argv)

    from vibe.research_pipeline.paths import research_db_path
    from vibe.research_pipeline.storage.sqlite_store import SqliteResearchStore
    from vibe.research_pipeline.trial_ledger import (
        combine_ledger,
        load_external_trials,
        resolve_ledger_file,
    )

    store = SqliteResearchStore(args.research_db or research_db_path())
    try:
        ledger = store.trial_counts(args.programme)
    finally:
        store.close()

    print(f"Trial ledger: {ledger['programme']}")
    print(f"{'phase':<22}{'family':<14}{'experiments':>12}{'trials':>8}")
    for cell in ledger["cells"]:
        print(
            f"{cell['phase']:<22}{cell['family']:<14}"
            f"{cell['experiments']:>12}{cell['trials']:>8}"
        )
    print(f"{'recorded in research DB':<36}{'':>12}{ledger['total_trials']:>8}")

    ledger_file = resolve_ledger_file(
        args.programme, args.ledger_file, disabled=args.no_external
    )
    if ledger_file is None:
        print(f"{'total':<36}{'':>12}{ledger['total_trials']:>8}")
        return 0
    combined = combine_ledger(
        ledger, load_external_trials(ledger_file, programme=args.programme)
    )
    print()
    print(f"External / legacy trials ({ledger_file.name})")
    for line in combined["external"]:
        flag = "" if line["counted"] else f"  [not counted: {line['skipped_reason']}]"
        print(f"  {line['source']}: {line['id']}{flag}")
        runs = ", ".join(f"{k} {v} runs" for k, v in line["runs"].items())
        detail = f"{line['phase']}/{line['family']}" + (f"; {runs}" if runs else "")
        print(f"    {detail:<56}{line['trials']:>8}")
    print()
    print(f"{'total (DB + external)':<36}{'':>12}{combined['total_trials']:>8}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
