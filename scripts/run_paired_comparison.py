"""Compare a variant ruleset against a baseline on identical symbols and sessions.

Example:
    python scripts/run_paired_comparison.py --title "ORB trailing stop" \
        --baseline-ruleset vibe/rulesets/orb_production.yaml \
        --variant-ruleset path/to/variant.yaml --lever exit.trailing_stop \
        --symbols QQQ,SPY --start 2018-01-02 --end 2023-12-29 \
        --evidence-role exploration

The estimate is the mean per-session difference in net R. See
``vibe/research_pipeline/paired.py``. Runs are recorded without a validator, so
they rest at ``VALIDATING``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sys
from datetime import date
from pathlib import Path
from typing import Any, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

METHODOLOGY_VERSION = "paired_comparison/v1"
DEFAULT_RULESET = "vibe/rulesets/orb_production.yaml"


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--title", required=True)
    p.add_argument("--baseline-ruleset", default=DEFAULT_RULESET)
    p.add_argument("--variant-ruleset", required=True)
    p.add_argument("--lever", required=True)
    p.add_argument("--symbols", required=True, help="Comma-separated symbols")
    p.add_argument("--start", required=True, type=date.fromisoformat)
    p.add_argument("--end", required=True, type=date.fromisoformat)
    p.add_argument(
        "--evidence-role", required=True,
        choices=("exploration", "confirmation", "platform_validation"),
    )
    p.add_argument("--data-dir", default=None)
    p.add_argument("--research-db", type=Path, default=None)
    p.add_argument("--capital", type=float, default=100_000.0)
    p.add_argument("--warmup-sessions", type=int, default=60)
    p.add_argument("--min-edge-r", type=float, default=0.05)
    p.add_argument("--confidence-level", type=float, default=0.95)
    p.add_argument("--bootstrap-replicates", type=int, default=1000)
    p.add_argument("--bootstrap-seed", type=int, default=20261031)
    p.add_argument("--programme", default=None)
    p.add_argument("--trial-family", default="enhancement",
                   choices=("baseline", "condition", "enhancement", "validation"))
    p.add_argument("--trial-label", default=None)
    p.add_argument("--hypothesis-id", action="append", default=[])
    p.add_argument("--dataset-manifest", type=Path, default=None,
                   help="JSON file mapping SYMBOL -> dataset_id")
    p.add_argument("--registration", type=Path, default=None)
    p.add_argument("--no-record", action="store_true")
    return p.parse_args(argv)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _fmt(value: Any, digits: int = 4) -> str:
    return "n/a" if value is None else f"{value:.{digits}f}"


def _print_summary(result) -> None:
    s = result.summary
    d = s["difference"]
    print("\n" + "=" * 72)
    print(f"{result.spec.title}  [{result.spec.evidence_role}]  -> {result.state}")
    print("=" * 72)
    print(
        f"Variant - baseline per symbol-session: n={d['n']} clusters={d['n_clusters']} "
        f"mean={_fmt(d['mean_r'])}R CI=[{_fmt(d['ci_low_r'])}, {_fmt(d['ci_high_r'])}]"
    )
    print(
        f"Outcome vs {_fmt(s['min_edge_per_session_r'])}R/session "
        f"({result.spec.min_edge_r}R/trade): {s['outcome']}"
    )
    print(f"Minimum detectable effect: {_fmt(s['mde_per_session_r'])}R/session")
    print(
        f"Sessions changed: {s['sessions_changed']}/{s['n_symbol_sessions']}; "
        f"R/year difference: {_fmt(s['difference_per_year_r'], 2)}"
    )
    print(
        f"Baseline trades={s['baseline']['n_trades']}  Variant trades={s['variant']['n_trades']}"
    )
    if s["errors"]:
        print("\nErrors:")
        for symbol, message in s["errors"].items():
            print(f"  {symbol}: {message}")


def _data_dates(data_dir: Path, symbol: str) -> set[str] | None:
    import pandas as pd

    path = data_dir / f"{symbol}.parquet"
    if not path.is_file():
        return None
    index = pd.read_parquet(path).index
    if not isinstance(index, pd.DatetimeIndex):
        return None
    return {ts.date().isoformat() for ts in index}


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    import yaml

    from vibe.backtester.core.engine import BacktestEngine
    from vibe.backtester.core.execution_realism import (
        EXECUTION_MODEL_VERSION,
        ExecutionRealismConfig,
    )
    from vibe.backtester.data.paths import (
        MarketDataNotFoundError,
        resolve_market_data_dir,
    )
    from vibe.common.ruleset.models import StrategyRuleSet
    from vibe.research_pipeline.contracts import RetentionProfile, SessionSegment
    from vibe.research_pipeline.fixed_rule import ConfirmationNotSupported, check_holdout
    from vibe.research_pipeline.holdout import HoldoutViolation
    from vibe.research_pipeline.paired import (
        PairedSpec,
        paired_definition,
        record_paired_comparison,
        run_paired_comparison,
    )
    from vibe.research_pipeline.recording import (
        BacktestRunSink,
        hash_effective_ruleset,
    )
    from vibe.research_pipeline.segment_runner import run_segment
    from vibe.research_pipeline.splits.calendar import SessionCalendar
    from vibe.research_pipeline.trials import TrialTag

    trial = None
    if args.programme:
        trial = TrialTag(
            programme=args.programme, phase=args.evidence_role,
            family=args.trial_family, label=args.trial_label or args.title,
            hypothesis_ids=tuple(args.hypothesis_id),
        )
    spec = PairedSpec(
        title=args.title,
        baseline_ruleset=args.baseline_ruleset,
        variant_ruleset=args.variant_ruleset,
        lever=args.lever,
        symbols=tuple(args.symbols.split(",")),
        start=args.start, end=args.end, evidence_role=args.evidence_role,
        min_edge_r=args.min_edge_r, confidence_level=args.confidence_level,
        bootstrap_replicates=args.bootstrap_replicates,
        bootstrap_seed=args.bootstrap_seed,
        warmup_sessions=args.warmup_sessions, capital=args.capital, trial=trial,
    )

    try:
        if spec.evidence_role == "confirmation":
            raise ConfirmationNotSupported(
                "confirmation runs need a multi-symbol registration; not implemented yet"
            )
        check_holdout(spec)
    except (HoldoutViolation, ConfirmationNotSupported) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    try:
        data_dir = Path(resolve_market_data_dir(args.data_dir))
    except MarketDataNotFoundError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    manifest_hash = None
    if args.dataset_manifest is not None:
        from vibe.research_pipeline.datasets.cache import verify_materialized_dataset

        ids = json.loads(args.dataset_manifest.read_text(encoding="utf-8"))
        for symbol in spec.symbols:
            verify_materialized_dataset(data_dir, symbol=symbol, dataset_id=ids[symbol])
        manifest_hash = _sha256(args.dataset_manifest)

    configs = {
        name: yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        for name, path in (
            ("baseline", spec.baseline_ruleset), ("variant", spec.variant_ruleset)
        )
    }
    calendar = SessionCalendar()
    segment = SessionSegment(
        role=spec.segment_role, start_session=spec.start, end_session=spec.end,
        session_count=calendar.session_count(spec.start, spec.end),
    )
    rulesets: dict[str, dict[str, Any]] = {"baseline": {}, "variant": {}}

    def runner(name: str):
        def run(symbol: str):
            cfg = json.loads(json.dumps(configs[name], default=str))
            cfg["instruments"]["symbols"] = [symbol]
            ruleset = StrategyRuleSet(**cfg)
            rulesets[name][symbol] = ruleset
            engine = BacktestEngine(
                ruleset=ruleset, data_dir=data_dir, initial_capital=spec.capital,
                execution_realism=ExecutionRealismConfig.realistic(),
            )
            return run_segment(
                engine, symbol, segment, warmup_sessions=spec.warmup_sessions
            ).result
        return run

    def sessions_for(symbol: str) -> list[str]:
        graded = [d.isoformat() for d in calendar.sessions_between(spec.start, spec.end)]
        present = _data_dates(data_dir, symbol)
        return [d for d in graded if present is None or d in present]

    try:
        result = run_paired_comparison(
            spec, runner("baseline"), runner("variant"), sessions_for
        )
    except HoldoutViolation as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    _print_summary(result)

    if not args.no_record:
        from vibe.research_pipeline.paths import research_db_path
        from vibe.research_pipeline.research_sink import (
            _repository_root,
            dataset_snapshot_id,
        )
        from vibe.research_pipeline.storage.sqlite_store import SqliteResearchStore

        store = SqliteResearchStore(
            args.research_db or research_db_path(create_parents=True)
        )
        try:
            sinks = {
                symbol: BacktestRunSink(
                    store, strategy_id=f"paired_{symbol.lower()}", symbol=symbol,
                    data_snapshot_id=dataset_snapshot_id(data_dir, symbol),
                    execution_model_version=EXECUTION_MODEL_VERSION,
                    methodology_version=METHODOLOGY_VERSION,
                    repo_root=_repository_root(), starting_equity=spec.capital,
                    retention=RetentionProfile.DIAGNOSTIC,
                )
                for symbol in spec.symbols
            }

            def first_hash(name: str) -> str:
                ruleset = next(iter(rulesets[name].values()), None)
                return hash_effective_ruleset(ruleset) if ruleset else ""

            definition = paired_definition(
                spec,
                baseline_ruleset_hash=first_hash("baseline"),
                variant_ruleset_hash=first_hash("variant"),
                dataset_manifest_hash=manifest_hash,
            )
            if args.registration is not None:
                definition["registration_sha256"] = _sha256(args.registration)
            experiment_id = record_paired_comparison(
                store, result, sinks=sinks,
                baseline_ruleset_for=rulesets["baseline"],
                variant_ruleset_for=rulesets["variant"],
                definition=definition,
            )
            print(f"\nExperiment: {experiment_id}")
        finally:
            store.close()
    return 2 if result.errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
