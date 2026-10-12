"""Command line tools for dataset manifest generation, verification, and sync."""

from __future__ import annotations

import argparse
import subprocess
from datetime import datetime
from pathlib import Path

from vibe.research_pipeline.datasets.cache import DatasetSynchronizer
from vibe.research_pipeline.datasets.manifest import (
    converter_metadata,
    generate_manifest,
    read_manifest,
    verify_parquet,
    write_manifest,
)
from vibe.research_pipeline.datasets.models import (
    DatasetStatus,
    LicenseApproval,
)
from vibe.research_pipeline.datasets.remote import (
    DatasetSupabaseSettings,
    SupabaseDatasetClient,
)
from vibe.research_pipeline.hashing import hash_file


def _commit() -> str:
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], text=True
    ).strip()


def _generate(args: argparse.Namespace) -> None:
    source = Path(args.source)
    approval_time = (
        datetime.fromisoformat(args.approved_at) if args.approved_at else None
    )
    manifest = generate_manifest(
        args.parquet,
        status=DatasetStatus(args.status),
        vendor=args.vendor,
        vendor_job_id=args.vendor_job_id,
        source_dataset=args.source_dataset,
        source_schema=args.source_schema,
        symbol=args.symbol,
        bar_interval=args.bar_interval,
        session=args.session,
        source_sha256=hash_file(source),
        source_bytes=source.stat().st_size,
        converter=converter_metadata(
            path="scripts/convert_databento.py", code_commit=_commit()
        ),
        license=LicenseApproval(
            classification=args.license_classification,
            approved_by=args.approved_by,
            approved_at=approval_time,
            evidence_reference=args.evidence_reference,
        ),
    )
    write_manifest(args.output, manifest)
    print(manifest.dataset_id)


def _verify(args: argparse.Namespace) -> None:
    manifest = read_manifest(args.manifest)
    verify_parquet(args.parquet, manifest)
    print(f"verified {manifest.dataset_id}")


def _sync(args: argparse.Namespace) -> None:
    client = SupabaseDatasetClient(DatasetSupabaseSettings.from_env())
    target = DatasetSynchronizer(client).sync(
        args.dataset_id, data_dir=args.data_dir
    )
    print(target)


def _transition(args: argparse.Namespace) -> None:
    client = SupabaseDatasetClient(DatasetSupabaseSettings.from_env())
    manifest = client.transition_status(
        args.dataset_id, DatasetStatus(args.status)
    )
    print(f"{manifest.dataset_id} {manifest.status.value}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Approved dataset tooling")
    commands = parser.add_subparsers(dest="command", required=True)

    generate = commands.add_parser("generate")
    generate.add_argument("--parquet", required=True)
    generate.add_argument("--source", required=True)
    generate.add_argument("--output", required=True)
    generate.add_argument("--status", choices=[s.value for s in DatasetStatus],
                          default=DatasetStatus.CANDIDATE.value)
    generate.add_argument("--vendor", required=True)
    generate.add_argument("--vendor-job-id", required=True)
    generate.add_argument("--source-dataset", required=True)
    generate.add_argument("--source-schema", required=True)
    generate.add_argument("--symbol", required=True)
    generate.add_argument("--bar-interval", default="1m")
    generate.add_argument("--session", default="regular")
    generate.add_argument(
        "--license-classification", default="private-internal-use"
    )
    generate.add_argument("--approved-by", default="")
    generate.add_argument("--approved-at", default="")
    generate.add_argument("--evidence-reference", default="")
    generate.set_defaults(func=_generate)

    verify = commands.add_parser("verify")
    verify.add_argument("--manifest", required=True)
    verify.add_argument("--parquet", required=True)
    verify.set_defaults(func=_verify)

    sync = commands.add_parser("sync")
    sync.add_argument("dataset_id")
    sync.add_argument("--data-dir")
    sync.set_defaults(func=_sync)

    transition = commands.add_parser("transition")
    transition.add_argument("dataset_id")
    transition.add_argument(
        "status",
        choices=[
            DatasetStatus.RETIRED.value,
            DatasetStatus.REVOKED.value,
        ],
    )
    transition.set_defaults(func=_transition)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
