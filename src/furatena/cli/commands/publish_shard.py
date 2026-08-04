"""``publish-shard`` parser and thin orchestration boundary."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from furatena.catalog.exceptions import CatalogConfigError
from furatena.cli.commands._shared import (
    CommandModule,
    _app_root,
    _autodoc_config,
    _docs_yaml,
    _repo_for_app,
)
from furatena.cli.contracts import CommandResult, command_name


def _run_publish_shard(args: argparse.Namespace) -> CommandResult:
    from furatena.catalog.federation_publish import PublishShardOptions, publish_shard
    from furatena.catalog.federation_s3 import S3ObjectBackend
    from furatena.catalog.freeze import FreezeCatalogOptions, freeze_catalog

    app_root = _app_root(args)
    repo_root = _repo_for_app(app_root)
    frozen_dir = (
        Path(args.frozen_dir).expanduser().resolve() if args.frozen_dir else app_root / "frozen"
    )
    freeze_catalog(
        FreezeCatalogOptions(
            docs_config=_docs_yaml(args),
            app_root=app_root,
            repo_root=repo_root,
            output_dir=frozen_dir,
            full_rebuild=args.full,
            workers=args.workers,
            autodoc=True,
            autodoc_config=_autodoc_config(args, repo_root),
        )
    )
    verification = _read_verification(Path(args.verification).expanduser().resolve())
    access_key = os.environ.get("AWS_ACCESS_KEY_ID", "")
    secret_key = os.environ.get("AWS_SECRET_ACCESS_KEY", "")
    if not access_key or not secret_key:
        raise CatalogConfigError(
            "AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY must both be configured before publishing.",
            operation="publish_shard_config",
        )
    try:
        backend = S3ObjectBackend(
            endpoint=args.s3_endpoint,
            bucket=args.s3_bucket,
            prefix=args.s3_prefix,
            region=args.s3_region,
            access_key_id=access_key,
            secret_access_key=secret_key,
            session_token=os.environ.get("AWS_SESSION_TOKEN"),
        )
    except ValueError as exc:
        raise CatalogConfigError(str(exc), operation="publish_shard_config") from exc

    source_shard = frozen_dir / "mounts" / args.mount / args.edition
    with tempfile.TemporaryDirectory(prefix="fura-publish-shard-") as temporary:
        result = publish_shard(
            PublishShardOptions(
                source_shard=source_shard,
                staging_dir=Path(temporary),
                public_base_url=args.public_base_url,
                verification=verification,
                repository_url=args.repository_url,
                lifecycle_status=args.lifecycle_status,
                release_date=args.release_date,
                end_of_life=args.end_of_life,
                retention_days=args.retention_days,
                pinned_by=tuple(args.pinned_by),
            ),
            backend,
        )
    if args.hub_entry_output:
        target = Path(args.hub_entry_output).expanduser().resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(result.hub_entry, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    return CommandResult(
        command=command_name(args),
        ok=True,
        summary=f"shard {args.mount}:{args.edition} {result.status}",
        data={
            "status": result.status,
            "fingerprint": result.fingerprint,
            "uploaded_objects": result.uploaded_objects,
            "reused_objects": result.reused_objects,
            "hub_entry": result.hub_entry,
            **(
                {"hub_entry_output": str(Path(args.hub_entry_output).expanduser().resolve())}
                if args.hub_entry_output
                else {}
            ),
        },
        terminal_lines=(
            f"Shard {args.mount}:{args.edition} {result.status}: {result.fingerprint}",
            json.dumps(result.hub_entry, sort_keys=True, separators=(",", ":")),
        ),
    )


def _read_verification(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CatalogConfigError(
            f"cannot read verification metadata: {exc}",
            path=path,
            operation="publish_shard_config",
        ) from exc
    if not isinstance(payload, dict):
        raise CatalogConfigError(
            "The verification metadata file must contain one top-level JSON object.",
            path=path,
            operation="publish_shard_config",
        )
    return payload


def configure(sub: Any) -> None:
    publish = sub.add_parser("publish-shard", help="Freeze and publish one public federation shard")
    publish.add_argument("--mount", required=True, help="Public mount id")
    publish.add_argument("--edition", required=True, help="Frozen release edition id")
    publish.add_argument(
        "--public-base-url",
        required=True,
        help="Public HTTPS prefix immediately above the sha256 object-set directory",
    )
    publish.add_argument(
        "--repository-url",
        default=None,
        help="Optional assertion against the repository URL recorded by the freeze",
    )
    publish.add_argument(
        "--verification",
        required=True,
        help="JSON containing signature and attestation references for the shard fingerprint",
    )
    publish.add_argument("--s3-endpoint", required=True, help="S3-compatible HTTPS endpoint")
    publish.add_argument("--s3-bucket", required=True, help="S3 bucket")
    publish.add_argument("--s3-prefix", default="shards", help="Object key prefix")
    publish.add_argument(
        "--s3-region", default=os.environ.get("AWS_REGION", "us-east-1"), help="SigV4 region"
    )
    publish.add_argument("--frozen-dir", default=None, help="Freeze output directory")
    publish.add_argument("--full", action="store_true", help="Force a full freeze")
    publish.add_argument("--workers", type=int, default=None, help="Parallel freeze workers")
    publish.add_argument(
        "--lifecycle-status",
        choices=("legacy", "deprecated", "preview", "eol"),
        default="legacy",
        help="Immutable release lifecycle status",
    )
    publish.add_argument("--release-date", default=None, help="ISO release date")
    publish.add_argument("--end-of-life", default=None, help="ISO end-of-life date")
    publish.add_argument("--retention-days", type=int, default=365, help="Minimum retention")
    publish.add_argument(
        "--pinned-by", action="append", default=["hub:public-docs"], help="Retention pin"
    )
    publish.add_argument(
        "--hub-entry-output", default=None, help="Write exact hub shards[key] JSON"
    )
    publish.add_argument("--json", action="store_true", help="Emit standard command result JSON")
    publish.set_defaults(handler=_run_publish_shard)


COMMAND = CommandModule("publish-shard", configure)
