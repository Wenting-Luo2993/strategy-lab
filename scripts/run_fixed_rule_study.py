"""Run one fixed ruleset over a symbol set and report pooled expectancy in R.

Example:
    python scripts/run_fixed_rule_study.py --title "ORB baseline" \
        --symbols QQQ,SPY --start 2018-01-02 --end 2023-12-29 \
        --evidence-role exploration

Without ``--no-record`` the per-symbol runs and an experiment holding only the
aggregate summary are written to the research store. Runs are recorded without
a validator, so they rest at ``VALIDATING``.
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

logger = logging.getLogger("run_fixed_rule_study")

METHODOLOGY_VERSION = "fixed_rule/v1"
DEFAULT_RULESET = "vibe/rulesets/orb_production.yaml"


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--title", required=True)
    p.add_argument("--ruleset", default=DEFAULT_RULESET)
    p.add_argument("--symbols", required=True, help="Comma-separated symbols")
    p.add_argument("--start", required=True, type=date.fromisoformat)
    p.add_argument("--end", required=True, type=date.fromisoformat)
    p.add_argument(
        "--evidence-role",
        required=True,
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
    p.add_argument("--trial-family", default="baseline",
                   choices=("baseline", "condition", "enhancement", "validation"))
    p.add_argument("--trial-label", default=None)
    p.add_argument("--hypothesis-id", action="append", default=[])
    p.add_argument(
        "--dataset-manifest", type=Path, default=None,
        help="JSON file mapping SYMBOL -> dataset_id; each is verified against the data dir",
    )
    p.add_argument("--registration", type=Path, default=None,
                   help="Pre-registration file; its sha256 is stored in the definition")
    p.add_argument("--condition", action="append", default=[],
                   help="Session feature for a diagnostic tercile breakdown (repeatable)")
    p.add_argument("--no-record", action="store_true")
    return p.parse_args(argv)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _fmt(value: Any, digits: int = 4) -> str:
    return "n/a" if value is None else f"{value:.{digits}f}"


def _print_summary(result) -> None:
    s = result.summary
    pooled = s["pooled"]
    print("\n" + "=" * 72)
    print(f"{result.spec.title}  [{result.spec.evidence_role}]  -> {result.state}")
    print("=" * 72)
    print(
        f"Pooled n={pooled['n']} clusters={pooled['n_clusters']} "
        f"mean={_fmt(pooled['mean_r'])}R "
        f"CI=[{_fmt(pooled['ci_low_r'])}, {_fmt(pooled['ci_high_r'])}]"
    )
    print(f"Outcome vs {result.spec.min_edge_r}R edge: {s['outcome']}")
    print(f"Minimum detectable effect: {_fmt(s['mde_r'])}R")
    print("\nBy year:")
    for year, row in s["by_year"].items():
        print(f"  {year}  n={row['n']:<5} mean={_fmt(row['mean_r'])}R")
    for variable, block in s.get("conditions", {}).items():
        ix = block["interaction"]
        shown = (
            "n/a" if ix is None
            else f"{_fmt(ix['high_minus_low_r'])}R [{_fmt(ix['ci_low_r'])}, {_fmt(ix['ci_high_r'])}]"
        )
        print(f"\nCondition {variable} (diagnostic): high-low={shown} "
              f"monotonic={block['monotonic']} missing={block['n_missing']}")
    if s["errors"]:
        print("\nErrors:")
        for symbol, message in s["errors"].items():
            print(f"  {symbol}: {message}")


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
    from vibe.research_pipeline.fixed_rule import (
        ConfirmationNotSupported,
        FixedRuleSpec,
        check_holdout,
        record_fixed_rule_study,
        run_fixed_rule_study,
        study_definition,
    )
    from vibe.research_pipeline.holdout import HoldoutViolation
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
            programme=args.programme,
            phase=args.evidence_role,
            family=args.trial_family,
            label=args.trial_label or args.title,
            hypothesis_ids=tuple(args.hypothesis_id),
        )
    spec = FixedRuleSpec(
        title=args.title,
        ruleset_path=args.ruleset,
        symbols=tuple(args.symbols.split(",")),
        start=args.start,
        end=args.end,
        evidence_role=args.evidence_role,
        min_edge_r=args.min_edge_r,
        confidence_level=args.confidence_level,
        bootstrap_replicates=args.bootstrap_replicates,
        bootstrap_seed=args.bootstrap_seed,
        warmup_sessions=args.warmup_sessions,
        capital=args.capital,
        trial=trial,
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
        data_dir = resolve_market_data_dir(args.data_dir)
    except MarketDataNotFoundError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    manifest_hash = None
    if args.dataset_manifest is not None:
        from vibe.research_pipeline.datasets.cache import verify_materialized_dataset

        ids = json.loads(args.dataset_manifest.read_text(encoding="utf-8"))
        for symbol in spec.symbols:
            verify_materialized_dataset(
                data_dir, symbol=symbol, dataset_id=ids[symbol]
            )
        manifest_hash = _sha256(args.dataset_manifest)

    config = yaml.safe_load(Path(spec.ruleset_path).read_text(encoding="utf-8"))
    calendar = SessionCalendar()
    segment = SessionSegment(
        role=spec.segment_role,
        start_session=spec.start,
        end_session=spec.end,
        session_count=calendar.session_count(spec.start, spec.end),
    )

    rulesets: dict[str, Any] = {}

    def run_symbol(symbol: str):
        cfg = json.loads(json.dumps(config, default=str))
        cfg["instruments"]["symbols"] = [symbol]
        ruleset = StrategyRuleSet(**cfg)
        rulesets[symbol] = ruleset
        engine = BacktestEngine(
            ruleset=ruleset,
            data_dir=data_dir,
            initial_capital=spec.capital,
            execution_realism=ExecutionRealismConfig.realistic(),
        )
        return run_segment(
            engine, symbol, segment, warmup_sessions=spec.warmup_sessions
        ).result

    try:
        result = run_fixed_rule_study(spec, run_symbol)
    except HoldoutViolation as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    if args.condition:
        from vibe.research_pipeline.conditional import condition_blocks

        trades_by_symbol = {
            s: list(getattr(r, "trades", ()) or ())
            for s, r in result.per_symbol.items()
            if r is not None
        }
        reference = next(iter(rulesets.values()), None)
        try:
            result.summary["conditions"] = condition_blocks(
                data_dir,
                trades_by_symbol,
                args.condition,
                start=spec.start,
                end=spec.end,
                or_minutes=reference.strategy.orb_duration_minutes if reference else 5,
                timeframe=reference.instruments.timeframe if reference else "5m",
                confidence_level=spec.confidence_level,
                min_edge_r=spec.min_edge_r,
            )
        except ValueError as exc:
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
                    store,
                    strategy_id=f"fixed_rule_{symbol.lower()}",
                    symbol=symbol,
                    data_snapshot_id=dataset_snapshot_id(Path(data_dir), symbol),
                    execution_model_version=EXECUTION_MODEL_VERSION,
                    methodology_version=METHODOLOGY_VERSION,
                    repo_root=_repository_root(),
                    starting_equity=spec.capital,
                    retention=RetentionProfile.DIAGNOSTIC,
                )
                for symbol in spec.symbols
            }
            reference = next(iter(rulesets.values()), None)
            definition = study_definition(
                spec,
                effective_ruleset_hash=(
                    hash_effective_ruleset(reference) if reference else ""
                ),
                dataset_manifest_hash=manifest_hash,
            )
            if args.registration is not None:
                definition["registration_sha256"] = _sha256(args.registration)
            experiment_id = record_fixed_rule_study(
                store,
                result,
                sinks=sinks,
                ruleset_for=rulesets,
                definition=definition,
            )
            print(f"\nExperiment: {experiment_id}")
        finally:
            store.close()
    return 2 if result.errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
