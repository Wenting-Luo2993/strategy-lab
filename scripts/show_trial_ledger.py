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
    args = p.parse_args(argv)

    from vibe.research_pipeline.paths import research_db_path
    from vibe.research_pipeline.storage.sqlite_store import SqliteResearchStore

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
    print(f"{'total':<36}{'':>12}{ledger['total_trials']:>8}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
