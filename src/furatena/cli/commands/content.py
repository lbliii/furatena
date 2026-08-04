"""Manage Railway adopter content generations."""

from __future__ import annotations

import argparse
import errno
import json
import os
from pathlib import Path
from typing import Any

from furatena.catalog.content_deployment import (
    ContentDeploymentConfig,
    ContentDeploymentError,
    ContentDeploymentStore,
)
from furatena.catalog.content_refresh import (
    ContentRefreshRequest,
    ContentRefreshService,
    ContentRefreshState,
    cli_content_actor,
)
from furatena.catalog.exceptions import CatalogError
from furatena.cli.commands._shared import CommandModule


class _ContentCommandError(CatalogError):
    code = "fura.content_deployment"
    exit_code = 4


def _store() -> ContentDeploymentStore:
    config = ContentDeploymentConfig.from_environment()
    if config is None:
        raise ContentDeploymentError("FURA_CONTENT_REPOSITORY is not configured")
    return ContentDeploymentStore(config)


def _run_content(args: argparse.Namespace) -> None:
    store: ContentDeploymentStore | None = None
    try:
        store = _store()
        service = ContentRefreshService(store)
        actor = cli_content_actor()
        if args.content_command == "refresh":
            expected = (
                None
                if args.expected_active_commit.strip().lower() == "none"
                else args.expected_active_commit
            )
            receipt = service.submit(
                ContentRefreshRequest(
                    expected_active_commit=expected,
                    requested_commit=args.requested_commit,
                    idempotency_key=args.idempotency_key,
                ),
                actor=actor,
                asynchronous=False,
            )
            if receipt.state == ContentRefreshState.FAILED:
                failure = receipt.failure or {}
                raise ContentDeploymentError(
                    str(failure.get("message") or "Content refresh failed before promotion.")
                )
            result = receipt.to_dict()
        elif args.content_command == "rollback":
            result = service.rollback(actor=actor.actor, reason=args.reason)
        elif args.content_command == "reconcile":
            result = store.reconcile()
            reconciled = service.reconcile_startup()
            if reconciled:
                result = {
                    **result,
                    "reconciled_operations": [item.to_dict() for item in reconciled],
                }
            if store.startup_refresh_needed():
                try:
                    operation = service.submit_compatibility(
                        actor=actor,
                        asynchronous=False,
                    )
                    if operation.state == ContentRefreshState.FAILED:
                        if store.status()["active_generation"] is None:
                            failure = operation.failure or {}
                            raise ContentDeploymentError(
                                str(
                                    failure.get("message")
                                    or "Initial managed-content refresh failed."
                                )
                            )
                        result = {
                            **store.status(),
                            "status": "degraded",
                            "refresh_failed": True,
                            "refresh_operation": operation.to_dict(),
                        }
                    else:
                        result = operation.to_dict()
                        startup_reconciled = service.reconcile_startup()
                        if startup_reconciled:
                            result = startup_reconciled[-1].to_dict()
                except Exception:
                    if result["active_generation"] is None:
                        raise
                    result = {**store.status(), "status": "degraded", "refresh_failed": True}
        else:
            latest = service.latest()
            result = {
                **store.status(),
                "replica_contract": "single_replica_v1",
                "latest_refresh_operation": latest.to_dict() if latest is not None else None,
            }
    except ContentDeploymentError as exc:
        raise _ContentCommandError(
            str(exc),
            operation=f"content {args.content_command}",
        ) from exc
    except OSError as exc:
        if exc.errno not in {errno.EACCES, errno.EDQUOT, errno.ENOSPC, errno.EPERM, errno.EROFS}:
            raise
        state_root = (
            store.config.state_root
            if store is not None
            else Path(os.environ.get("FURA_CONTENT_STATE_ROOT", "/data/furatena"))
        )
        attempted_path = Path(exc.filename) if exc.filename else state_root
        reason = exc.strerror or exc.__class__.__name__
        raise _ContentCommandError(
            f"FURA_CONTENT_STATE_ROOT={state_root} cannot store managed content "
            f"at {attempted_path}: {reason}. Mount a writable Railway volume at "
            "/data/furatena or set FURA_CONTENT_STATE_ROOT to a writable persistent directory, "
            "then retry.",
            path=attempted_path,
            operation=f"content {args.content_command}",
        ) from exc
    print(json.dumps(result, indent=2, sort_keys=True))


def configure(sub: Any) -> None:
    content = sub.add_parser("content", help="Manage durable adopter content generations")
    commands = content.add_subparsers(dest="content_command", required=True)
    refresh = commands.add_parser("refresh", help="Fetch one exact commit and promote content")
    refresh.add_argument(
        "--expected-active-commit",
        required=True,
        help="Current 40-character active commit, or 'none' before first activation",
    )
    refresh.add_argument("--requested-commit", required=True, help="Exact reachable Git commit")
    refresh.add_argument("--idempotency-key", required=True)
    commands.add_parser("reconcile", help="Repair state and optionally refresh on startup")
    rollback = commands.add_parser("rollback", help="Select the last-known-good generation")
    rollback.add_argument("--reason", required=True)
    commands.add_parser("status", help="Report active and last-known-good generations")
    content.set_defaults(handler=_run_content)


COMMAND = CommandModule("content", configure)
