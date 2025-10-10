"""Generic Google Drive synchronization utilities.

Provides functions/classes to push and pull arbitrary files (e.g. market data
parquet caches, backtest result JSON/CSV) with accompanying metadata sidecar.

Design Goals:
  * Reusable across data cache and result persistence.
  * Minimal dependencies outside google-api-python-client + tenacity.
  * Pluggable hash function (default SHA256).
  * Non-intrusive: caller controls when to sync (e.g., at end of backtest).

Usage Overview:
  from src.utils.google_drive_sync import DriveSync
  sync = DriveSync(enable=True)  # or False to disable without branching
  sync.sync_down(local_path, remote_rel_path)
  # modify local file
  sync.sync_up(local_path, remote_rel_path)

Remote Folder Layout (configurable root):
  <root>/data_cache/... (parquet caches)
  <root>/results/backtests/... (result artifacts)
  <root>/results/live/... (live trading outputs)

Metadata file names: original + '.meta.json'
{
  "sha256": "...",
  "size": 12345,
  "updated_utc": "ISO8601",
  "file_id": "drive file id",
  "meta_id": "drive meta file id"
}

Credential Modes:
  * Service Account (env var GOOGLE_SERVICE_ACCOUNT_KEY path to JSON)
  * User OAuth (token file at ~/.strategy_lab/gdrive_token.json)

NOTE: OAuth interactive flow not implemented here; user supplies token file.
"""
from __future__ import annotations

import os
import json
import hashlib
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Tuple, List


from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload
from google.auth.transport.requests import Request
from google.oauth2 import service_account
from google.oauth2.credentials import Credentials
from tenacity import retry, stop_after_attempt, wait_exponential

from src.utils.logger import get_logger

# Initialize logger with console output enabled
logger = get_logger("DriveSync", log_to_console=True)

SCOPES = ["https://www.googleapis.com/auth/drive.file", "https://www.googleapis.com/auth/drive.metadata.readonly"]
DEFAULT_ROOT = "strategy-lab"

@dataclass
class MetaInfo:
    sha256: str
    size: int
    updated_utc: str
    file_id: Optional[str] = None
    meta_id: Optional[str] = None

    @classmethod
    def from_dict(cls, d: dict) -> "MetaInfo":
        return cls(
            sha256=d.get("sha256", ""),
            size=int(d.get("size", 0)),
            updated_utc=d.get("updated_utc", ""),
            file_id=d.get("file_id"),
            meta_id=d.get("meta_id"),
        )

    def to_dict(self) -> dict:
        return {
            "sha256": self.sha256,
            "size": self.size,
            "updated_utc": self.updated_utc,
            "file_id": self.file_id,
            "meta_id": self.meta_id,
        }

class DriveSync:
    def __init__(
        self,
        enable: bool = False,
        root_folder: str = DEFAULT_ROOT,
        service_account_env: str = "GOOGLE_SERVICE_ACCOUNT_KEY",
        use_service_account: bool = True,
        oauth_token_path: Optional[Path] = None,
        root_folder_id: Optional[str] = None,
    ):
        self.enable = enable
        self.root_folder = root_folder
        self.service_account_env = service_account_env
        self.use_service_account = use_service_account
        self.oauth_token_path = oauth_token_path or Path.home() / ".strategy_lab" / "gdrive_token.json"
        self.root_folder_id = root_folder_id
        # sanitize root_folder_id in case .env contains inline comments
        if isinstance(self.root_folder_id, str):
            self.root_folder_id = self.root_folder_id.split('#', 1)[0].strip() or None
        self._service = self._init_service() if enable else None
        self._folder_cache: dict[Tuple[str, ...], str] = {}
        if root_folder_id:
            # cache the root folder id under the tuple (root_folder,)
            self._folder_cache[(self.root_folder,)] = root_folder_id
        logger.debug(f"DriveSync initialized. root_folder={self.root_folder}, root_folder_id={self.root_folder_id}")

    # ---------------------- Auth ---------------------- #
    def _init_service(self):
        creds = None
        if self.use_service_account:
            key_path = self.service_account_env
            logger.info(f"Using service account env var: {self.service_account_env}")
            # if a direct path wasn't provided, treat this as an env var name
            if not Path(str(key_path)).is_absolute():
                key_path = os.getenv(self.service_account_env)
            # sanitize in case of inline comments in .env
            if isinstance(key_path, str):
                key_path = key_path.split('#', 1)[0].strip()
            logger.debug(f"Looking for service account key at: {key_path}")
            logger.debug(f"Sanitized service account key path: {key_path}")
            if key_path and Path(key_path).exists():
                logger.info(f"Using service account credentials from: {key_path}")
                creds = service_account.Credentials.from_service_account_file(key_path, scopes=SCOPES)
            else:
                logger.info("Service account key missing; falling back to OAuth token if present.")
        if creds is None and self.oauth_token_path.exists():
            logger.info(f"Attempting to use OAuth token from: {self.oauth_token_path}")
            creds = Credentials.from_authorized_user_file(str(self.oauth_token_path), SCOPES)
            if creds and creds.expired and creds.refresh_token:
                logger.info("OAuth token expired, attempting to refresh")
                creds.refresh(Request())
                logger.info("OAuth token successfully refreshed")
        if creds is None:
            logger.error("No valid credentials for DriveSync; operations will be skipped.")
            return None
        try:
            logger.info("Initializing Google Drive API service")
            service = build("drive", "v3", credentials=creds, cache_discovery=False)
            logger.info("Google Drive API service successfully initialized")
            return service
        except Exception as e:  # pragma: no cover
            logger.error(f"Failed to initialize Drive API: {e}")
            return None

    # ---------------------- Public API ---------------------- #
    def sync_down(self, local_path: Path, remote_rel_path: str):
        """Ensure local file matches remote version if remote exists and differs."""
        if not self.enable or self._service is None:
            logger.debug(f"Sync down skipped - service disabled or not initialized")
            return
        logger.info(f"Starting sync down for {remote_rel_path}")
        folder_id, data_name, meta_name = self._resolve_remote_names(remote_rel_path)
        if not folder_id:
            logger.warning(f"Could not resolve remote folder for {remote_rel_path}")
            return
        logger.debug(f"Looking for {data_name} in folder {folder_id}")
        data_file, meta_file = self._find_remote_pair(folder_id, data_name, meta_name)
        if not data_file:
            logger.info(f"No remote file found for {remote_rel_path}")
            return  # nothing remote yet
        remote_meta = self._fetch_remote_meta(meta_file)
        need = False
        if not local_path.exists():
            logger.info(f"Local file {local_path} does not exist, will download")
            need = True
        elif remote_meta:
            local_sha = self._compute_sha256(local_path)
            logger.debug(f"Local SHA: {local_sha}")
            logger.debug(f"Remote SHA: {remote_meta.sha256}")
            if local_sha != remote_meta.sha256:
                logger.info(f"Local and remote files differ, will download {data_name}")
                need = True
            else:
                logger.info(f"Local and remote files match for {data_name}")
        if need:
            self._download_file(data_file["id"], local_path)
            # Re-write local meta
            sha = self._compute_sha256(local_path)
            size = local_path.stat().st_size
            meta_obj = MetaInfo(
                sha256=sha,
                size=size,
                updated_utc=datetime.now(timezone.utc).isoformat(),
                file_id=data_file["id"],
                meta_id=meta_file["id"] if meta_file else None,
            )
            self._write_local_meta(local_path, meta_obj)
            logger.info(f"Updated local metadata for {data_name}")

    def sync_up(self, local_path: Path, remote_rel_path: str):
        """Upload or update remote file if changed locally."""
        if not self.enable or self._service is None:
            logger.debug(f"Sync up skipped - service disabled or not initialized")
            return
        if not local_path.exists():
            logger.warning(f"Local file {local_path} does not exist, skipping upload")
            return
        logger.info(f"Starting sync up for {local_path} to {remote_rel_path}")
        folder_id, data_name, meta_name = self._resolve_remote_names(remote_rel_path)
        if not folder_id:
            logger.warning(f"Could not resolve remote folder for {remote_rel_path}")
            return
        data_file, meta_file = self._find_remote_pair(folder_id, data_name, meta_name)
        local_sha = self._compute_sha256(local_path)
        local_size = local_path.stat().st_size
        remote_sha = None
        if meta_file:
            remote_meta = self._fetch_remote_meta(meta_file)
            remote_sha = remote_meta.sha256 if remote_meta else None
        if remote_sha == local_sha:
            return  # no change
        media = MediaFileUpload(str(local_path), mimetype="application/octet-stream", resumable=True)
        if data_file:
            self._service.files().update(
                fileId=data_file["id"],
                media_body=media,
                supportsAllDrives=True
            ).execute()
            file_id = data_file["id"]
            logger.info(f"Updated remote file {data_name}")
        else:
            metadata = {"name": data_name, "parents": [folder_id]}
            created = self._service.files().create(
                body=metadata,
                media_body=media,
                fields="id",
                supportsAllDrives=True
            ).execute()
            file_id = created["id"]
            logger.info(f"Uploaded new remote file {data_name}")
        # Upload meta JSON
        meta_payload = MetaInfo(
            sha256=local_sha,
            size=local_size,
            updated_utc=datetime.now(timezone.utc).isoformat(),
            file_id=file_id,
            meta_id=meta_file["id"] if meta_file else None,
        )
        self._upload_meta(folder_id, meta_name, meta_file, meta_payload)
        self._write_local_meta(local_path, meta_payload)

    # ---------------------- Internal Helpers ---------------------- #
    def _compute_sha256(self, path: Path) -> str:
        h = hashlib.sha256()
        with path.open("rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                h.update(chunk)
        return h.hexdigest()

    def _local_meta_path(self, path: Path) -> Path:
        return path.with_suffix(path.suffix + ".meta.json")

    def _write_local_meta(self, path: Path, meta: MetaInfo):
        meta_file = self._local_meta_path(path)
        meta_file.write_text(json.dumps(meta.to_dict(), indent=2))

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=10), reraise=True)
    def _ensure_folder(self, parts: Tuple[str, ...]) -> Optional[str]:
        if self._service is None:
            logger.debug("Service not initialized, can't ensure folder")
            return None
        if parts in self._folder_cache:
            logger.debug(f"Found folder in cache: {'/'.join(parts)}")
            return self._folder_cache[parts]
        logger.debug(f"Creating/finding folder path: {'/'.join(parts)}")
        parent_id = None
        path_accum: List[str] = []
        for name in parts:
            path_accum.append(name)
            key = tuple(path_accum)
            if key in self._folder_cache:
                parent_id = self._folder_cache[key]
                continue
            q = f"name = '{name}' and mimeType = 'application/vnd.google-apps.folder'"
            if parent_id:
                q += f" and '{parent_id}' in parents"
            # Regular Drive folder search/create (no shared drive parameters)
            res = self._service.files().list(q=q, fields="files(id,name)").execute()
            files = res.get("files", [])
            if files:
                fid = files[0]["id"]
            else:
                body = {"name": name, "mimeType": "application/vnd.google-apps.folder"}
                if parent_id:
                    body["parents"] = [parent_id]
                created = self._service.files().create(body=body, fields="id").execute()
                fid = created["id"]
            self._folder_cache[key] = fid
            parent_id = fid
        return parent_id

    def _resolve_remote_names(self, remote_rel_path: str) -> Tuple[Optional[str], str, str]:
        # remote_rel_path like "data_cache/AAPL_5m.parquet" or "results/backtests/run1.json"
        parts = tuple([self.root_folder] + remote_rel_path.strip("/").split("/")[:-1])
        data_name = remote_rel_path.split("/")[-1]
        meta_name = data_name + ".meta.json"
        logger.debug(f"Resolving remote path: {'/'.join(parts)}/{data_name}")
        folder_id = self._ensure_folder(parts)
        if folder_id:
            logger.debug(f"Resolved folder ID: {folder_id}")
        return folder_id, data_name, meta_name

    def _find_remote_pair(self, folder_id: str, data_name: str, meta_name: str) -> Tuple[Optional[dict], Optional[dict]]:
        q = f"'{folder_id}' in parents"
        res = self._service.files().list(q=q, fields="files(id,name,modifiedTime,size,mimeType)").execute()
        files = res.get("files", [])
        data = next((f for f in files if f["name"] == data_name), None)
        meta = next((f for f in files if f["name"] == meta_name), None)
        return data, meta

    def _fetch_remote_meta(self, meta_file: Optional[dict]) -> Optional[MetaInfo]:
        if not meta_file:
            return None
        try:
            raw = self._download_to_memory(meta_file["id"])
            return MetaInfo.from_dict(json.loads(raw.decode()))
        except Exception as e:  # pragma: no cover
            logger.warning(f"Failed parsing remote meta {meta_file.get('name')}: {e}")
            return None

    def _download_to_memory(self, file_id: str) -> bytes:
        request = self._service.files().get_media(fileId=file_id)
        import io
        fh = io.BytesIO()
        downloader = MediaIoBaseDownload(fh, request)
        done = False
        while not done:
            status, done = downloader.next_chunk()
        return fh.getvalue()

    def _download_file(self, file_id: str, dest: Path):
        data = self._download_to_memory(file_id)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        logger.info(f"Downloaded remote file {dest.name}")

    def _upload_meta(self, folder_id: str, meta_name: str, meta_file: Optional[dict], meta_payload: MetaInfo):
        import io
        tmp = io.BytesIO(json.dumps(meta_payload.to_dict(), indent=2).encode())
        # Simpler to write temp file for MediaFileUpload
        temp_path = Path.cwd() / f".{meta_name}.tmp"
        temp_path.write_bytes(tmp.getvalue())
        media = MediaFileUpload(str(temp_path), mimetype="application/json", resumable=False)
        if meta_file:
            self._service.files().update(fileId=meta_file["id"], media_body=media).execute()
        else:
            body = {"name": meta_name, "parents": [folder_id]}
            created = self._service.files().create(body=body, media_body=media, fields="id").execute()
            meta_payload.meta_id = created["id"]
        try:
            temp_path.unlink()
        except OSError:
            pass

__all__ = ["DriveSync", "MetaInfo"]
