"""Schema v18: editable one-line experiment conclusions and their sync."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Mapping, Optional

import pytest

from vibe.research_pipeline.publishing import (
    ExperimentConclusion,
    ResearchHttpResponse,
    ResearchSupabaseSettings,
    SupabaseResearchDestination,
    sync_experiment_conclusions,
)
from vibe.research_pipeline.storage import schema
from vibe.research_pipeline.storage.sqlite_store import SqliteResearchStore
from vibe.research_pipeline.store import ImmutableRecordError, StoreError

from test_experiments import build_experiment_fixture


@pytest.fixture()
def finished(tmp_path: Path):
    path = tmp_path / "demo.sqlite3"
    experiment_id, _ = build_experiment_fixture(path)
    store = SqliteResearchStore(path)
    yield store, experiment_id
    store.close()


def test_fresh_database_reaches_v18_with_conclusion_table(tmp_path: Path) -> None:
    assert schema.SCHEMA_VERSION == 18
    with sqlite3.connect(tmp_path / "fresh.sqlite3") as conn:
        assert schema.migrate(conn) == 18
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        assert "research_experiment_conclusions" in tables


def test_v17_database_migrates_to_v18(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "v17.sqlite3"
    with sqlite3.connect(path) as conn:
        with monkeypatch.context() as patch:
            patch.setattr(schema, "_MIGRATIONS", schema._MIGRATIONS[:17])
            patch.setattr(schema, "SCHEMA_VERSION", 17)
            assert schema.migrate(conn) == 17
        assert schema.migrate(conn) == 18


def test_conclusion_is_set_and_edited_in_place(finished) -> None:
    store, experiment_id = finished
    first = store.set_experiment_conclusion(experiment_id, "  No edge found.  ")
    assert first.conclusion == "No edge found."
    assert first.pending_sync

    assert store.mark_experiment_conclusion_synced(
        experiment_id, first.payload_sha256
    )
    assert not store.get_experiment_conclusion(experiment_id).pending_sync

    unchanged = store.set_experiment_conclusion(experiment_id, "No edge found.")
    assert unchanged.updated_at == first.updated_at
    assert not unchanged.pending_sync

    edited = store.set_experiment_conclusion(experiment_id, "Weak edge, CI spans 0.")
    assert edited.conclusion == "Weak edge, CI spans 0."
    assert edited.pending_sync
    count = store._conn.execute(
        "SELECT COUNT(*) FROM research_experiment_conclusions"
    ).fetchone()[0]
    assert count == 1
    # The experiment row itself stays immutable and the outbox is untouched.
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        store._conn.execute("UPDATE research_experiments SET title='changed'")


@pytest.mark.parametrize(
    "text, message",
    [
        ("", "empty"),
        ("   ", "empty"),
        ("line one\nline two", "single line"),
        ("x" * 501, "500"),
        ("apikey=abc123", "secret"),
    ],
)
def test_invalid_conclusions_are_rejected(finished, text: str, message: str) -> None:
    store, experiment_id = finished
    with pytest.raises(ValueError, match=message):
        store.set_experiment_conclusion(experiment_id, text)
    assert store.get_experiment_conclusion(experiment_id) is None


def test_unknown_or_running_experiment_is_rejected(finished) -> None:
    store, _ = finished
    with pytest.raises(StoreError, match="Unknown experiment"):
        store.set_experiment_conclusion("missing", "Nothing")
    running_id = store.start_experiment("running study", {"symbol": "SYNTHETIC"})
    with pytest.raises(ImmutableRecordError, match="running"):
        store.set_experiment_conclusion(running_id, "Too early")


def test_edit_during_sync_stays_pending(finished) -> None:
    store, experiment_id = finished
    stale = store.set_experiment_conclusion(experiment_id, "First draft")
    store.set_experiment_conclusion(experiment_id, "Second draft")
    assert not store.mark_experiment_conclusion_synced(
        experiment_id, stale.payload_sha256
    )
    assert store.get_experiment_conclusion(experiment_id).pending_sync


class FakeTransport:
    def __init__(self, responses: list[ResearchHttpResponse]) -> None:
        self.responses = responses
        self.requests: list[dict[str, Any]] = []

    async def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        params: Optional[Mapping[str, str]] = None,
        json_body: Optional[Any] = None,
        timeout_seconds: float,
    ) -> ResearchHttpResponse:
        self.requests.append(
            {
                "method": method,
                "url": url,
                "headers": dict(headers),
                "params": dict(params or {}),
                "json": json_body,
            }
        )
        return self.responses.pop(0)


def _destination(transport: FakeTransport, secret: str = "secret-key"):
    return SupabaseResearchDestination(
        ResearchSupabaseSettings(
            url="https://example.supabase.co", service_role_key=secret
        ),
        transport=transport,
    )


@pytest.mark.asyncio
async def test_sync_upserts_pending_conclusions_once(finished) -> None:
    store, experiment_id = finished
    stored = store.set_experiment_conclusion(experiment_id, "No edge found.")
    transport = FakeTransport(
        [ResearchHttpResponse(201, [stored.remote_row()], {})]
    )
    result = await sync_experiment_conclusions(store, _destination(transport))

    assert (result.pending, result.synced, result.failed) == (1, 1, 0)
    request = transport.requests[0]
    assert request["method"] == "POST"
    assert request["url"].endswith("/rest/v1/research_experiment_conclusions")
    assert request["params"] == {"on_conflict": "experiment_id"}
    assert "resolution=merge-duplicates" in request["headers"]["Prefer"]
    assert request["json"] == {
        "experiment_id": experiment_id,
        "conclusion": "No edge found.",
        "updated_at": stored.updated_at,
    }
    assert store.pending_experiment_conclusions() == []

    again = await sync_experiment_conclusions(store, _destination(FakeTransport([])))
    assert (again.pending, again.synced) == (0, 0)


@pytest.mark.asyncio
async def test_sync_failure_stays_pending_and_redacts_secret(finished) -> None:
    store, experiment_id = finished
    store.set_experiment_conclusion(experiment_id, "No edge found.")
    secret = "service-role-secret-value"
    transport = FakeTransport(
        [ResearchHttpResponse(404, {"message": f"apikey={secret} missing"}, {})]
    )
    result = await sync_experiment_conclusions(
        store, _destination(transport, secret)
    )
    assert (result.pending, result.synced, result.failed) == (1, 0, 1)
    assert secret not in " ".join(result.errors)
    assert len(store.pending_experiment_conclusions()) == 1


def test_remote_row_excludes_local_sync_markers() -> None:
    conclusion = ExperimentConclusion(
        experiment_id="e",
        conclusion="c",
        updated_at="2026-11-02T00:00:00Z",
        payload_sha256="a" * 64,
        synced_sha256="b" * 64,
        synced_at="2026-11-02T00:00:01Z",
    )
    assert set(conclusion.remote_row()) == {"experiment_id", "conclusion", "updated_at"}
    assert conclusion.pending_sync
