#!/usr/bin/env python
"""Run a nested walk-forward: optimize on train, select on validation, test once.

This script exists because the capability it exposes had no caller. Every
component of the nested procedure is composed here --
``TemporalSplitPlanner``, ``Optimizer``, ``Selector``, ``RulesetSegmentExecutor``,
``MetricValidator``, and ``NestedWalkForwardDriver``. The former fixed-parameter
walk-forward path was deleted because it was not a valid replacement for this
procedure.

What this does differently, per fold:

1. Optimize **only** on that fold's TRAIN sessions.
2. Select on a dedicated VALIDATION window, using a plateau medoid rather than
   a bare argmax, and record the margin over the runner-up.
3. Freeze the selected candidate.
4. Evaluate it exactly once on the next unseen TEST window.
5. Advance by exchange sessions and repeat.

Purge and embargo gaps separate the three roles, and the driver audits that no
session appears in more than one role before any of it runs.

If any fold fails to complete, the whole result is INCONCLUSIVE and nothing is
stitched. Reporting the folds that happened to succeed would be survivorship
selection, which is the exact bias a walk-forward exists to measure.

Example
-------
    python scripts/run_nested_walk_forward.py \\
        --symbol QQQ \\
        --dev-start 2021-01-04 --dev-end 2023-12-29 \\
        --oos-start 2024-01-02 --oos-end 2024-12-31 \\
        --axis orb_minutes=strategy.orb_duration_minutes:5,15,30
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logger = logging.getLogger("nested_walk_forward")

DEFAULT_RULESET = Path("vibe/rulesets/orb_production.yaml")

# Bump when a change alters what a recorded fold *means*, not merely how fast
# it runs. Runs carrying different methodology versions are not comparable.
METHODOLOGY_VERSION = "nested_walk_forward/v1"


def _parse_axis(raw: str) -> tuple[str, str, tuple[Any, ...]]:
    """Parse ``name=dotted.path:v1,v2,v3`` into an axis definition.

    The dotted path is required rather than inferred: ``RulesetSegmentExecutor``
    refuses a path that does not already exist in the base ruleset, which is
    what turns a typo into an error instead of a silently ignored parameter.
    """
    if "=" not in raw or ":" not in raw.split("=", 1)[1]:
        raise argparse.ArgumentTypeError(
            f"--axis must look like name=dotted.path:v1,v2,v3 (got {raw!r})"
        )
    name, remainder = raw.split("=", 1)
    path, values_raw = remainder.rsplit(":", 1)
    values = tuple(_coerce(v.strip()) for v in values_raw.split(",") if v.strip())
    if not values:
        raise argparse.ArgumentTypeError(f"Axis {name!r} has no values")
    return name.strip(), path.strip(), values


def _coerce(token: str) -> Any:
    low = token.lower()
    if low in ("true", "false"):
        return low == "true"
    for cast in (int, float):
        try:
            return cast(token)
        except ValueError:
            continue
    return token


def _as_date(raw: str) -> date:
    return datetime.strptime(raw, "%Y-%m-%d").date()


# The leakage suite and the ORB boundary check live in
# vibe.research_pipeline.research_evidence so that this script and the
# sweep runner cannot drift into measuring different things. Two copies of a
# leakage check that disagree are worse than one.
from vibe.research_pipeline.research_evidence import (  # noqa: E402
    build_orb_boundary_report,
)


def build_leakage_report(symbol: str, data_dir: Path, spec) -> Any:
    """Measure leakage over the development window this plan was built from.

    Only the plan-specific part stays here: which window to measure. The
    measurement itself is shared.
    """
    from vibe.research_pipeline.research_evidence import (
        build_leakage_report as run_suite,
        load_session_bars,
    )

    bars = load_session_bars(symbol, data_dir, spec.dev_start, spec.dev_end)
    return run_suite(bars), bars


def build_fold_leakage(plan, calendar_name: str) -> dict:
    """Per-fold split-contamination evidence, keyed by segment.

    Computed from the plan rather than asserted, so a planner regression that
    let a test session into the training window would surface here as a named
    session rather than as a quietly better out-of-sample number.
    """
    from vibe.research_pipeline.contracts import SegmentRole
    from vibe.research_pipeline.evaluation import SegmentValidator
    from vibe.research_pipeline.features.leakage import check_split_contamination
    from vibe.research_pipeline.splits.calendar import SessionCalendar

    calendar = SessionCalendar(calendar_name)
    by_segment: dict = {}
    for manifest in plan.folds:
        roles = {segment.role: segment for segment in manifest.segments}
        graded = [
            roles[role]
            for role in (SegmentRole.TRAIN, SegmentRole.VALIDATION, SegmentRole.TEST)
            if role in roles
        ]
        sessions = {
            segment.role: calendar.sessions_between(
                segment.start_session, segment.end_session
            )
            for segment in graded
        }
        report = check_split_contamination(
            train_sessions=sessions.get(SegmentRole.TRAIN, ()),
            test_sessions=sessions.get(SegmentRole.TEST, ()),
            selection_sessions=sessions.get(SegmentRole.VALIDATION),
        )
        for segment in graded:
            by_segment[SegmentValidator.segment_key(segment)] = report
    return by_segment


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Nested walk-forward with per-fold optimization",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--symbol", default="QQQ")
    parser.add_argument("--ruleset", type=Path, default=DEFAULT_RULESET)
    parser.add_argument("--dev-start", type=_as_date, required=True)
    parser.add_argument("--dev-end", type=_as_date, required=True)
    parser.add_argument("--oos-start", type=_as_date, required=True)
    parser.add_argument("--oos-end", type=_as_date, required=True)
    parser.add_argument("--train-sessions", type=int, default=120)
    parser.add_argument("--validation-sessions", type=int, default=40)
    parser.add_argument("--test-sessions", type=int, default=40)
    parser.add_argument(
        "--step-sessions",
        type=int,
        default=None,
        help="Defaults to --test-sessions so TEST windows tile without overlap.",
    )
    parser.add_argument("--warmup-sessions", type=int, default=50)
    parser.add_argument("--capital", type=float, default=100_000.0)
    parser.add_argument("--min-trades", type=int, default=10)
    parser.add_argument(
        "--axis",
        action="append",
        default=[],
        metavar="NAME=PATH:V1,V2",
        help="Parameter axis to optimize, repeatable.",
    )
    parser.add_argument(
        "--skip-leakage",
        action="store_true",
        help=(
            "Skip the leakage suite. No fold can then reach COMPLETED, "
            "because absent required evidence is blocking rather than a "
            "pass. Plumbing diagnostics only."
        ),
    )
    parser.add_argument(
        "--record-runs",
        action="store_true",
        help=(
            "Record every evaluated segment into the local research store, "
            "labelled with its true segment role. Without this the only "
            "walk-forward that produces genuine out-of-sample evidence "
            "leaves no trace, and the viewer shows only in-sample runs."
        ),
    )
    parser.add_argument(
        "--record-test-only",
        action="store_true",
        help=(
            "With --record-runs, persist only the frozen TEST evaluations. "
            "Useful when the grid is large enough that recording every "
            "training candidate would bury the folds it produced."
        ),
    )
    parser.add_argument(
        "--strategy-id",
        default="orb",
        help="Strategy identifier recorded alongside each run.",
    )
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--experiment-title", help="Human-readable controlled-experiment name.")
    parser.add_argument(
        "--reliability-policy",
        type=Path,
        help="Optional JSON policy overrides applied to immutable Reliability Policy v1.",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    from vibe.backtester.data.paths import (
        MarketDataNotFoundError,
        resolve_market_data_dir,
    )
    from vibe.research_pipeline.contracts import SegmentRole
    from vibe.research_pipeline.evaluation import SegmentValidator
    from vibe.research_pipeline.features.leakage import LeakageReport
    from vibe.research_pipeline.lifecycle import RunState
    from vibe.research_pipeline.optimization import (
        Optimizer,
        ParameterAxis,
        RulesetSegmentExecutor,
        Selector,
        generate_grid,
    )
    from vibe.research_pipeline.splits.planner import (
        SplitPlanError,
        SplitSpec,
        TemporalSplitPlanner,
    )
    from vibe.research_pipeline.validation import ValidationProfile
    from vibe.research_pipeline.walk_forward import NestedWalkForwardDriver
    from vibe.research_pipeline.reliability import ReliabilityPolicy

    if not args.axis:
        parser.error(
            "At least one --axis is required. A walk-forward with nothing to "
            "optimize is the defect this script exists to replace."
        )

    try:
        data_dir = resolve_market_data_dir()
    except MarketDataNotFoundError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        print("Run: python scripts/convert_databento.py", file=sys.stderr)
        return 1

    parsed = [_parse_axis(raw) for raw in args.axis]
    parameter_paths = {name: path for name, path, _ in parsed}
    candidates = generate_grid(
        tuple(ParameterAxis(name, values) for name, _, values in parsed)
    )

    spec = SplitSpec(
        dev_start=args.dev_start,
        dev_end=args.dev_end,
        final_oos_start=args.oos_start,
        final_oos_end=args.oos_end,
        train_sessions=args.train_sessions,
        validation_sessions=args.validation_sessions,
        test_sessions=args.test_sessions,
        step_sessions=args.step_sessions or args.test_sessions,
        indicator_lookback_sessions=args.warmup_sessions,
    )

    try:
        plan = TemporalSplitPlanner().plan(spec)
    except SplitPlanError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    logger.info(
        "Plan %s: %d fold(s), %d candidate(s), %d engine runs",
        plan.plan_hash[:12],
        len(plan.folds),
        len(candidates),
        len(plan.folds) * (2 * len(candidates) + 1),
    )

    bars = None
    fold_leakage = build_fold_leakage(plan, spec.calendar_name)
    if args.skip_leakage:
        leakage_report = LeakageReport(())
        logger.warning(
            "Leakage suite skipped. The profile's required checks will be "
            "absent, which is BLOCKING, so no fold can reach COMPLETED. Use "
            "this only to exercise plumbing."
        )
    else:
        leakage_report, bars = build_leakage_report(args.symbol, data_dir, spec)
        import yaml

        with Path(args.ruleset).open("r", encoding="utf-8") as handle:
            ruleset_config = yaml.safe_load(handle)
        orb_report = build_orb_boundary_report(bars, ruleset_config)
        leakage_report = LeakageReport(
            leakage_report.findings + orb_report.findings
        )
        logger.info(
            "Leakage suite: %d finding(s), passed=%s",
            len(leakage_report.findings),
            leakage_report.passed,
        )
        for failure in leakage_report.failures:
            logger.error("LEAKAGE %s", failure)

    executor = RulesetSegmentExecutor.from_yaml(
        args.ruleset,
        parameter_paths,
        data_dir=data_dir,
        warmup_sessions=args.warmup_sessions,
        engine_kwargs={"initial_capital": args.capital},
    )
    validator = SegmentValidator(
        ValidationProfile(
            name="nested_walk_forward",
            min_trades=args.min_trades,
            min_sessions=1,
        ),
        leakage_report,
        starting_equity=args.capital,
        bars_by_symbol={args.symbol: bars} if bars is not None else None,
        leakage_by_segment=fold_leakage,
    )

    def score(backtest_result: Any) -> float:
        # The scorer receives the inner BacktestResult, while the validator
        # receives the enclosing SegmentResult. Confusing the two fails loudly
        # as an AttributeError rather than silently scoring the wrong object.
        return float(backtest_result.overall.expectancy_r)

    policy_overrides = {}
    if args.reliability_policy:
        import json
        policy_overrides = json.loads(args.reliability_policy.read_text(encoding="utf-8"))
    reliability_policy = ReliabilityPolicy(**policy_overrides)

    sink = None
    experiment = None
    if args.record_runs:
        from vibe.research_pipeline.research_sink import open_run_sink
        from vibe.research_pipeline.recording_executor import (
            RecordingSegmentExecutor,
        )
        from vibe.research_pipeline.experiments import ExperimentRecorder

        recorded_validator = validator.for_recorded_segments(
            segment
            for fold in plan.folds
            for segment in fold.segments
        )
        sink = open_run_sink(
            data_dir=data_dir,
            symbol=args.symbol,
            strategy_id=args.strategy_id,
            starting_equity=args.capital,
            methodology_version=METHODOLOGY_VERSION,
            validator=recorded_validator,
        )
        roles = (
            frozenset({SegmentRole.TEST})
            if args.record_test_only
            else frozenset(SegmentRole)
        )
        experiment = ExperimentRecorder(
            sink.store,
            title=args.experiment_title or f"{args.symbol} {args.strategy_id.upper()} - " + ", ".join(
                f"{name}: {' / '.join(map(str, values))}" for name, _, values in parsed
            ),
            plan=plan, candidates=candidates,
            axes=[{"name": name, "path": path, "values": list(values)}
                  for name, path, values in parsed],
            symbol=args.symbol, strategy_id=args.strategy_id,
            methodology_version=METHODOLOGY_VERSION,
            record_test_only=args.record_test_only,
            reference_configuration=executor.effective_ruleset(candidates[0]).model_dump(mode="json"),
            reliability_policy=reliability_policy,
        )
        reliability_policy = experiment.reliability_policy
        executor = RecordingSegmentExecutor(
            executor, sink, record_roles=roles, on_record=experiment.record_run,
        )
        logger.info(
            "Recording %s segments into the research store.",
            "TEST" if args.record_test_only else "all",
        )

    driver = NestedWalkForwardDriver(
        Optimizer(executor, validator, score),
        Selector(executor, validator, score),
        executor,
        validator,
        reliability_policy=reliability_policy,
        symbols=(args.symbol,),
    )

    try:
        result = driver.run(plan, candidates)
    except Exception as exc:
        if experiment is not None:
            sink.store.finish_experiment(experiment.experiment_id, "execution_failed", {
                "reason": f"{type(exc).__name__}: {exc}",
                "recording_errors": [*sink.failures, *experiment.recording_errors],
            })
        raise
    if experiment is not None:
        experiment.finish(result, recording_errors=sink.failures)
        logger.info("Experiment: %s", experiment.experiment_id)
    _report(result, RunState)
    if sink is not None:
        logger.info("Research store: %s", sink.summary())
        for failure in sink.failures:
            logger.warning("RECORDING %s", failure)
    return 0 if result.state is RunState.COMPLETED else 2


def _report(result, RunState) -> None:
    print("\n" + "=" * 72)
    print(f"Nested walk-forward {result.plan_hash[:12]}  ->  {result.state.value}")
    print("=" * 72)
    print(f"Folds completed: {result.n_folds_completed}/{result.n_folds_expected}")
    if result.reason:
        print(f"Reason: {result.reason}")
    if result.reliability_report is not None:
        print(
            "Research reliability: "
            f"{result.reliability_report.verdict.value} "
            f"(policy {result.reliability_report.policy_hash[:12]})"
        )

    for fold in result.folds:
        print(f"\n-- Fold {fold.fold_index} [{fold.state.value}]")
        if fold.optimization is not None:
            batch = fold.optimization
            print(
                f"   TRAIN grid        : {batch.n_candidates_evaluated}"
                f"/{batch.n_candidates_expected} evaluated, "
                f"{batch.n_candidates_eligible} eligible"
            )
            # A grid with holes is the failure mode plan section 10 singles
            # out, so name every hole rather than reporting only the count.
            for failure in batch.failures:
                print(
                    f"   ! {failure.candidate_id} failed in {failure.phase}: "
                    f"{failure.message}"
                )
            for evaluation in batch.evaluations:
                report = evaluation.validation_report
                if report.target_state is RunState.COMPLETED:
                    continue
                codes = ", ".join(
                    f"{f.code}({f.severity.value})" for f in report.findings
                ) or "no findings"
                print(
                    f"   ! {evaluation.candidate.parameter_map} -> "
                    f"{report.target_state.value}: {codes}"
                )
        # VALIDATION-stage outcomes. Without these a fold that refuses to
        # select can only say "non-selectable outcomes", which names the
        # category but not the gate -- leaving the operator unable to tell a
        # genuine signal from a threshold that needs calibration.
        for failure in fold.validation_failures:
            print(
                f"   ! {failure.candidate_id} failed in {failure.phase}: "
                f"{failure.message}"
            )
        for evaluation in fold.validation_evaluations:
            report = evaluation.validation_report
            if report.target_state is RunState.COMPLETED:
                continue
            codes = ", ".join(
                f"{f.code}({f.severity.value})" for f in report.findings
            ) or "no findings"
            print(
                f"   ! VALIDATION {evaluation.candidate.parameter_map} -> "
                f"{report.target_state.value}: {codes}"
            )
            # A bare code cannot be triaged. Whether a plausibility gate is a
            # real signal or a small-sample artifact depends entirely on the
            # observed value and the sample it was measured over.
            for finding in report.findings:
                print(
                    f"       {finding.code}: {finding.metric_key}="
                    f"{finding.observed!r} expected {finding.expected!r}"
                )
        if (
            fold.test_validation is not None
            and fold.test_validation.target_state is not RunState.COMPLETED
        ):
            codes = ", ".join(
                f"{f.code}({f.severity.value})"
                for f in fold.test_validation.findings
            ) or "no findings"
            print(
                f"   ! TEST -> {fold.test_validation.target_state.value}: {codes}"
            )
        if fold.selection is None:
            print(f"   no selection: {fold.reason}")
            continue
        selection = fold.selection
        print(f"   parameters        : {selection.selected.candidate.parameter_map}")
        print(
            f"   candidates        : {selection.n_candidates_evaluated} evaluated, "
            f"{selection.n_candidates_eligible} eligible"
        )
        print(f"   validation score  : {selection.winner_score:.4f}")
        print(f"   selection margin  : {selection.selection_margin}")
        print(f"   plateau size      : {len(selection.neighborhood_candidate_ids)}")
        if fold.test_result is not None:
            overall = fold.test_result.result.overall
            print(
                f"   TEST (unseen)     : {overall.n_trades} trades, "
                f"expectancy {overall.expectancy_r:.4f}R, "
                f"pnl ${overall.total_pnl:,.2f}"
            )

    if result.stitched_oos is None:
        print(
            "\nNo stitched out-of-sample result. Folds are only stitched when "
            "every one of them completes; reporting the survivors would be "
            "survivorship selection."
        )
        return

    stitched = result.stitched_oos
    print("\n" + "-" * 72)
    print("Stitched out-of-sample (non-overlapping TEST windows only)")
    print("-" * 72)
    print(f"  folds      : {stitched.n_folds}")
    print(f"  trades     : {stitched.n_trades}")
    print(f"  expectancy : {stitched.expectancy_r:.4f}R")
    print(f"  window     : {stitched.first_session} .. {stitched.last_session}")


if __name__ == "__main__":
    sys.exit(main())
