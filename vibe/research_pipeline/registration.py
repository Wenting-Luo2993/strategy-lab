"""Fail-closed validation for machine-readable research registrations."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import date
from pathlib import Path
from typing import Any, Mapping, Sequence

import yaml

from vibe.backtester.analysis.metrics import METRIC_CALCULATION_VERSION
from vibe.backtester.core.execution_realism import EXECUTION_MODEL_VERSION
from vibe.research_pipeline.holdout import load_lock
from vibe.research_pipeline.reliability import ReliabilityPolicy
from vibe.research_pipeline.splits.planner import SplitSpec, TemporalSplitPlanner
from vibe.research_pipeline.storage.schema import SCHEMA_VERSION


class RegistrationError(ValueError):
    """Raised when an execution request diverges from its registration."""


def load_registration(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RegistrationError(f"Cannot load registration {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise RegistrationError("Registration root must be a JSON object")
    return payload


def validate_registration_execution(
    registration: Mapping[str, Any],
    *,
    repository_root: Path,
    symbol: str,
    ruleset_path: Path,
    axes: Sequence[tuple[str, str, Sequence[Any]]],
    dev_start: date,
    dev_end: date,
    oos_start: date,
    oos_end: date,
    train_sessions: int,
    validation_sessions: int,
    test_sessions: int,
    step_sessions: int,
    warmup_sessions: int,
    record_runs: bool,
    record_test_only: bool,
    reliability_policy: ReliabilityPolicy,
    dataset_manifest_path: Path | None,
    data_dir_path: Path | None,
    research_db_path: Path | None,
) -> None:
    if registration.get("registration_version") != 1:
        raise RegistrationError("Unsupported registration version")
    if registration.get("evidence_label") != "exploratory_reused_development_evidence":
        raise RegistrationError("Evidence label differs from the approved registration")
    if registration.get("confirmation_claim_permitted") is not False:
        raise RegistrationError("Exploratory registration cannot permit confirmation claims")

    strategy = _mapping(registration, "strategy")
    if strategy.get("symbol") != symbol:
        raise RegistrationError("Execution symbol differs from registration")

    registered_ruleset = repository_root / _text(strategy, "ruleset_path")
    requested_ruleset = ruleset_path.resolve()
    if registered_ruleset.resolve() != requested_ruleset:
        raise RegistrationError("Execution ruleset path differs from registration")
    digest = hashlib.sha256(requested_ruleset.read_bytes()).hexdigest()
    if digest != _text(strategy, "ruleset_sha256"):
        raise RegistrationError("Execution ruleset hash differs from registration")
    ruleset = yaml.safe_load(requested_ruleset.read_text(encoding="utf-8"))
    direction = _mapping(_mapping(ruleset, "strategy"), "direction_mode", scalar=True)
    if direction != strategy.get("direction_mode"):
        raise RegistrationError("Effective direction differs from registration")

    registered_axes = registration.get("candidate_axes")
    requested_axes = [
        {"name": name, "path": path, "values": list(values)}
        for name, path, values in axes
    ]
    if registered_axes != requested_axes:
        raise RegistrationError("Candidate axes differ from registration")

    split = _mapping(registration, "split_policy")
    requested_split = {
        "train_sessions": train_sessions,
        "validation_sessions": validation_sessions,
        "test_sessions": test_sessions,
        "step_sessions": step_sessions,
        "warmup_sessions": warmup_sessions,
    }
    for key, value in requested_split.items():
        if split.get(key) != value:
            raise RegistrationError(f"Split setting {key} differs from registration")

    policy = _mapping(registration, "reliability_policy")
    if policy.get("version") != reliability_policy.version:
        raise RegistrationError("Reliability policy version differs from registration")
    if policy.get("policy_hash") != reliability_policy.policy_hash:
        raise RegistrationError("Reliability policy hash differs from registration")

    execution = _mapping(registration, "execution_identity")
    if execution.get("execution_model_version") != EXECUTION_MODEL_VERSION:
        raise RegistrationError("Execution-model version differs from registration")
    if execution.get("metric_calculation_version") != METRIC_CALCULATION_VERSION:
        raise RegistrationError("Metric-calculation version differs from registration")

    recording = _mapping(registration, "recording")
    if recording.get("record_all_roles") is not record_runs:
        raise RegistrationError("Run-recording requirement differs from registration")
    if recording.get("record_test_only") is not record_test_only:
        raise RegistrationError("TEST-only recording differs from registration")

    regimes = _mapping(registration, "regimes")
    if regimes.get("candidate_axis") is not False or regimes.get("entry_filter") is not False:
        raise RegistrationError("Registered regimes must remain reporting-only")

    holdout = _mapping(registration, "final_holdout")
    holdout_path = repository_root / _text(holdout, "config_path")
    lock = load_lock(holdout_path)
    if holdout.get("lock_hash") != lock.lock_hash:
        raise RegistrationError("Final-holdout lock hash differs from registration")
    if holdout.get("start") != lock.oos_start.isoformat():
        raise RegistrationError("Registered final-holdout start differs from lock")
    if holdout.get("end") != lock.oos_end.isoformat():
        raise RegistrationError("Registered final-holdout end differs from lock")
    if oos_start != lock.oos_start or oos_end != lock.oos_end:
        raise RegistrationError("Declared final-holdout dates differ from registration")
    if dev_end >= lock.oos_start or dev_start > dev_end:
        raise RegistrationError("Development dates overlap or invert the final holdout")

    gate = _mapping(registration, "execution_gate")
    gate_state = gate.get("state")
    if gate_state not in {"blocked", "ready"}:
        raise RegistrationError("Registration execution gate state is invalid")
    if gate_state == "ready":
        _validate_ready_bindings(
            registration,
            symbol=symbol,
            dataset_manifest_path=dataset_manifest_path,
            data_dir_path=data_dir_path,
            research_db_path=research_db_path,
            expected_split=_split_binding(
                dev_start=dev_start,
                dev_end=dev_end,
                oos_start=oos_start,
                oos_end=oos_end,
                train_sessions=train_sessions,
                validation_sessions=validation_sessions,
                test_sessions=test_sessions,
                step_sessions=step_sessions,
                warmup_sessions=warmup_sessions,
            ),
        )
    else:
        reasons = gate.get("reasons")
        detail = "; ".join(str(reason) for reason in reasons) if isinstance(reasons, list) else ""
        raise RegistrationError(f"Registration execution gate is not ready: {detail}")


def _validate_ready_bindings(
    registration: Mapping[str, Any],
    *,
    symbol: str,
    dataset_manifest_path: Path | None,
    data_dir_path: Path | None,
    research_db_path: Path | None,
    expected_split: Mapping[str, Any],
) -> None:
    split = _mapping(registration, "split_policy")
    if split.get("exact_calendar_boundaries") != expected_split["boundaries"]:
        raise RegistrationError("Registered fold boundaries differ from generated plan")
    if split.get("fold_count") != expected_split["fold_count"]:
        raise RegistrationError("Registered fold count differs from generated plan")
    if split.get("plan_hash") != expected_split["plan_hash"]:
        raise RegistrationError("Registered split-plan hash differs from generated plan")

    binding = _mapping(registration, "data_binding")
    if dataset_manifest_path is None:
        raise RegistrationError("Ready registration requires --dataset-manifest")
    try:
        manifest_bytes = dataset_manifest_path.read_bytes()
    except OSError as exc:
        raise RegistrationError(f"Cannot read dataset manifest: {exc}") from exc
    manifest_digest = hashlib.sha256(manifest_bytes).hexdigest()
    if manifest_digest != _text(binding, "manifest_sha256"):
        raise RegistrationError("Dataset manifest hash differs from registration")
    try:
        manifest = json.loads(manifest_bytes)
    except json.JSONDecodeError as exc:
        raise RegistrationError(f"Cannot load dataset manifest: {exc}") from exc
    if not isinstance(manifest, Mapping):
        raise RegistrationError("Dataset manifest root must be an object")
    if manifest.get("manifest_id") != binding.get("manifest_id"):
        raise RegistrationError("Dataset manifest identity differs from registration")
    if manifest.get("session_sequence_sha256") != binding.get("session_sequence_sha256"):
        raise RegistrationError("Dataset session sequence differs from registration")
    symbols = manifest.get("symbols")
    if not isinstance(symbols, list) or symbol not in symbols:
        raise RegistrationError("Dataset manifest does not cover the registered symbol")
    if data_dir_path is None:
        raise RegistrationError("Ready registration requires --data-dir")
    files = manifest.get("files")
    if not isinstance(files, Mapping):
        raise RegistrationError("Dataset manifest must contain file identities")
    file_identity = files.get(symbol)
    if not isinstance(file_identity, Mapping):
        raise RegistrationError("Dataset manifest lacks the registered symbol file")
    relative_path = _text(file_identity, "path")
    data_file = data_dir_path / relative_path
    try:
        data_digest = hashlib.sha256(data_file.read_bytes()).hexdigest()
    except OSError as exc:
        raise RegistrationError(f"Cannot read registered dataset file: {exc}") from exc
    if data_digest != _text(file_identity, "sha256"):
        raise RegistrationError("Dataset file hash differs from manifest")

    database = _mapping(registration, "research_database")
    if research_db_path is None:
        raise RegistrationError("Ready registration requires --research-db")
    if research_db_path.resolve() != Path(_text(database, "path")).resolve():
        raise RegistrationError("Research database path differs from registration")
    if not research_db_path.is_file():
        raise RegistrationError("Registered research database does not exist")
    _validate_research_database(research_db_path)


def _split_binding(
    *,
    dev_start: date,
    dev_end: date,
    oos_start: date,
    oos_end: date,
    train_sessions: int,
    validation_sessions: int,
    test_sessions: int,
    step_sessions: int,
    warmup_sessions: int,
) -> dict[str, Any]:
    plan = TemporalSplitPlanner().plan(
        SplitSpec(
            dev_start=dev_start,
            dev_end=dev_end,
            final_oos_start=oos_start,
            final_oos_end=oos_end,
            train_sessions=train_sessions,
            validation_sessions=validation_sessions,
            test_sessions=test_sessions,
            step_sessions=step_sessions,
            indicator_lookback_sessions=warmup_sessions,
        )
    )
    return {
        "plan_hash": plan.plan_hash,
        "fold_count": len(plan.folds),
        "boundaries": [
            fold.model_dump(mode="json")
            for fold in plan.folds
        ],
    }


def _validate_research_database(path: Path) -> None:
    try:
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        integrity = connection.execute("PRAGMA integrity_check").fetchone()
        if integrity is None or integrity[0] != "ok":
            raise RegistrationError("Research database integrity check failed")
        row = connection.execute(
            "SELECT COALESCE(MAX(version), 0) FROM schema_migrations"
        ).fetchone()
        if row is None or int(row[0]) != SCHEMA_VERSION:
            raise RegistrationError(
                "Research database schema version differs from this binary"
            )
    except sqlite3.Error as exc:
        raise RegistrationError(f"Cannot validate research database: {exc}") from exc
    finally:
        if "connection" in locals():
            connection.close()


def _mapping(
    parent: Mapping[str, Any],
    key: str,
    *,
    scalar: bool = False,
) -> Any:
    value = parent.get(key)
    if scalar:
        if isinstance(value, (dict, list)) or value is None:
            raise RegistrationError(f"Registration field {key!r} is invalid")
        return value
    if not isinstance(value, Mapping):
        raise RegistrationError(f"Registration field {key!r} must be an object")
    return value


def _text(parent: Mapping[str, Any], key: str) -> str:
    value = parent.get(key)
    if not isinstance(value, str) or not value:
        raise RegistrationError(f"Registration field {key!r} must be text")
    return value
