"""Dev reload wiring — browser hot-swap vs Pounce process restart."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from furatena.catalog.docs_app import DocsApp
    from furatena.catalog.theme import DocsTheme


def browser_reload_dirs(theme: DocsTheme) -> tuple[str, ...]:
    """Absolute paths for Chirp SSE browser reload (CSS/templates, not process restart)."""
    seen: set[Path] = set()
    unique: list[str] = []
    for path in theme.browser_reload_dirs:
        resolved = path.resolve()
        if resolved not in seen and resolved.is_dir():
            seen.add(resolved)
            unique.append(str(resolved))
    return tuple(unique)


@dataclass(frozen=True)
class DevServerRecord:
    pid: int
    host: str
    port: int


def dev_server_pid_path(repo_root: Path) -> Path:
    """PID file for the workspace dev server (Conductor/agent cleanup)."""
    return (repo_root / ".context" / "fura-serve.pid").resolve()


def read_dev_server_record(path: Path) -> DevServerRecord | None:
    if not path.is_file():
        return None
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
        if len(lines) < 3:
            return None
        return DevServerRecord(pid=int(lines[0]), host=lines[1], port=int(lines[2]))
    except OSError, ValueError:
        return None


def write_dev_server_record(path: Path, *, pid: int, host: str, port: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{pid}\n{host}\n{port}\n", encoding="utf-8")


def clear_dev_server_record(path: Path) -> None:
    with suppress(OSError):
        path.unlink(missing_ok=True)


def _pid_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _terminate_pid(pid: int, *, grace_seconds: float = 2.0) -> bool:
    if not _pid_is_alive(pid):
        return True
    sent = False
    try:
        os.kill(pid, signal.SIGTERM)
        sent = True
    except ProcessLookupError:
        return True
    except PermissionError:
        return False
    deadline = time.monotonic() + grace_seconds
    while time.monotonic() < deadline:
        if not _pid_is_alive(pid):
            return True
        time.sleep(0.05)
    try:
        os.kill(pid, signal.SIGKILL)
        sent = True
    except ProcessLookupError:
        return True
    except PermissionError:
        return sent
    deadline = time.monotonic() + grace_seconds
    while time.monotonic() < deadline:
        if not _pid_is_alive(pid):
            return True
        time.sleep(0.05)
    return sent


def _listener_pids(host: str, port: int) -> tuple[int, ...]:
    """PIDs listening on host:port (best-effort, Unix only)."""
    if os.name != "posix":
        return ()
    cmd = ["lsof", "-nP", f"-iTCP@{host}:{port}", "-sTCP:LISTEN", "-t"]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    except OSError:
        return ()
    if proc.returncode != 0:
        return ()
    seen: set[int] = set()
    pids: list[int] = []
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            pid = int(line)
        except ValueError:
            continue
        if pid not in seen:
            seen.add(pid)
            pids.append(pid)
    return tuple(pids)


def stop_dev_server(
    repo_root: Path,
    *,
    host: str | None = None,
    port: int | None = None,
) -> bool:
    """Stop a stray Furatena dev server for this workspace.

    Uses the PID file written by ``run_serve`` and, as a fallback, any process
    listening on the configured host/port.
    """
    pid_path = dev_server_pid_path(repo_root)
    record = read_dev_server_record(pid_path)
    resolved_host = host or (record.host if record else "127.0.0.1")
    resolved_port = port or (record.port if record else int(os.environ.get("FURA_PORT", "8001")))

    explicit_endpoint = host is not None or port is not None
    record_matches_request = (
        record is not None and record.host == resolved_host and record.port == resolved_port
    )

    stopped = False
    if (
        record is not None
        and _pid_is_alive(record.pid)
        and (not explicit_endpoint or record_matches_request)
    ):
        stopped = _terminate_pid(record.pid) or stopped
    for pid in _listener_pids(resolved_host, resolved_port):
        if record is not None and pid == record.pid:
            continue
        stopped = _terminate_pid(pid) or stopped
    if not explicit_endpoint or record_matches_request:
        clear_dev_server_record(pid_path)
    return stopped


def process_reload_dirs(repo_root: Path) -> tuple[str, ...]:
    """Absolute paths that should trigger a Pounce process restart (Python co-dev only)."""
    if os.environ.get("FURA_RELOAD_SRC", "").lower() not in {"1", "true", "yes"}:
        return ()
    src = (repo_root / "src" / "furatena").resolve()
    return (str(src),) if src.is_dir() else ()


def run_docs_dev_server(
    docs_app: DocsApp,
    *,
    host: str | None = None,
    port: int | None = None,
) -> None:
    """Start the docs dev server with split browser vs process reload layers.

    Chirp browser reload watches ``AppConfig.reload_dirs`` (theme/assets).
    Pounce process reload watches only ``process_reload_dirs`` (``src/furatena``
    when ``FURA_RELOAD_SRC=1``) plus the narrowed docs app cwd — not theme CSS.
    """
    from chirp.server.dev import run_dev_server

    app = docs_app.app
    resolved_host = host or app.config.host
    resolved_port = port or app.config.port
    docs_root = docs_app.config.root.resolve()
    previous_cwd = Path.cwd()

    app._ensure_frozen()
    os.chdir(docs_root)
    try:
        run_dev_server(
            app,
            resolved_host,
            resolved_port,
            reload=app.config.debug,
            reload_include=(),
            reload_dirs=process_reload_dirs(docs_app.repo_root),
        )
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        from chirp.server.terminal_errors import format_startup_error

        message = format_startup_error(exc)
        if message is None or os.environ.get("CHIRP_TRACEBACK", "").lower() == "full":
            raise
        print(message, file=sys.stderr)
        raise SystemExit(1) from exc
    finally:
        os.chdir(previous_cwd)
