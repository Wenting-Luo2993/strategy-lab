"""Safety and durability tests for opt-in research artifact publication."""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Sequence

import pytest

from vibe.research_pipeline.artifacts import (
    ArtifactDestination,
    ArtifactPolicy,
    ArtifactPublicationRequest,
    ArtifactPublisher,
    ArtifactReconciler,
    ArtifactStager,
    PreviewPolicy,
    RemoteArtifactState,
    SqliteArtifactOutbox,
    StagedArtifact,
    resolve_repository_artifact,
)
from vibe.research_pipeline.publishing import BackoffPolicy
from vibe.research_pipeline.store import StoreError


def _request(path: str, *, preview: PreviewPolicy | None = None) -> ArtifactPublicationRequest:
    return ArtifactPublicationRequest(
        owning_publication_key="run:fixture",
        repository_path=path,
        source_registry_checksum="a" * 64,
        preview_policy=preview,
    )


class MemoryArtifactDestination(ArtifactDestination):
    def __init__(self, *, fail_manifest_once: bool = False) -> None:
        self.fail_manifest_once = fail_manifest_once
        self.objects: dict[str, str] = {}
        self.manifests: dict[str, RemoteArtifactState] = {}
        self.deleted: list[str] = []
        self.store_calls = 0

    async def store_object(self, artifact: StagedArtifact) -> str:
        self.store_calls += 1
        manifest = artifact.manifest
        existing = self.objects.get(manifest.object_key)
        if existing is not None and existing != manifest.sha256:
            raise RuntimeError("immutable object divergence")
        self.objects[manifest.object_key] = manifest.sha256
        return manifest.sha256

    async def publish_manifest(self, manifest) -> RemoteArtifactState:
        if self.fail_manifest_once:
            self.fail_manifest_once = False
            raise ConnectionError("manifest insert interrupted")
        state = RemoteArtifactState(
            artifact_id=manifest.artifact_id,
            sha256=manifest.sha256,
            object_key=manifest.object_key,
            manifest_present=True,
            object_present=self.objects.get(manifest.object_key) == manifest.sha256,
        )
        self.manifests[manifest.artifact_id] = state
        return state

    async def list_state(self) -> Sequence[RemoteArtifactState]:
        rows = list(self.manifests.values())
        referenced = {row.object_key for row in rows}
        rows.extend(
            RemoteArtifactState(
                artifact_id=f"staged:{index}",
                sha256=sha256,
                object_key=key,
                manifest_present=False,
                object_present=True,
            )
            for index, (key, sha256) in enumerate(self.objects.items())
            if key not in referenced
        )
        return tuple(rows)

    async def delete_unreferenced_staged_object(
        self, object_key: str, sha256: str, *, older_than: datetime
    ) -> bool:
        del sha256, older_than
        self.deleted.append(object_key)
        self.objects.pop(object_key, None)
        return True


def test_stager_builds_repository_free_content_addressed_manifest(tmp_path: Path):
    repository = tmp_path / "repo"
    artifact = repository / "reports" / "result summary.md"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("# Result\n\nAggregate metrics only.", encoding="utf-8")

    staged = ArtifactStager(tmp_path / "outbox").stage(
        _request("reports/result summary.md"), repository
    )

    assert staged.manifest.artifact_id.startswith("artifact:")
    assert staged.manifest.object_key == (
        f"v1/{staged.manifest.sha256}/result-summary.md"
    )
    assert staged.manifest.preview_policy is PreviewPolicy.INLINE
    assert staged.staging_path.read_bytes() == artifact.read_bytes()
    assert "repo" not in str(staged.manifest)
    assert "reports/" not in str(staged.manifest)


def test_repository_containment_rejects_external_and_symlink_escape(tmp_path: Path):
    repository = tmp_path / "repo"
    repository.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("aggregate", encoding="utf-8")

    with pytest.raises(StoreError, match="repository-relative"):
        resolve_repository_artifact(repository, str(outside))

    link = repository / "escape.txt"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlink creation is unavailable on this Windows host")
    with pytest.raises(StoreError, match="escapes the repository"):
        resolve_repository_artifact(repository, "escape.txt")


@pytest.mark.parametrize(
    ("name", "content", "message"),
    [
        ("bars.parquet", b"PAR1", "forbidden"),
        ("api-key.txt", b"not actually a key", "filename"),
        ("summary.txt", b"authorization=Bearer abc123", "credential-shaped"),
        ("events.json", b'{"fills":[{"price":10}]}', "event-level"),
        (
            "series.csv",
            b"timestamp,open,high,low,close,volume\n2026-01-01,1,1,1,1,1\n",
            "event-level",
        ),
    ],
)
def test_policy_rejects_forbidden_data(
    tmp_path: Path, name: str, content: bytes, message: str
):
    repository = tmp_path / "repo"
    repository.mkdir()
    (repository / name).write_bytes(content)

    with pytest.raises(StoreError, match=message):
        ArtifactPolicy().inspect(_request(name), repository)


def test_pdf_cannot_be_inline(tmp_path: Path):
    repository = tmp_path / "repo"
    repository.mkdir()
    (repository / "report.pdf").write_bytes(b"%PDF-1.7\naggregate only")

    with pytest.raises(StoreError, match="cannot use inline"):
        ArtifactPolicy().inspect(
            _request("report.pdf", preview=PreviewPolicy.INLINE), repository
        )


def test_json_event_scan_does_not_stop_at_a_prefix(tmp_path: Path):
    repository = tmp_path / "repo"
    repository.mkdir()
    values = [{"metric": index} for index in range(1001)]
    values.append({"fills": [{"price": 10}]})
    (repository / "summary.json").write_text(
        json.dumps(values), encoding="utf-8"
    )

    with pytest.raises(StoreError, match=r"\$\[1001\]\.fills"):
        ArtifactPolicy().inspect(_request("summary.json"), repository)


@pytest.mark.asyncio
async def test_interrupted_manifest_insert_recovers_from_durable_outbox(tmp_path: Path):
    repository = tmp_path / "repo"
    repository.mkdir()
    (repository / "summary.json").write_text(
        '{"expectancy":0.25,"trade_count":42}', encoding="utf-8"
    )
    staging = tmp_path / "staging"
    staged = ArtifactStager(staging).stage(_request("summary.json"), repository)
    db_path = tmp_path / "research.sqlite3"
    outbox = SqliteArtifactOutbox(db_path, staging)
    outbox.enqueue(staged)

    now = datetime(2026, 10, 12, tzinfo=timezone.utc)
    current = now
    destination = MemoryArtifactDestination(fail_manifest_once=True)
    publisher = ArtifactPublisher(
        outbox,
        destination,
        backoff=BackoffPolicy(
            base_seconds=1, maximum_seconds=1, jitter_fraction=0
        ),
        clock=lambda: current,
    )

    assert await publisher.flush_once() is False
    assert destination.objects == {staged.manifest.object_key: staged.manifest.sha256}
    outbox.close()

    current = now + timedelta(seconds=1)
    restarted = SqliteArtifactOutbox(db_path, staging)
    publisher = ArtifactPublisher(
        restarted,
        destination,
        backoff=BackoffPolicy(
            base_seconds=1, maximum_seconds=1, jitter_fraction=0
        ),
        clock=lambda: current,
    )
    assert await publisher.flush_once() is True
    assert destination.store_calls == 1
    state = restarted.local_state()[0]
    assert state.manifest_present is True
    assert state.object_present is True
    restarted.close()


@pytest.mark.asyncio
async def test_reconciliation_repairs_missing_manifest_but_not_divergence(
    tmp_path: Path,
):
    repository = tmp_path / "repo"
    repository.mkdir()
    (repository / "summary.txt").write_text("aggregate only", encoding="utf-8")
    staging = tmp_path / "staging"
    staged = ArtifactStager(staging).stage(_request("summary.txt"), repository)
    outbox = SqliteArtifactOutbox(tmp_path / "research.sqlite3", staging)
    outbox.enqueue(staged)
    destination = MemoryArtifactDestination()
    destination.objects[staged.manifest.object_key] = staged.manifest.sha256
    reconciler = ArtifactReconciler(outbox, destination)

    report = await reconciler.compare()
    assert report.missing_manifests == (staged.manifest.artifact_id,)
    assert report.missing_objects == ()
    assert reconciler.repair(report) == 1

    destination.manifests[staged.manifest.artifact_id] = RemoteArtifactState(
        artifact_id=staged.manifest.artifact_id,
        sha256="f" * 64,
        object_key=staged.manifest.object_key,
        manifest_present=True,
        object_present=True,
    )
    divergent = await reconciler.compare()
    assert divergent.divergent == (staged.manifest.artifact_id,)
    assert reconciler.repair(divergent) == 0
    outbox.close()


def test_outbox_detects_divergent_manifest_for_same_artifact_id(tmp_path: Path):
    repository = tmp_path / "repo"
    repository.mkdir()
    (repository / "summary.txt").write_text("aggregate only", encoding="utf-8")
    staging = tmp_path / "staging"
    staged = ArtifactStager(staging).stage(_request("summary.txt"), repository)
    outbox = SqliteArtifactOutbox(tmp_path / "research.sqlite3", staging)
    outbox.enqueue(staged)

    divergent = StagedArtifact(
        staged.manifest.__class__(
            **{
                **staged.manifest.__dict__,
                "media_type": "application/json",
            }
        ),
        staged.staging_path,
    )
    with pytest.raises(StoreError, match="divergent manifest"):
        outbox.enqueue(divergent)
    outbox.close()


def test_local_staging_gc_only_removes_old_unreferenced_files(tmp_path: Path):
    repository = tmp_path / "repo"
    repository.mkdir()
    (repository / "summary.txt").write_text("aggregate only", encoding="utf-8")
    staging = tmp_path / "staging"
    outbox = SqliteArtifactOutbox(tmp_path / "research.sqlite3", staging)
    staged = outbox.stage_and_enqueue(
        ArtifactStager(staging), _request("summary.txt"), repository
    )
    orphan = staging / "v1" / ("f" * 64) / "orphan.txt"
    orphan.parent.mkdir(parents=True)
    orphan.write_text("orphan", encoding="utf-8")
    old = datetime.now(timezone.utc) - timedelta(days=2)
    timestamp = old.timestamp()
    os.utime(orphan, (timestamp, timestamp))

    assert outbox.garbage_collect_local_staging(older_than=old + timedelta(days=1)) == 1
    assert not orphan.exists()
    assert staged.staging_path.exists()
    outbox.close()
