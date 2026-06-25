"""Parse and write ``objects.inv`` reference inventory files (no Sphinx dependency)."""

from __future__ import annotations

import zlib
from pathlib import Path

from furatena.catalog.inventories.models import InventoryEntry


def parse_objects_inv_bytes(raw: bytes, *, inventory_id: str = "") -> tuple[InventoryEntry, ...]:
    """Parse an ``objects.inv`` v2 inventory from raw bytes."""
    split = raw.find(b"\n\n")
    if split == -1:
        return ()
    compressed = raw[split + 2 :].strip()
    if not compressed:
        return ()
    try:
        payload = zlib.decompress(compressed)
    except zlib.error:
        payload = compressed
    text = payload.decode("utf-8", errors="replace")
    entries: list[InventoryEntry] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(None, 4)
        if len(parts) < 4:
            continue
        name, typ, priority_raw, uri = parts[0], parts[1], parts[2], parts[3]
        display = parts[4] if len(parts) > 4 else name
        if ":" in typ:
            domain, objtype = typ.split(":", 1)
        else:
            domain, objtype = "std", typ
        try:
            priority = int(priority_raw)
        except ValueError:
            priority = 0
        entries.append(
            InventoryEntry(
                domain=domain,
                name=name,
                objtype=objtype,
                uri=uri,
                display_name=display,
                priority=priority,
                inventory_id=inventory_id,
            )
        )
    return tuple(entries)


def parse_objects_inv_file(path: Path, *, inventory_id: str = "") -> tuple[InventoryEntry, ...]:
    return parse_objects_inv_bytes(path.read_bytes(), inventory_id=inventory_id)


def write_objects_inv_bytes(
    entries: tuple[InventoryEntry, ...],
    *,
    project: str = "Furatena",
    version: str = "1",
) -> bytes:
    """Serialize inventory entries to Sphinx v2 ``objects.inv`` bytes."""
    lines: list[str] = []
    for entry in entries:
        objtype = f"{entry.domain}:{entry.objtype}"
        display = entry.display_name or entry.name
        lines.append(f"{entry.name} {objtype} {entry.priority} {entry.uri} {display}")
    body = "\n".join(lines) + "\n"
    header = (
        "# Sphinx inventory version 2\n"
        f"# Project: {project}\n"
        f"# Version: {version}\n"
        "# The remainder of this file is compressed with zlib.\n\n"
    )
    return header.encode("utf-8") + zlib.compress(body.encode("utf-8"))
