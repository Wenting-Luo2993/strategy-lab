from __future__ import annotations

from argparse import Namespace
from pathlib import Path

import pytest

import scripts.manage_research_study as command


def test_final_holdout_claims_before_frozen_definition_or_file_reads(monkeypatch):
    calls: list[str] = []

    class Store:
        def claim_final_holdout(self, experiment_id, token):
            calls.append("claim")
            return object()

        def get_frozen_study(self, experiment_id):
            calls.append("get_frozen")
            raise RuntimeError("stop after claim")

        def interrupt_final_holdout(self, claim, reason):
            calls.append("interrupt")

        def close(self):
            calls.append("close")

    monkeypatch.setattr(command, "_store", lambda path: Store())
    args = Namespace(
        db=None,
        experiment_id="experiment-1",
        authorization_token="0123456789abcdef",
        ruleset=Path("must-not-be-read.yaml"),
        data_dir=Path("must-not-be-resolved"),
        capital=100_000.0,
    )

    with pytest.raises(RuntimeError, match="stop after claim"):
        command.final_holdout(args)

    assert calls == ["claim", "get_frozen", "interrupt", "close"]
