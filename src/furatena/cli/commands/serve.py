"""serve command parser and execution."""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

from furatena.cli.commands._shared import (
    CommandModule,
    _app_root,
    _autodoc_config,
    _docs_yaml,
    _ensure_pythonpath,
    _json_output,
    _repo_for_app,
)
from furatena.cli.contracts import CommandResult, Diagnostic, ExitCode, command_name

if TYPE_CHECKING:
    from furatena.catalog.dev_banner import ServeStartupResult


def _is_loopback_host(host: str) -> bool:
    normalized = host.strip().strip("[]").lower()
    if normalized == "localhost":
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


def _run_serve(args: argparse.Namespace) -> None:
    _ensure_pythonpath()
    if args.author:
        os.environ["FURA_MODE"] = "author"
    elif args.preview or args.frozen:
        os.environ["FURA_MODE"] = "preview"
    elif args.hybrid:
        os.environ["FURA_MODE"] = "hybrid"
    if args.no_autodoc:
        os.environ["FURA_AUTODOC"] = "0"
    if args.channel:
        os.environ["FURA_CHANNEL"] = args.channel
    if args.base_url:
        os.environ["FURA_BASE_URL"] = args.base_url
    if args.port:
        os.environ["FURA_PORT"] = str(args.port)
    if args.workers is not None:
        os.environ["FURA_WORKERS"] = str(args.workers)

    from furatena.catalog.config import load_docs_config
    from furatena.catalog.docs_app import DocsApp
    from furatena.catalog.registry import load_mounts
    from furatena.catalog.runtime import ServeMode, resolve_serve_config

    app_root = _app_root(args)
    repo_root = _repo_for_app(app_root)
    docs_yaml = _docs_yaml(args)
    if not docs_yaml.is_file():
        raise SystemExit(f"docs config not found: {docs_yaml}")
    config = load_docs_config(docs_yaml)
    mounts = load_mounts(config.mounts_path or app_root / "mounts.yaml", repo_root=repo_root)
    mode = None
    if args.author:
        mode = ServeMode.AUTHOR
    elif args.preview or args.frozen:
        mode = ServeMode.PREVIEW
    elif args.hybrid:
        mode = ServeMode.HYBRID
    configured_frozen = os.environ.get("FURA_FROZEN_DIR", "").strip()
    frozen_dir = (
        Path(configured_frozen).expanduser().resolve() if configured_frozen else app_root / "frozen"
    )
    if os.environ.get("FURA_CONTENT_REPOSITORY", "").strip():
        from furatena.catalog.application_roots import ApplicationRoots
        from furatena.catalog.content_deployment import (
            ContentDeploymentConfig,
            ContentDeploymentError,
            ContentDeploymentStore,
        )
        from furatena.catalog.exceptions import CatalogConfigError

        roots = ApplicationRoots.from_environment(app_root)
        roots.ensure_writable_roots()
        deployment = ContentDeploymentConfig.from_environment()
        if deployment is None:  # pragma: no cover - guarded by the environment check
            raise ContentDeploymentError(
                "The managed content repository is not configured for generation selection."
            )
        try:
            ContentDeploymentStore(deployment).active_selection().require_runtime(
                site_root=app_root,
                frozen_root=frozen_dir,
            )
        except ContentDeploymentError as exc:
            raise CatalogConfigError(str(exc), operation="select managed generation") from exc
    serve = resolve_serve_config(
        docs_root=app_root,
        content_roots=tuple(mount.content_root for mount in mounts),
        mode=mode,
        frozen_dir=frozen_dir,
        env_frozen=bool(configured_frozen or os.environ.get("FURA_FROZEN")),
    )
    host = args.host or "127.0.0.1"
    if serve.mode == ServeMode.AUTHOR and not _is_loopback_host(host):
        raise SystemExit(
            "author mode may bind only to a loopback host until a trusted identity integration is configured"
        )
    contract_setting = os.environ.get("CHIRP_SKIP_CONTRACT_CHECKS")
    from furatena.catalog.dev_banner import contract_checks_requested

    run_contract_checks = contract_checks_requested(contract_setting)
    # The composed preflight owns the single check pass. AppConfig snapshots
    # environment during construction, so defer Chirp's constructor-time
    # renderer and restore the caller's process environment immediately after.
    os.environ["CHIRP_SKIP_CONTRACT_CHECKS"] = "1"
    try:
        docs = DocsApp.from_paths(
            docs_yaml,
            repo_root=repo_root,
            autodoc_config=_autodoc_config(args, repo_root),
            serve=serve,
            workers=args.workers,
        )
    finally:
        if contract_setting is None:
            os.environ.pop("CHIRP_SKIP_CONTRACT_CHECKS", None)
        else:
            os.environ["CHIRP_SKIP_CONTRACT_CHECKS"] = contract_setting

    port = args.port or int(os.environ.get("FURA_PORT", "8001"))
    url = f"http://{host}:{port}/"
    from furatena.catalog.dev_banner import compose_serve_preflight

    startup = compose_serve_preflight(
        docs.app,
        docs.serve,
        page_count=len(docs.catalog.nodes),
        mount_count=len(docs.catalog.mounts),
        configured_url=url,
        run_contract_checks=(run_contract_checks and docs.serve.mode != ServeMode.PREVIEW),
    )
    result = _startup_command_result(args, startup, host=host, port=port)
    if _json_output(args):
        result.write_json()
    else:
        _write_human_preflight(startup, structured=docs.app.config.log_format == "json")
    if not startup.ok:
        raise SystemExit(int(ExitCode.VALIDATION_ERROR))
    docs.run_serve(port=port, host=host)


def _startup_command_result(
    args: argparse.Namespace,
    startup: ServeStartupResult,
    *,
    host: str,
    port: int,
) -> CommandResult:
    diagnostics = tuple(
        Diagnostic(
            severity=item.severity,
            message=item.message,
            source_path=item.source_path,
            rule_id=item.rule_id,
            next_action=item.next_action,
        )
        for item in startup.diagnostics
    )
    data = startup.to_dict()
    data.update({"host": host, "port": port, "workers": args.workers})
    return CommandResult(
        command=command_name(args),
        ok=startup.ok,
        exit_code=ExitCode.SUCCESS if startup.ok else ExitCode.VALIDATION_ERROR,
        summary="serve preflight passed" if startup.ok else "serve preflight failed",
        diagnostics=diagnostics,
        data=data,
    )


def _write_human_preflight(startup: ServeStartupResult, *, structured: bool) -> None:
    if structured:
        payload = {
            "level": "info" if startup.ok else "error",
            "event": "furatena.serve.preflight",
            **startup.to_dict(),
            "diagnostics": [
                {
                    "severity": item.severity,
                    "message": item.message,
                    "source_path": item.source_path,
                    "rule_id": item.rule_id,
                    "next_action": item.next_action,
                }
                for item in startup.diagnostics
            ],
        }
        sys.stderr.write(json.dumps(payload, separators=(",", ":"), sort_keys=True) + "\n")
        return
    from furatena.catalog.dev_banner import format_serve_preflight

    for line in format_serve_preflight(startup):
        print(line, file=sys.stderr)


def configure(sub: Any) -> None:
    serve = sub.add_parser("serve", help="Run the Furatena app")
    serve.add_argument("--host", default=None, help="Bind host (default 127.0.0.1)")
    serve.add_argument("--port", type=int, default=None, help="Bind port (default 8001)")
    mode = serve.add_mutually_exclusive_group()
    mode.add_argument("--author", action="store_true", help="Live index from source")
    mode.add_argument("--preview", action="store_true", help="Frozen catalog only")
    mode.add_argument("--hybrid", action="store_true", help="Frozen baseline + live overlay")
    serve.add_argument("--frozen", action="store_true", help="Alias for --preview")
    serve.add_argument("--no-autodoc", action="store_true", help="Skip autodoc slice")
    serve.add_argument("--channel", default=None, help="Version channel (FURA_CHANNEL)")
    serve.add_argument("--base-url", default=None, help="Public origin (FURA_BASE_URL)")
    serve.add_argument("--workers", type=int, default=None, help="Parallel index workers")
    serve.add_argument(
        "--json", action="store_true", help="Emit preflight as standard command result JSON"
    )
    serve.set_defaults(handler=_run_serve)


COMMAND = CommandModule("serve", configure)
