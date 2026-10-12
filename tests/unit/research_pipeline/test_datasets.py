from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pytest
from pydantic import ValidationError

from vibe.research_pipeline.datasets import (
    DatasetStatus,
    DatasetSynchronizer,
    DatasetSupabaseSettings,
    LicenseApproval,
    SupabaseDatasetClient,
    converter_metadata,
    generate_manifest,
    verify_materialized_dataset,
    write_manifest,
)
from vibe.research_pipeline.datasets.remote import DatasetHttpResponse
from vibe.research_pipeline.identity import RunFingerprint


def _bars() -> pd.DataFrame:
    index = pd.date_range(
        "2024-01-02 09:30", periods=3, freq="1min",
        tz="America/New_York", name="ts_event",
    )
    return pd.DataFrame(
        {
            "open": [100.0, 101.0, 102.0],
            "high": [101.0, 102.0, 103.0],
            "low": [99.0, 100.0, 101.0],
            "close": [100.5, 101.5, 102.5],
            "volume": [10, 20, 30],
        },
        index=index,
    )


def _license(*, approved: bool = True) -> LicenseApproval:
    return LicenseApproval(
        classification="private-internal-use",
        approved_by="owner" if approved else "",
        approved_at=datetime(2026, 10, 12, tzinfo=timezone.utc)
        if approved else None,
        evidence_reference="license-review-1" if approved else "",
    )


def _manifest(
    tmp_path: Path,
    *,
    status: DatasetStatus = DatasetStatus.APPROVED,
    compression: str = "snappy",
):
    parquet = tmp_path / f"QQQ-{compression}.parquet"
    _bars().to_parquet(parquet, compression=compression)
    return parquet, generate_manifest(
        parquet,
        status=status,
        vendor="databento",
        vendor_job_id="job-1",
        source_dataset="XNAS.ITCH",
        source_schema="ohlcv-1m",
        symbol="qqq",
        bar_interval="1m",
        session="regular",
        source_sha256="a" * 64,
        source_bytes=123,
        converter=converter_metadata(
            path="scripts/convert_databento.py",
            code_commit="1234567abcdef",
        ),
        license=_license(approved=status is DatasetStatus.APPROVED),
    )


def test_logical_identity_is_independent_of_parquet_encoding(tmp_path):
    _snappy_path, snappy = _manifest(tmp_path, compression="snappy")
    _gzip_path, gzip = _manifest(tmp_path, compression="gzip")

    assert snappy.dataset_id == gzip.dataset_id
    assert snappy.object_sha256 != gzip.object_sha256
    assert snappy.object_key != gzip.object_key


def test_manifest_rejects_tampered_dataset_id(tmp_path):
    _path, manifest = _manifest(tmp_path)
    payload = manifest.model_dump(mode="python")
    payload["dataset_id"] = "sha256:" + "f" * 64

    with pytest.raises(ValidationError, match="dataset_id mismatch"):
        type(manifest).model_validate(payload)


def test_manifest_file_uses_canonical_json(tmp_path):
    _path, manifest = _manifest(tmp_path)
    destination = tmp_path / "manifest.json"

    write_manifest(destination, manifest)

    assert destination.read_text(encoding="utf-8") == (
        manifest.canonical_json() + "\n"
    )


def test_approval_requires_clean_license_evidence(tmp_path):
    parquet = tmp_path / "QQQ.parquet"
    _bars().to_parquet(parquet)

    with pytest.raises(ValidationError, match="license approval evidence"):
        generate_manifest(
            parquet,
            status=DatasetStatus.APPROVED,
            vendor="databento",
            vendor_job_id="job-1",
            source_dataset="XNAS.ITCH",
            source_schema="ohlcv-1m",
            symbol="QQQ",
            bar_interval="1m",
            session="regular",
            source_sha256="a" * 64,
            source_bytes=123,
            converter=converter_metadata(
                path="scripts/convert_databento.py",
                code_commit="1234567abcdef",
            ),
            license=_license(approved=False),
        )


def test_lifecycle_allows_only_forward_safety_transitions(tmp_path):
    _path, approved = _manifest(tmp_path)
    retired = approved.transition(DatasetStatus.RETIRED)
    assert retired.status is DatasetStatus.RETIRED
    assert retired.dataset_id == approved.dataset_id
    assert retired.transition(DatasetStatus.REVOKED).status is DatasetStatus.REVOKED
    with pytest.raises(ValueError, match="Illegal dataset lifecycle"):
        retired.transition(DatasetStatus.APPROVED)


def test_candidate_can_attach_license_evidence_at_approval(tmp_path):
    _path, candidate = _manifest(tmp_path, status=DatasetStatus.CANDIDATE)

    approved = candidate.transition(
        DatasetStatus.APPROVED, license=_license()
    )

    assert approved.dataset_id == candidate.dataset_id
    assert approved.license.approved


class _Source:
    def __init__(self, manifest, path):
        self.manifest = manifest
        self.path = path
        self.downloads = 0

    def fetch_manifest(self, dataset_id):
        return self.manifest

    def iter_object(self, object_key, *, chunk_size=1 << 20):
        self.downloads += 1
        with self.path.open("rb") as handle:
            while chunk := handle.read(17):
                yield chunk


def test_sync_verifies_caches_and_atomically_materializes(tmp_path):
    parquet, manifest = _manifest(tmp_path)
    source = _Source(manifest, parquet)
    data_dir = tmp_path / "data"
    synchronizer = DatasetSynchronizer(
        source, cache_dir=tmp_path / "cache"
    )

    target = synchronizer.sync(manifest.dataset_id, data_dir=data_dir)
    synchronizer.sync(manifest.dataset_id, data_dir=data_dir)

    assert target == data_dir / "QQQ.parquet"
    assert source.downloads == 1
    assert not list(data_dir.glob("*.tmp"))
    verified = verify_materialized_dataset(
        data_dir, symbol="QQQ", dataset_id=manifest.dataset_id
    )
    assert verified.object_sha256 == manifest.object_sha256


def test_sync_refuses_non_approved_dataset(tmp_path):
    parquet, manifest = _manifest(tmp_path, status=DatasetStatus.CANDIDATE)
    source = _Source(manifest, parquet)

    with pytest.raises(PermissionError, match="only approved"):
        DatasetSynchronizer(source, cache_dir=tmp_path / "cache").sync(
            manifest.dataset_id, data_dir=tmp_path / "data"
        )


def test_sync_removes_partial_download_after_hash_failure(tmp_path):
    parquet, manifest = _manifest(tmp_path)
    corrupt = tmp_path / "corrupt.parquet"
    corrupt.write_bytes(b"not the approved object")
    synchronizer = DatasetSynchronizer(
        _Source(manifest, corrupt), cache_dir=tmp_path / "cache"
    )

    with pytest.raises(ValueError, match="hash does not match"):
        synchronizer.sync(manifest.dataset_id, data_dir=tmp_path / "data")

    assert not list((tmp_path / "cache" / "objects").iterdir())
    assert not (tmp_path / "data" / "QQQ.parquet").exists()


def test_run_fingerprint_checks_materialized_dataset_without_hash_change(tmp_path):
    parquet, manifest = _manifest(tmp_path)
    DatasetSynchronizer(
        _Source(manifest, parquet), cache_dir=tmp_path / "cache"
    ).sync(manifest.dataset_id, data_dir=tmp_path / "data")
    fingerprint = RunFingerprint(
        strategy_id="orb",
        ruleset_content_sha256="a" * 64,
        universe_hash="b" * 64,
        split_manifest_hash="c" * 64,
        data_snapshot_id=manifest.dataset_id,
        feature_set_version=1,
        execution_model_version=1,
        metric_calculation_version=1,
        code_commit="1234567abcdef",
        code_dirty=False,
    )
    before = fingerprint.fingerprint

    fingerprint.assert_materialized_dataset(tmp_path / "data", symbol="QQQ")

    assert fingerprint.dataset_id == manifest.dataset_id
    assert fingerprint.fingerprint == before
    with pytest.raises(ValueError, match="expects"):
        verify_materialized_dataset(
            tmp_path / "data",
            symbol="QQQ",
            dataset_id="sha256:" + "0" * 64,
        )


class _Transport:
    def __init__(self, responses, stream_payload=b"payload"):
        self.responses = list(responses)
        self.requests = []
        self.stream_payload = stream_payload

    def request(self, method, url, *, headers, body, timeout_seconds):
        self.requests.append((method, url, headers, body))
        return self.responses.pop(0)

    def stream(self, url, *, headers, timeout_seconds, chunk_size):
        yield self.stream_payload


def test_remote_settings_hide_and_isolate_service_role_key():
    secret = "dataset-secret"
    settings = DatasetSupabaseSettings.from_env(
        {
            "RESEARCH_DATASET_SUPABASE_URL": "https://example.supabase.co",
            "RESEARCH_DATASET_SUPABASE_SERVICE_ROLE_KEY": secret,
        }
    )
    assert secret not in repr(settings)
    with pytest.raises(ValueError, match="NEXT_PUBLIC"):
        DatasetSupabaseSettings.from_env(
            {
                "RESEARCH_DATASET_SUPABASE_URL": "https://example.supabase.co",
                "RESEARCH_DATASET_SUPABASE_SERVICE_ROLE_KEY": secret,
                "NEXT_PUBLIC_DATASET_KEY": secret,
            }
        )


def test_remote_manifest_fetch_and_lifecycle_transition(tmp_path):
    _path, manifest = _manifest(tmp_path)
    retired = manifest.transition(DatasetStatus.RETIRED)
    transport = _Transport(
        [
            DatasetHttpResponse(
                200,
                json.dumps(
                    [{
                        "manifest": manifest.model_dump(mode="json"),
                        "status": "approved",
                    }]
                ).encode(),
            ),
            DatasetHttpResponse(
                200,
                json.dumps([{"dataset_id": manifest.dataset_id}]).encode(),
            ),
        ]
    )
    client = SupabaseDatasetClient(
        DatasetSupabaseSettings(
            url="https://example.supabase.co",
            service_role_key="secret",
        ),
        transport=transport,
    )

    assert client.transition_status(
        manifest.dataset_id, DatasetStatus.RETIRED
    ) == retired
    method, url, _headers, body = transport.requests[-1]
    assert method == "PATCH"
    assert "status=eq.approved" in url
    assert json.loads(body)["status"] == "retired"


def test_trusted_publisher_verifies_object_and_never_upserts(tmp_path):
    parquet, manifest = _manifest(tmp_path)
    transport = _Transport(
        [
            DatasetHttpResponse(200, b"{}"),
            DatasetHttpResponse(201, b""),
            DatasetHttpResponse(201, b""),
        ]
    )
    client = SupabaseDatasetClient(
        DatasetSupabaseSettings(
            url="https://example.supabase.co",
            service_role_key="secret",
        ),
        transport=transport,
    )

    client.publish(manifest, parquet)

    object_request, dataset_request, metadata_request = transport.requests
    assert object_request[0] == "POST"
    assert object_request[2]["x-upsert"] == "false"
    assert object_request[2]["Authorization"] == "Bearer " + "sec" + "ret"
    assert "/research_datasets" in dataset_request[1]
    assert "on_conflict=dataset_id" in dataset_request[1]
    assert (
        dataset_request[2]["Prefer"]
        == "resolution=merge-duplicates,return=minimal"
    )
    assert "/research_dataset_objects" in metadata_request[1]


def test_trusted_publisher_recovers_after_object_upload_ack_is_lost(tmp_path):
    parquet, manifest = _manifest(tmp_path)
    transport = _Transport(
        [
            DatasetHttpResponse(409, b'{"message":"already exists"}'),
            DatasetHttpResponse(201, b""),
            DatasetHttpResponse(201, b""),
        ],
        stream_payload=parquet.read_bytes(),
    )
    client = SupabaseDatasetClient(
        DatasetSupabaseSettings(
            url="https://example.supabase.co",
            service_role_key="secret",
        ),
        transport=transport,
    )

    client.publish(manifest, parquet)

    assert len(transport.requests) == 3
