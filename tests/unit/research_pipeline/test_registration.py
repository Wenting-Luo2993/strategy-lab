from __future__ import annotations

from datetime import date
from pathlib import Path
import subprocess
import sys

import pytest

from vibe.research_pipeline.registration import (
    RegistrationError,
    load_registration,
    validate_registration_execution,
)
from vibe.research_pipeline.reliability import ReliabilityPolicy


ROOT = Path(__file__).resolve().parents[3]
REGISTRATION = ROOT / "config/research/orb_duration_symmetric_exploratory_v1.json"


def _validate(**overrides: object) -> None:
    arguments = {
        "repository_root": ROOT,
        "symbol": "QQQ",
        "ruleset_path": ROOT / "vibe/rulesets/orb_production.yaml",
        "axes": [
            (
                "orb_minutes",
                "strategy.orb_duration_minutes",
                (5, 15, 30),
            )
        ],
        "dev_start": date(2021, 1, 4),
        "dev_end": date(2024, 12, 31),
        "oos_start": date(2025, 1, 1),
        "oos_end": date(2026, 4, 27),
        "train_sessions": 252,
        "validation_sessions": 63,
        "test_sessions": 63,
        "step_sessions": 63,
        "warmup_sessions": 50,
        "record_runs": True,
        "record_test_only": False,
        "reliability_policy": ReliabilityPolicy(),
        "dataset_manifest_path": None,
        "data_dir_path": None,
        "research_db_path": None,
    }
    arguments.update(overrides)
    validate_registration_execution(load_registration(REGISTRATION), **arguments)


def test_registered_execution_remains_blocked_until_dataset_binding() -> None:
    with pytest.raises(RegistrationError, match="execution gate is not ready"):
        _validate()


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"axes": [("orb_minutes", "strategy.orb_duration_minutes", (5, 15))]},
         "Candidate axes differ"),
        ({"record_test_only": True}, "TEST-only recording differs"),
        ({"train_sessions": 251}, "train_sessions differs"),
    ],
)
def test_registration_rejects_execution_drift(
    override: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(RegistrationError, match=message):
        _validate(**override)


def test_registration_hashes_match_current_repository() -> None:
    registration = load_registration(REGISTRATION)
    assert registration["strategy"]["direction_mode"] == "symmetric"
    assert registration["reliability_policy"]["policy_hash"] == ReliabilityPolicy().policy_hash


def test_recorded_cli_requires_registration() -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/run_nested_walk_forward.py"),
            "--dev-start", "2021-01-04",
            "--dev-end", "2024-12-31",
            "--oos-start", "2025-01-01",
            "--oos-end", "2026-04-27",
            "--axis", "orb_minutes=strategy.orb_duration_minutes:5,15,30",
            "--record-runs",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "--registration is required" in result.stderr


def test_ready_gate_requires_bound_dataset_and_calendar() -> None:
    registration = load_registration(REGISTRATION)
    registration["execution_gate"] = {"state": "ready", "reasons": []}
    with pytest.raises(RegistrationError, match="exact calendar boundaries"):
        validate_registration_execution(
            registration,
            repository_root=ROOT,
            symbol="QQQ",
            ruleset_path=ROOT / "vibe/rulesets/orb_production.yaml",
            axes=[("orb_minutes", "strategy.orb_duration_minutes", (5, 15, 30))],
            dev_start=date(2021, 1, 4),
            dev_end=date(2024, 12, 31),
            oos_start=date(2025, 1, 1),
            oos_end=date(2026, 4, 27),
            train_sessions=252,
            validation_sessions=63,
            test_sessions=63,
            step_sessions=63,
            warmup_sessions=50,
            record_runs=True,
            record_test_only=False,
            reliability_policy=ReliabilityPolicy(),
            dataset_manifest_path=None,
            data_dir_path=None,
            research_db_path=None,
        )


def test_ready_gate_requires_bound_dataset_identity() -> None:
    registration = load_registration(REGISTRATION)
    registration["execution_gate"] = {"state": "ready", "reasons": []}
    registration["split_policy"]["exact_calendar_boundaries"] = [
        {
            "fold": 1,
            "train": ["2021-01-04", "2021-12-31"],
            "validation": ["2022-01-03", "2022-03-31"],
            "test": ["2022-04-01", "2022-06-30"],
        }
    ]
    registration["split_policy"]["fold_count"] = 1
    with pytest.raises(RegistrationError, match="data_binding"):
        validate_registration_execution(
            registration,
            repository_root=ROOT,
            symbol="QQQ",
            ruleset_path=ROOT / "vibe/rulesets/orb_production.yaml",
            axes=[("orb_minutes", "strategy.orb_duration_minutes", (5, 15, 30))],
            dev_start=date(2021, 1, 4),
            dev_end=date(2024, 12, 31),
            oos_start=date(2025, 1, 1),
            oos_end=date(2026, 4, 27),
            train_sessions=252,
            validation_sessions=63,
            test_sessions=63,
            step_sessions=63,
            warmup_sessions=50,
            record_runs=True,
            record_test_only=False,
            reliability_policy=ReliabilityPolicy(),
            dataset_manifest_path=None,
            data_dir_path=None,
            research_db_path=None,
        )
