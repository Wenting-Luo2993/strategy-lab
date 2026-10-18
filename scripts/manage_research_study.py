#!/usr/bin/env python
"""Freeze a study or execute its authorized, exactly-once final holdout."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _store(path: Path | None):
    from vibe.research_pipeline.storage.sqlite_store import SqliteResearchStore
    return SqliteResearchStore(path)


def freeze(args: argparse.Namespace) -> int:
    from vibe.research_pipeline.methodology import FrozenStudyDefinition

    raw = json.loads(args.definition.read_text(encoding="utf-8"))
    definition = FrozenStudyDefinition.freeze(
        **raw,
        frozen_by=args.frozen_by,
    )
    store = _store(args.db)
    try:
        identity = store.freeze_study(args.experiment_id, definition)
    finally:
        store.close()
    print(identity)
    return 0


def authorize_final_holdout(args: argparse.Namespace) -> int:
    from vibe.research_pipeline.methodology import FinalHoldoutAuthorization

    authorization = FinalHoldoutAuthorization(
        token=args.authorization_token,
        requested_by=args.requested_by,
        reason=args.reason,
    )
    store = _store(args.db)
    try:
        store.authorize_final_holdout(args.experiment_id, authorization)
    finally:
        store.close()
    print("authorized")
    return 0


def _validate_frozen_runtime(frozen, *, ruleset_raw, candidate, data_dir, lock):
    from vibe.backtester.analysis.metrics import METRIC_CALCULATION_VERSION
    from vibe.backtester.core.execution_realism import EXECUTION_MODEL_VERSION
    from vibe.research_pipeline.datasets import verify_materialized_dataset
    from vibe.research_pipeline.hashing import hash_object
    declared_ruleset = dict(frozen.ruleset_identity)
    expected_ruleset_hash = (
        declared_ruleset.get("sha256")
        or declared_ruleset.get("content_sha256")
        or declared_ruleset.get("ruleset_content_sha256")
    )
    actual_ruleset_hash = hash_object(ruleset_raw)
    if expected_ruleset_hash != actual_ruleset_hash:
        raise ValueError(
            "Frozen ruleset identity does not match the supplied ruleset content"
        )

    symbols = tuple(
        str(symbol).upper()
        for symbol in dict(frozen.universe_identity).get("symbols", ())
    )
    runtime_symbols = tuple(
        str(symbol).upper()
        for symbol in ruleset_raw.get("instruments", {}).get("symbols", ())
    )
    if not symbols or runtime_symbols != symbols:
        raise ValueError(
            f"Frozen universe {symbols!r} does not equal runtime universe "
            f"{runtime_symbols!r}"
        )
    if tuple(symbol.upper() for symbol in lock.symbols) != symbols:
        raise ValueError("Frozen universe does not equal the committed holdout universe")

    axes = tuple(dict(axis) for axis in frozen.parameter_axes)
    axis_names = tuple(str(axis["name"]) for axis in axes)
    if set(axis_names) != set(candidate.parameter_map):
        raise ValueError("Frozen parameter axes do not match the final candidate")
    if len(candidate.coordinates) != len(axes):
        raise ValueError("Frozen candidate coordinates do not match parameter axes")
    for coordinate, axis in zip(candidate.coordinates, axes):
        values = tuple(axis.get("values", ()))
        if coordinate < 0 or coordinate >= len(values):
            raise ValueError("Frozen candidate coordinate is outside its parameter axis")
        if candidate.parameter_map[axis["name"]] not in tuple(axis.get("values", ())):
            raise ValueError(
                f"Final candidate value for {axis['name']!r} is outside the frozen axis"
            )
        if values[coordinate] != candidate.parameter_map[axis["name"]]:
            raise ValueError(
                f"Frozen candidate coordinate for {axis['name']!r} does not "
                "identify the frozen parameter value"
            )

    if frozen.execution_model_version != EXECUTION_MODEL_VERSION:
        raise ValueError("Frozen execution-model version differs from this runtime")
    if frozen.metric_calculation_version != METRIC_CALCULATION_VERSION:
        raise ValueError("Frozen metric-calculation version differs from this runtime")
    if frozen.final_holdout_lock_hash != lock.lock_hash:
        raise ValueError("Committed holdout lock differs from the frozen lock")

    dataset_ids = tuple(frozen.dataset_ids)
    if len(dataset_ids) not in {1, len(symbols)}:
        raise ValueError("Frozen dataset IDs must contain one ID or one per symbol")
    verified_datasets = {}
    for index, symbol in enumerate(symbols):
        dataset_id = dataset_ids[0] if len(dataset_ids) == 1 else dataset_ids[index]
        verify_materialized_dataset(data_dir, symbol=symbol, dataset_id=dataset_id)
        verified_datasets[symbol] = dataset_id

    return {
        "definition_hash": frozen.definition_hash,
        "ruleset_hash": actual_ruleset_hash,
        "universe_identity": dict(frozen.universe_identity),
        "symbols": list(symbols),
        "dataset_ids": verified_datasets,
        "candidate_id": candidate.candidate_id,
        "candidate_parameters": dict(candidate.parameter_map),
        "candidate_coordinates": list(candidate.coordinates),
        "parameter_axes": [dict(axis) for axis in axes],
        "execution_model_version": EXECUTION_MODEL_VERSION,
        "metric_calculation_version": METRIC_CALCULATION_VERSION,
        "lock_hash": lock.lock_hash,
        "reliability_policy_hash": frozen.reliability_policy.policy_hash,
    }


def final_holdout(args: argparse.Namespace) -> int:
    import yaml

    from vibe.backtester.core.integrity import EngineIntegrityStatus
    from vibe.backtester.core.execution_realism import EXECUTION_MODEL_VERSION
    from vibe.research_pipeline.contracts import (
        RetentionProfile,
        SegmentRole,
        SessionSegment,
    )
    from vibe.research_pipeline.holdout import (
        UnlockToken,
        load_lock,
        record_access,
    )
    from vibe.research_pipeline.methodology import acceptance_verdict
    from vibe.research_pipeline.optimization import CandidateSpec, RulesetSegmentExecutor
    from vibe.research_pipeline.recording import (
        RunRecorder,
        build_run_fingerprint,
        hash_effective_ruleset,
    )
    from vibe.research_pipeline.research_evidence import build_study_validator
    from vibe.research_pipeline.splits.calendar import SessionCalendar
    from vibe.backtester.data.paths import resolve_market_data_dir

    store = _store(args.db)
    claim = None
    try:
        # This is deliberately the first operation after opening the database.
        # It atomically burns the one-use authorization before ruleset, lock, or
        # market-data files are read.
        claim = store.claim_final_holdout(
            args.experiment_id, args.authorization_token
        )
        frozen = store.get_frozen_study(args.experiment_id)
        if frozen is None:
            raise SystemExit("Refusing final holdout: study is not frozen")
        lock = load_lock()
        ruleset_raw = yaml.safe_load(args.ruleset.read_text(encoding="utf-8"))
        if not isinstance(ruleset_raw, dict):
            raise ValueError("Ruleset must contain a mapping")
        data_dir = resolve_market_data_dir(args.data_dir)
        declared = dict(frozen.ruleset_identity)
        parameter_paths = dict(declared.get("parameter_paths") or {})
        parameters = dict(declared.get("candidate_parameters") or {})
        candidate = CandidateSpec.create(
            parameters,
            tuple(int(value) for value in declared.get("candidate_coordinates", ())),
        )
        verified_identities = _validate_frozen_runtime(
            frozen,
            ruleset_raw=ruleset_raw,
            candidate=candidate,
            data_dir=data_dir,
            lock=lock,
        )
        if len(verified_identities["symbols"]) != 1:
            raise ValueError(
                "This final-holdout command requires a single-symbol frozen "
                "universe; multi-symbol studies must use the portfolio executor"
            )
        executor = RulesetSegmentExecutor(
            ruleset_raw,
            parameter_paths,
            data_dir=data_dir,
            warmup_sessions=int(dict(frozen.warmup_policy).get("sessions", 0)),
            engine_kwargs={"initial_capital": args.capital},
            holdout_token=claim,
        )
        holdout_sessions = SessionCalendar().sessions_between(
            lock.oos_start, lock.oos_end
        )
        segment = SessionSegment(
            role=SegmentRole.FINAL_OOS,
            start_session=holdout_sessions[0],
            end_session=holdout_sessions[-1],
            session_count=len(holdout_sessions),
        )
        effective_ruleset = executor.effective_ruleset(candidate)
        verified_identities["effective_ruleset_hash"] = hash_effective_ruleset(
            effective_ruleset
        )
        fingerprint = build_run_fingerprint(
            strategy_id=frozen.strategy_id,
            ruleset_sha256=hash_effective_ruleset(effective_ruleset),
            parameters=dict(candidate.parameter_map),
            symbol=verified_identities["symbols"][0],
            first_session=lock.oos_start,
            last_session=lock.oos_end,
            session_count=segment.session_count,
            data_snapshot_id=next(iter(verified_identities["dataset_ids"].values())),
            execution_model_version=EXECUTION_MODEL_VERSION,
            metric_calculation_version=frozen.metric_calculation_version,
            repo_root=Path(__file__).resolve().parent.parent,
            segment_role=SegmentRole.FINAL_OOS,
        )
        with RunRecorder(
            store,
            fingerprint=fingerprint,
            methodology_version=f"final-holdout/{frozen.version}",
            retention=RetentionProfile.FULL,
            owner="final_holdout",
            segment_role=SegmentRole.FINAL_OOS,
            notes=f"Exactly-once final holdout for {args.experiment_id}",
        ) as recorder:
            store.protect_run_detail(
                recorder.run_id,
                reason="final holdout evidence is retained indefinitely",
            )
            record_access(
                lock=lock,
                token=UnlockToken(
                    reason=claim.reason,
                    requested_by=claim.requested_by,
                    run_id=recorder.run_id,
                    log_access=False,
                ),
                symbol=verified_identities["symbols"][0],
                start=lock.oos_start,
                end=lock.oos_end,
            )
            validator = build_study_validator(
                symbol=verified_identities["symbols"][0],
                data_dir=data_dir,
                start=lock.oos_start,
                end=lock.oos_end,
                ruleset_config=ruleset_raw,
                starting_equity=args.capital,
                holdout_token=claim,
            )
            recorder.set_validator(validator)
            outcome = executor(candidate, segment)
            run_id = recorder.record(
                outcome.result, starting_equity=args.capital
            )
        integrity = outcome.result.engine_integrity_report
        if integrity is None or integrity.status is not EngineIntegrityStatus.PASS:
            metric_value = float("nan")
            acceptance = type("Outcome", (), {
                "passed": False,
                "conclusive": False,
                "detail": "Engine integrity did not pass",
            })()
        else:
            metric_value = float(
                getattr(outcome.result.overall, lock.acceptance.metric)
            )
            acceptance = lock.acceptance.evaluate(
                metric_value, outcome.result.overall.n_trades
            )
        verdict = acceptance_verdict(acceptance)
        store.record_final_holdout(
            args.experiment_id,
            claim,
            verdict=verdict,
            run_id=run_id,
            verified_identities=verified_identities,
            payload={
                "lock_hash": lock.lock_hash,
                "definition_hash": frozen.definition_hash,
                "metric": lock.acceptance.metric,
                "metric_value": metric_value,
                "n_trades": outcome.result.overall.n_trades,
                "acceptance_detail": acceptance.detail,
                "engine_integrity": integrity.status.value if integrity else None,
                "run_id": run_id,
            },
        )
    except BaseException as exc:
        if claim is not None:
            try:
                store.interrupt_final_holdout(
                    claim, f"{type(exc).__name__}: {exc}"
                )
            except Exception:
                pass
        raise
    finally:
        store.close()
    print(verdict)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path)
    sub = parser.add_subparsers(required=True)
    freeze_parser = sub.add_parser("freeze")
    freeze_parser.add_argument("experiment_id")
    freeze_parser.add_argument("definition", type=Path)
    freeze_parser.add_argument("--frozen-by", default="Bloom")
    freeze_parser.set_defaults(func=freeze)
    authorize_parser = sub.add_parser("authorize-final-holdout")
    authorize_parser.add_argument("experiment_id")
    authorize_parser.add_argument("--authorization-token", required=True)
    authorize_parser.add_argument("--requested-by", default="Bloom")
    authorize_parser.add_argument("--reason", required=True)
    authorize_parser.set_defaults(func=authorize_final_holdout)
    final_parser = sub.add_parser("final-holdout")
    final_parser.add_argument("experiment_id")
    final_parser.add_argument("--ruleset", type=Path, required=True)
    final_parser.add_argument("--data-dir", type=Path)
    final_parser.add_argument("--capital", type=float, default=100_000.0)
    final_parser.add_argument("--authorization-token", required=True)
    final_parser.set_defaults(func=final_holdout)
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
