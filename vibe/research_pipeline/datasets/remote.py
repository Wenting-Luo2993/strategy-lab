"""Trusted Supabase publisher and downloader for private research datasets."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Mapping, Protocol

from vibe.research_pipeline.datasets.manifest import verify_parquet
from vibe.research_pipeline.datasets.models import (
    DatasetManifest,
    DatasetStatus,
)


@dataclass(frozen=True)
class DatasetSupabaseSettings:
    url: str
    service_role_key: str = field(repr=False)
    bucket: str = "research-datasets"
    timeout_seconds: float = 30.0

    @classmethod
    def from_env(
        cls, environ: Mapping[str, str] | None = None
    ) -> "DatasetSupabaseSettings":
        env = os.environ if environ is None else environ
        url = env.get("RESEARCH_DATASET_SUPABASE_URL", "").strip()
        key = env.get("RESEARCH_DATASET_SUPABASE_SERVICE_ROLE_KEY", "").strip()
        if not url or not key:
            raise ValueError(
                "RESEARCH_DATASET_SUPABASE_URL and "
                "RESEARCH_DATASET_SUPABASE_SERVICE_ROLE_KEY are required"
            )
        if any(
            name.startswith("NEXT_PUBLIC_") and value == key
            for name, value in env.items()
        ):
            raise ValueError(
                "Dataset service-role credentials must never use NEXT_PUBLIC_*"
            )
        for name in (
            "SUPABASE_SERVICE_ROLE_KEY",
            "DASHBOARD_SUPABASE_SERVICE_ROLE_KEY",
            "TRADING_SUPABASE_SERVICE_ROLE_KEY",
            "RESEARCH_SUPABASE_SERVICE_ROLE_KEY",
        ):
            if env.get(name) == key:
                raise ValueError(
                    "Dataset credentials must be isolated from "
                    f"{name}"
                )
        return cls(
            url=url,
            service_role_key=key,
            bucket=env.get(
                "RESEARCH_DATASET_SUPABASE_BUCKET", "research-datasets"
            ),
            timeout_seconds=float(
                env.get("RESEARCH_DATASET_SUPABASE_TIMEOUT_SECONDS", "30")
            ),
        )


@dataclass(frozen=True)
class DatasetHttpResponse:
    status: int
    body: bytes


class DatasetHttpTransport(Protocol):
    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout_seconds: float,
    ) -> DatasetHttpResponse: ...

    def stream(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        timeout_seconds: float,
        chunk_size: int,
    ) -> Iterable[bytes]: ...


class UrllibDatasetTransport:
    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout_seconds: float,
    ) -> DatasetHttpResponse:
        request = urllib.request.Request(
            url, data=body, headers=dict(headers), method=method
        )
        try:
            with urllib.request.urlopen(
                request, timeout=timeout_seconds
            ) as response:
                return DatasetHttpResponse(response.status, response.read())
        except urllib.error.HTTPError as exc:
            return DatasetHttpResponse(exc.code, exc.read())

    def stream(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        timeout_seconds: float,
        chunk_size: int,
    ) -> Iterable[bytes]:
        request = urllib.request.Request(url, headers=dict(headers), method="GET")
        try:
            with urllib.request.urlopen(
                request, timeout=timeout_seconds
            ) as response:
                while chunk := response.read(chunk_size):
                    yield chunk
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(
                f"Supabase dataset download failed: status={exc.code} "
                f"body={detail}"
            ) from None


class SupabaseDatasetClient:
    """Service-role adapter intended only for owner-controlled Python devices."""

    def __init__(
        self,
        settings: DatasetSupabaseSettings,
        *,
        transport: DatasetHttpTransport | None = None,
    ) -> None:
        self.settings = settings
        self.transport = transport or UrllibDatasetTransport()

    def _headers(self, *, content_type: str = "application/json") -> dict[str, str]:
        return {
            "apikey": self.settings.service_role_key,
            "Authorization": f"Bearer {self.settings.service_role_key}",
            "Content-Type": content_type,
        }

    def _safe_error(self, operation: str, response: DatasetHttpResponse) -> None:
        if 200 <= response.status < 300:
            return
        body = response.body.decode("utf-8", errors="replace").replace(
            self.settings.service_role_key, "******"
        )
        raise RuntimeError(
            f"Supabase dataset {operation} failed: "
            f"status={response.status} body={body}"
        )

    def _request(self, operation: str, *args, **kwargs) -> DatasetHttpResponse:
        try:
            response = self.transport.request(*args, **kwargs)
        except Exception as exc:
            safe = str(exc).replace(
                self.settings.service_role_key, "******"
            )
            raise RuntimeError(
                f"Supabase dataset {operation} failed: {safe}"
            ) from None
        self._safe_error(operation, response)
        return response

    def fetch_manifest(self, dataset_id: str) -> DatasetManifest:
        query = urllib.parse.urlencode(
            {
                "select": "manifest,status",
                "dataset_id": f"eq.{dataset_id}",
                "limit": "1",
            }
        )
        endpoint = (
            f"{self.settings.url.rstrip('/')}/rest/v1/research_datasets?{query}"
        )
        response = self._request(
            "manifest fetch",
            "GET",
            endpoint,
            headers=self._headers(),
            body=None,
            timeout_seconds=self.settings.timeout_seconds,
        )
        rows = json.loads(response.body or b"[]")
        if not isinstance(rows, list) or len(rows) != 1:
            raise FileNotFoundError(f"Dataset {dataset_id} was not found")
        row = rows[0]
        manifest = DatasetManifest.model_validate(row["manifest"])
        if row["status"] != manifest.status.value:
            raise ValueError("Dataset metadata status does not match manifest")
        return manifest

    def iter_object(
        self, object_key: str, *, chunk_size: int = 1 << 20
    ) -> Iterable[bytes]:
        quoted = urllib.parse.quote(object_key, safe="/:")
        endpoint = (
            f"{self.settings.url.rstrip('/')}/storage/v1/object/"
            f"{self.settings.bucket}/{quoted}"
        )
        def chunks() -> Iterable[bytes]:
            try:
                yield from self.transport.stream(
                    endpoint,
                    headers=self._headers(
                        content_type="application/octet-stream"
                    ),
                    timeout_seconds=self.settings.timeout_seconds,
                    chunk_size=chunk_size,
                )
            except Exception as exc:
                safe = str(exc).replace(
                    self.settings.service_role_key, "******"
                )
                raise RuntimeError(
                    f"Supabase dataset download failed: {safe}"
                ) from None

        return chunks()

    def publish(self, manifest: DatasetManifest, parquet_path: str | Path) -> None:
        if manifest.status is not DatasetStatus.APPROVED:
            raise PermissionError("Only approved datasets may be published")
        path = Path(parquet_path)
        verify_parquet(path, manifest)
        object_endpoint = (
            f"{self.settings.url.rstrip('/')}/storage/v1/object/"
            f"{self.settings.bucket}/"
            f"{urllib.parse.quote(manifest.object_key, safe='/:')}"
        )
        self._request(
            "object publish",
            "POST",
            object_endpoint,
            headers={
                **self._headers(content_type="application/octet-stream"),
                "x-upsert": "false",
            },
            body=path.read_bytes(),
            timeout_seconds=self.settings.timeout_seconds,
        )

        dataset_row = {
            "dataset_id": manifest.dataset_id,
            "status": manifest.status.value,
            "manifest_sha256": manifest.manifest_sha256,
            "manifest": manifest.model_dump(mode="json"),
            "license_classification": manifest.license.classification,
        }
        self._upsert_dataset(dataset_row)
        self._insert_metadata(
            "research_dataset_objects",
            {
                "dataset_id": manifest.dataset_id,
                "symbol": manifest.symbol,
                "object_key": manifest.object_key,
                "object_sha256": manifest.object_sha256,
                "object_bytes": manifest.object_bytes,
                "format": "parquet",
                "object_version": 1,
            },
        )

    def transition_status(
        self,
        dataset_id: str,
        status: DatasetStatus,
    ) -> DatasetManifest:
        if status is DatasetStatus.APPROVED:
            raise ValueError(
                "Approval must use publish() so verified object upload "
                "precedes the approved metadata transition"
            )
        current = self.fetch_manifest(dataset_id)
        updated = current.transition(status)
        query = urllib.parse.urlencode(
            {
                "dataset_id": f"eq.{dataset_id}",
                "status": f"eq.{current.status.value}",
            }
        )
        endpoint = (
            f"{self.settings.url.rstrip('/')}/rest/v1/research_datasets?{query}"
        )
        response = self._request(
            "lifecycle transition",
            "PATCH",
            endpoint,
            headers={
                **self._headers(),
                "Prefer": "return=representation",
            },
            body=json.dumps(
                {
                    "status": updated.status.value,
                    "manifest_sha256": updated.manifest_sha256,
                    "manifest": updated.model_dump(mode="json"),
                },
                separators=(",", ":"),
            ).encode("utf-8"),
            timeout_seconds=self.settings.timeout_seconds,
        )
        rows = json.loads(response.body or b"[]")
        if not isinstance(rows, list) or len(rows) != 1:
            raise RuntimeError(
                "Dataset lifecycle transition lost a concurrent update"
            )
        return updated

    def _upsert_dataset(self, row: dict[str, object]) -> None:
        endpoint = (
            f"{self.settings.url.rstrip('/')}/rest/v1/research_datasets"
            "?on_conflict=dataset_id"
        )
        self._request(
            "research_datasets upsert",
            "POST",
            endpoint,
            headers={
                **self._headers(),
                "Prefer": "resolution=merge-duplicates,return=minimal",
            },
            body=json.dumps(row, separators=(",", ":")).encode("utf-8"),
            timeout_seconds=self.settings.timeout_seconds,
        )

    def _insert_metadata(self, table: str, row: dict[str, object]) -> None:
        endpoint = f"{self.settings.url.rstrip('/')}/rest/v1/{table}"
        self._request(
            f"{table} insert",
            "POST",
            endpoint,
            headers={
                **self._headers(),
                "Prefer": "return=minimal",
            },
            body=json.dumps(row, separators=(",", ":")).encode("utf-8"),
            timeout_seconds=self.settings.timeout_seconds,
        )
