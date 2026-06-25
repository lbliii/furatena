"""Parallel worker configuration for docs catalog indexing and freeze."""

from __future__ import annotations

import os

DEFAULT_MAX_WORKERS = 8


def resolve_workers(explicit: int | None = None) -> int:
    """Return thread-pool size for catalog index/freeze (1 = sequential).

    Resolution order: explicit CLI value, ``FURA_WORKERS`` env, then
    ``min(cpu_count, 8)`` when more than one CPU is available.
    """
    if explicit is not None:
        if explicit <= 0:
            return 1
        return explicit
    raw = os.environ.get("FURA_WORKERS", "").strip()
    if raw:
        try:
            parsed = int(raw)
            if parsed > 0:
                return parsed
        except ValueError:
            pass
    cpus = os.cpu_count() or 1
    if cpus <= 1:
        return 1
    return min(cpus, DEFAULT_MAX_WORKERS)
