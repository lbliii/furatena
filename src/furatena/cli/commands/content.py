"""Manage Railway adopter content generations."""

from __future__ import annotations

import argparse
import json
from typing import Any

from furatena.catalog.content_deployment import (
    ContentDeploymentConfig,
    ContentDeploymentError,
    ContentDeploymentStore,
)
from furatena.cli.commands._shared import CommandModule


def _store() -> ContentDeploymentStore:
    config = ContentDeploymentConfig.from_environment()
    if config is None:
        raise ContentDeploymentError("FURA_CONTENT_REPOSITORY is not configured")
    return ContentDeploymentStore(config)


def _run_content(args: argparse.Namespace) -> None:
    store = _store()
    if args.content_command == "refresh":
        result = store.refresh(trigger=args.trigger)
    elif args.content_command == "rollback":
        result = store.rollback()
    elif args.content_command == "reconcile":
        result = store.reconcile()
        if store.startup_refresh_needed():
            try:
                result = store.refresh(trigger="startup")
            except Exception:
                if result["active_generation"] is None:
                    raise
                result = {**store.status(), "status": "degraded", "refresh_failed": True}
    else:
        result = store.status()
    print(json.dumps(result, indent=2, sort_keys=True))


def configure(sub: Any) -> None:
    content = sub.add_parser("content", help="Manage durable adopter content generations")
    commands = content.add_subparsers(dest="content_command", required=True)
    refresh = commands.add_parser("refresh", help="Fetch, validate, freeze, and promote content")
    refresh.add_argument("--trigger", default="manual")
    commands.add_parser("reconcile", help="Repair state and optionally refresh on startup")
    commands.add_parser("rollback", help="Select the last-known-good generation")
    commands.add_parser("status", help="Report active and last-known-good generations")
    content.set_defaults(handler=_run_content)


COMMAND = CommandModule("content", configure)
