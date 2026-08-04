"""Typed dev-server preflight state and compact startup presentation."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any

from furatena import __version__
from furatena.catalog.runtime import ServeConfig, ServeMode

_POUNCE_DISPLAY_DEFAULTS = (
    ("POUNCE_APP_NAME", "Furatena"),
    ("POUNCE_APP_TAGLINE", "Live documentation from markdown"),
    ("POUNCE_APP_VERSION", __version__),
    ("POUNCE_SIGNAGE", "minimal"),
)
_TRUE_VALUES = frozenset({"1", "true", "yes"})


class ServeStartupPhase(Enum):
    """Stable phases that machine consumers may observe before serving."""

    PREFLIGHT = "preflight"


@dataclass(frozen=True, slots=True)
class ServeReloadBehavior:
    """Actual reload and frozen-catalog behavior for one serve mode."""

    content: str
    theme: str
    python: str
    frozen_baseline: bool

    def to_dict(self) -> dict[str, str | bool]:
        return {
            "content": self.content,
            "theme": self.theme,
            "python": self.python,
            "frozen_baseline": self.frozen_baseline,
        }


@dataclass(frozen=True, slots=True)
class ServeStartupDiagnostic:
    """Actionable startup diagnostic independent of terminal rendering."""

    severity: str
    message: str
    rule_id: str
    source_path: str | None
    next_action: str


@dataclass(frozen=True, slots=True)
class ServeStartupResult:
    """Immutable result of configuration and the fail-fast contract preflight."""

    phase: ServeStartupPhase
    mode: ServeMode
    configured_url: str
    page_count: int
    mount_count: int
    frozen_dir: str | None
    stale_freeze: bool
    reload: ServeReloadBehavior
    checks_skipped: bool
    check_elapsed_ms: float
    diagnostics: tuple[ServeStartupDiagnostic, ...]

    @property
    def error_count(self) -> int:
        return sum(item.severity == "error" for item in self.diagnostics)

    @property
    def warning_count(self) -> int:
        return sum(item.severity == "warning" for item in self.diagnostics)

    @property
    def info_count(self) -> int:
        return sum(item.severity == "info" for item in self.diagnostics)

    @property
    def ok(self) -> bool:
        return self.error_count == 0

    def to_dict(self) -> dict[str, Any]:
        """Return the stable machine payload for the configured preflight phase."""
        return {
            "phase": self.phase.value,
            "ready": False,
            "configured_url": self.configured_url,
            "mode": self.mode.value,
            "page_count": self.page_count,
            "mount_count": self.mount_count,
            "frozen_dir": self.frozen_dir,
            "stale_freeze": self.stale_freeze,
            "reload": self.reload.to_dict(),
            "checks_skipped": self.checks_skipped,
            "check_elapsed_ms": round(self.check_elapsed_ms, 3),
            "error_count": self.error_count,
            "warning_count": self.warning_count,
            "info_count": self.info_count,
        }


def configure_pounce_display_defaults() -> None:
    """Brand Pounce startup output without overriding operator configuration."""
    for name, value in _POUNCE_DISPLAY_DEFAULTS:
        os.environ.setdefault(name, value)


def contract_checks_requested(value: str | None) -> bool:
    """Return whether the caller requested the normal serve contract pass."""
    return (value or "").strip().lower() not in _TRUE_VALUES


def serve_reload_behavior(serve: ServeConfig) -> ServeReloadBehavior:
    """Describe the effective mode without implying reload that is not configured."""
    if serve.mode == ServeMode.PREVIEW or not serve.auto_reload:
        return ServeReloadBehavior(
            content="frozen",
            theme="frozen",
            python="frozen",
            frozen_baseline=serve.frozen_dir is not None,
        )
    python_reload = (
        "restart"
        if os.environ.get("FURA_RELOAD_SRC", "").strip().lower() in _TRUE_VALUES
        else "manual restart"
    )
    return ServeReloadBehavior(
        content="htmx",
        theme="browser",
        python=python_reload,
        frozen_baseline=serve.mode == ServeMode.HYBRID,
    )


def compose_serve_preflight(
    app: Any,
    serve: ServeConfig,
    *,
    page_count: int,
    mount_count: int,
    configured_url: str,
    run_contract_checks: bool,
) -> ServeStartupResult:
    """Freeze once, run one explicit contract pass, and return typed startup state."""
    app.freeze()
    diagnostics: tuple[ServeStartupDiagnostic, ...] = ()
    elapsed_ms = 0.0
    if run_contract_checks:
        from chirp.contracts import check_hypermedia_surface

        started = time.perf_counter()
        check_result = check_hypermedia_surface(app)
        elapsed_ms = (time.perf_counter() - started) * 1000
        check_result.elapsed_ms = elapsed_ms
        diagnostics = tuple(_startup_diagnostic(issue) for issue in check_result.issues)
    return ServeStartupResult(
        phase=ServeStartupPhase.PREFLIGHT,
        mode=serve.mode,
        configured_url=configured_url,
        page_count=page_count,
        mount_count=mount_count,
        frozen_dir=str(serve.frozen_dir) if serve.frozen_dir is not None else None,
        stale_freeze=serve.warn_stale_freeze,
        reload=serve_reload_behavior(serve),
        checks_skipped=not run_contract_checks,
        check_elapsed_ms=elapsed_ms,
        diagnostics=diagnostics,
    )


def format_serve_preflight(result: ServeStartupResult) -> tuple[str, ...]:
    """Render stable, non-interactive human preflight lines without claiming readiness."""
    lines = [
        (f"Catalog   {result.page_count} pages · {result.mount_count} mounts · {result.mode.value}")
    ]
    if result.stale_freeze:
        lines.append("Freeze    stale · run `fura freeze` for a fresh export")
    if result.checks_skipped:
        lines.append("Checks    skipped · CHIRP_SKIP_CONTRACT_CHECKS=1")
    elif result.ok:
        finding = (
            "passed"
            if result.warning_count == 0
            else f"{result.warning_count} warnings · details: uv run fura check"
        )
        lines.append(f"Checks    {finding} · {_format_elapsed(result.check_elapsed_ms)}")
    else:
        lines.append(
            "Checks    failed · "
            f"{result.error_count} errors · {result.warning_count} warnings "
            f"· {_format_elapsed(result.check_elapsed_ms)}"
        )
        for diagnostic in result.diagnostics:
            origin = f"{diagnostic.source_path}: " if diagnostic.source_path else ""
            lines.append(f"{diagnostic.severity}: {origin}{diagnostic.message}")
            lines.append(f"  next: {diagnostic.next_action}")
    reload = result.reload
    if reload.content == "frozen":
        lines.append("Reload    frozen catalog · live reload off")
    else:
        baseline = "frozen baseline · " if reload.frozen_baseline else ""
        lines.append(
            f"Reload    {baseline}content: {reload.content} · theme: {reload.theme} "
            f"· Python: {reload.python}"
        )
    return tuple(lines)


def _startup_diagnostic(issue: Any) -> ServeStartupDiagnostic:
    category = str(issue.category or "contract")
    return ServeStartupDiagnostic(
        severity=issue.severity.value,
        message=issue.message,
        source_path=issue.template or issue.route,
        rule_id=f"chirp.{category}",
        next_action=(
            issue.details
            or f"Fix the Chirp {category.replace('_', ' ')} contract and rerun fura check."
        ),
    )


def _format_elapsed(elapsed_ms: float) -> str:
    if elapsed_ms < 1000:
        return f"{elapsed_ms:.1f}ms"
    return f"{elapsed_ms / 1000:.2f}s"
