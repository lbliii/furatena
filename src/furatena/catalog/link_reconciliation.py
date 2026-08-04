"""Persistent incremental link state for federated catalog shards."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from contextlib import suppress
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from furatena.catalog.graph import normalize_internal_url

_SCHEMA_VERSION = 1
_HEALTH_LINK_LIMIT = 100


@dataclass(frozen=True, slots=True)
class LinkNode:
    node_id: str
    mount: str
    edition: str
    title: str
    url: str


@dataclass(frozen=True, slots=True)
class OutboundLink:
    source_id: str
    source_mount: str
    source_edition: str
    source_title: str
    source_url: str
    target_url: str
    target_mount: str
    label: str = ""
    line: int | None = None


@dataclass(frozen=True, slots=True)
class ShardLinkSnapshot:
    key: str
    mount: str
    edition: str
    fingerprint: str
    nodes: tuple[LinkNode, ...]
    outbound: tuple[OutboundLink, ...]


def local_shard_link_snapshot(
    shard: Any,
    *,
    target_mount_for_url: Callable[[str], str],
) -> ShardLinkSnapshot:
    """Extract deterministic link records from one local Content-IR shard."""
    nodes: list[LinkNode] = []
    outbound: list[OutboundLink] = []
    for node in sorted(shard.nodes, key=lambda item: item.node_id):
        nodes.append(
            LinkNode(
                node_id=node.node_id,
                mount=node.mount,
                edition=node.edition,
                title=node.title,
                url=node.url,
            )
        )
        content_ir = node.content_ir
        links = content_ir.links if content_ir is not None else ()
        seen: set[str] = set()
        for link in links:
            target = normalize_internal_url(link.href)
            if target is None or target in seen:
                continue
            seen.add(target)
            outbound.append(
                OutboundLink(
                    source_id=node.node_id,
                    source_mount=node.mount,
                    source_edition=node.edition,
                    source_title=node.title,
                    source_url=node.url,
                    target_url=target,
                    target_mount=target_mount_for_url(target),
                    label=link.text,
                    line=link.line,
                )
            )
    nodes_tuple = tuple(nodes)
    outbound_tuple = tuple(sorted(outbound, key=_edge_sort_key))
    fingerprint = _snapshot_fingerprint(nodes_tuple, outbound_tuple)
    mount = str(getattr(shard, "mount", "") or (nodes[0].mount if nodes else ""))
    edition = str(getattr(shard, "active_channel", "") or (nodes[0].edition if nodes else "latest"))
    return ShardLinkSnapshot(
        key=f"{mount}:{edition}",
        mount=mount,
        edition=edition,
        fingerprint=fingerprint,
        nodes=nodes_tuple,
        outbound=outbound_tuple,
    )


def remote_shard_link_snapshot(
    catalog: Mapping[str, Any],
    *,
    mount: str,
    edition: str,
    fingerprint: str,
    target_mount_for_url: Callable[[str], str],
) -> ShardLinkSnapshot:
    """Extract link records directly from a verified DCP catalog mapping."""
    nodes: list[LinkNode] = []
    outbound: list[OutboundLink] = []
    pages = catalog.get("pages")
    for page in sorted(
        pages if isinstance(pages, Sequence) else (), key=lambda item: item["node_id"]
    ):
        node_id = str(page["node_id"])
        title = str(page["title"])
        url = str(page["url"])
        nodes.append(LinkNode(node_id, mount, edition, title, url))
        content = page.get("content")
        links = content.get("links") if isinstance(content, Mapping) else ()
        seen: set[str] = set()
        for raw in links if isinstance(links, Sequence) else ():
            if not isinstance(raw, Mapping):
                continue
            target = normalize_internal_url(str(raw.get("href") or ""))
            if target is None or target in seen:
                continue
            seen.add(target)
            raw_line = raw.get("line")
            outbound.append(
                OutboundLink(
                    source_id=node_id,
                    source_mount=mount,
                    source_edition=edition,
                    source_title=title,
                    source_url=url,
                    target_url=target,
                    target_mount=target_mount_for_url(target),
                    label=str(raw.get("text") or ""),
                    line=raw_line if isinstance(raw_line, int) else None,
                )
            )
    return ShardLinkSnapshot(
        key=f"{mount}:{edition}",
        mount=mount,
        edition=edition,
        fingerprint=fingerprint,
        nodes=tuple(nodes),
        outbound=tuple(sorted(outbound, key=_edge_sort_key)),
    )


class IncrementalLinkIndex:
    """Thread-safe, disk-backed link index updated by shard edge neighborhood."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.RLock()
        self._shards: dict[str, ShardLinkSnapshot] = {}
        self._nodes: dict[tuple[str, str], LinkNode] = {}
        self._incoming: dict[tuple[str, str], dict[tuple[str, str], OutboundLink]] = {}
        self._backlinks: dict[tuple[str, str], tuple[dict[str, str], ...]] = {}
        self._broken: dict[tuple[str, str], tuple[OutboundLink, ...]] = {}
        self._load_error: str | None = None
        self._last: dict[str, Any] = {
            "changed_shards": 0,
            "total_shards": 0,
            "untouched_shards": 0,
            "scanned_source_nodes": 0,
            "neighborhood_edges": 0,
            "affected_targets": 0,
            "elapsed_ms": 0.0,
        }
        self._load()

    def fingerprint_for(self, key: str) -> str | None:
        with self._lock:
            snapshot = self._shards.get(key)
            return snapshot.fingerprint if snapshot is not None else None

    def shard_keys(self) -> frozenset[str]:
        with self._lock:
            return frozenset(self._shards)

    def shard_keys_for_mount(self, mount: str) -> frozenset[str]:
        """Return stored shard keys owned by one exact mount identity."""
        with self._lock:
            return frozenset(
                key for key, snapshot in self._shards.items() if snapshot.mount == mount
            )

    def reconcile(
        self,
        replacements: Mapping[str, ShardLinkSnapshot | None],
        *,
        retain_keys: frozenset[str] | None = None,
    ) -> dict[str, Any]:
        """Replace changed shards and persist one atomic index generation."""
        started = time.perf_counter()
        with self._lock:
            pending = dict(replacements)
            if retain_keys is not None:
                pending.update(
                    (key, None) for key in self._shards.keys() - retain_keys - pending.keys()
                )
            pending = {
                key: value
                for key, value in pending.items()
                if not _same_snapshot(self._shards.get(key), value)
            }
            scanned_nodes = 0
            neighborhood_edges: set[tuple[str, str]] = set()
            affected_targets: set[tuple[str, str]] = set()
            previous = {key: self._shards.get(key) for key in pending}
            try:
                for key, replacement in sorted(pending.items()):
                    old = self._shards.get(key)
                    if old is not None:
                        scanned_nodes += len(old.nodes)
                        affected_targets.update((old.edition, node.url) for node in old.nodes)
                        for edge in old.outbound:
                            edge_id = (edge.source_id, edge.target_url)
                            neighborhood_edges.add(edge_id)
                            affected_targets.add((edge.source_edition, edge.target_url))
                            incoming = self._incoming.get((edge.source_edition, edge.target_url))
                            if incoming is not None:
                                incoming.pop(edge_id, None)
                                if not incoming:
                                    self._incoming.pop((edge.source_edition, edge.target_url), None)
                        for node in old.nodes:
                            self._nodes.pop((old.edition, node.url), None)
                        self._shards.pop(key, None)
                    if replacement is not None:
                        scanned_nodes += len(replacement.nodes)
                        self._shards[key] = replacement
                        for node in replacement.nodes:
                            self._nodes[(replacement.edition, node.url)] = node
                            affected_targets.add((replacement.edition, node.url))
                        for edge in replacement.outbound:
                            edge_id = (edge.source_id, edge.target_url)
                            neighborhood_edges.add(edge_id)
                            target_key = (edge.source_edition, edge.target_url)
                            affected_targets.add(target_key)
                            self._incoming.setdefault(target_key, {})[edge_id] = edge
                for target_key in affected_targets:
                    neighborhood_edges.update(self._incoming.get(target_key, ()))
                    self._refresh_target(target_key)
                self._write(frozenset(pending))
            except BaseException:
                for key in pending:
                    self._shards.pop(key, None)
                for key, snapshot in previous.items():
                    if snapshot is not None:
                        self._shards[key] = snapshot
                self._rebuild()
                raise
            if pending:
                self._load_error = None
            self._last = {
                "changed_shards": len(pending),
                "total_shards": len(self._shards),
                "untouched_shards": max(0, len(self._shards) - len(pending)),
                "scanned_source_nodes": scanned_nodes,
                "neighborhood_edges": len(neighborhood_edges),
                "affected_targets": len(affected_targets),
                "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
            }
            return dict(self._last)

    def backlinks(self, edition: str) -> dict[str, list[dict[str, str]]]:
        with self._lock:
            return {
                url: [dict(ref) for ref in refs]
                for (item_edition, url), refs in self._backlinks.items()
                if item_edition == edition
            }

    def reconcile_with_backlinks(
        self, snapshot: ShardLinkSnapshot, *, edition: str
    ) -> dict[str, list[dict[str, str]]]:
        """Publish one shard and return its generation-consistent backlink view."""
        with self._lock:
            if self.fingerprint_for(snapshot.key) != snapshot.fingerprint:
                self.reconcile({snapshot.key: snapshot})
            return self.backlinks(edition)

    def backlinks_for(self, edition: str, url: str) -> list[dict[str, str]]:
        with self._lock:
            return [dict(ref) for ref in self._backlinks.get((edition, url), ())]

    def mount_health(self, mount: str) -> dict[str, Any]:
        with self._lock:
            links = sorted(
                (
                    edge
                    for edges in self._broken.values()
                    for edge in edges
                    if edge.source_mount == mount
                ),
                key=_edge_sort_key,
            )
            return {
                "schema_version": _SCHEMA_VERSION,
                "status": "degraded" if links else "healthy",
                "broken_cross_shard_count": len(links),
                "broken_cross_shard_links": [
                    {
                        "source": edge.source_url,
                        "target": edge.target_url,
                        "target_mount": edge.target_mount,
                        "edition": edge.source_edition,
                        **({"label": edge.label} if edge.label else {}),
                        **({"line": edge.line} if edge.line is not None else {}),
                    }
                    for edge in links[:_HEALTH_LINK_LIMIT]
                ],
                "truncated": len(links) > _HEALTH_LINK_LIMIT,
            }

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "schema_version": _SCHEMA_VERSION,
                "persistence": "atomic-sharded-json",
                "load_error": self._load_error,
                "shard_count": len(self._shards),
                "node_count": len(self._nodes),
                "edge_count": sum(len(edges) for edges in self._incoming.values()),
                "broken_cross_shard_count": sum(len(edges) for edges in self._broken.values()),
                "last_reconciliation": dict(self._last),
            }

    def _refresh_target(self, target_key: tuple[str, str]) -> None:
        edges = tuple(self._incoming.get(target_key, {}).values())
        target = self._nodes.get(target_key)
        if target is None:
            self._backlinks.pop(target_key, None)
            broken = tuple(
                sorted(
                    (
                        edge
                        for edge in edges
                        if edge.target_mount and edge.target_mount != edge.source_mount
                    ),
                    key=_edge_sort_key,
                )
            )
            if broken:
                self._broken[target_key] = broken
            else:
                self._broken.pop(target_key, None)
            return
        self._broken.pop(target_key, None)
        deduped: dict[str, dict[str, str]] = {}
        for edge in edges:
            if edge.source_url == target.url:
                continue
            deduped[edge.source_url] = {
                "title": edge.source_title,
                "href": edge.source_url,
            }
        refs = tuple(sorted(deduped.values(), key=lambda ref: (ref["title"].lower(), ref["href"])))
        if refs:
            self._backlinks[target_key] = refs
        else:
            self._backlinks.pop(target_key, None)

    def _rebuild(self) -> None:
        self._nodes = {}
        self._incoming = {}
        self._backlinks = {}
        self._broken = {}
        for snapshot in self._shards.values():
            for node in snapshot.nodes:
                self._nodes[(snapshot.edition, node.url)] = node
            for edge in snapshot.outbound:
                key = (edge.source_edition, edge.target_url)
                self._incoming.setdefault(key, {})[(edge.source_id, edge.target_url)] = edge
        for key in self._incoming:
            self._refresh_target(key)

    def _load(self) -> None:
        shards_root = self.path / "shards"
        if not shards_root.is_dir():
            return
        loaded: dict[str, ShardLinkSnapshot] = {}
        errors: list[str] = []
        for path in sorted(shards_root.glob("*.json")):
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(raw, dict):
                    raise ValueError("link-index record must be a JSON object")
                integrity = raw.get("integrity_sha256")
                authenticated = {
                    key: value for key, value in raw.items() if key != "integrity_sha256"
                }
                if not isinstance(integrity, str) or not hmac.compare_digest(
                    integrity, _record_integrity(authenticated)
                ):
                    raise ValueError("link-index record integrity check failed")
                schema_version = raw.get("schema_version")
                if type(schema_version) is not int or schema_version != _SCHEMA_VERSION:
                    raise ValueError("unsupported link-index schema version")
                key = raw.get("key")
                if not isinstance(key, str) or path.stem != _shard_state_name(key):
                    raise ValueError("link-index filename does not match its shard key")
                if raw.get("deleted") is True:
                    continue
                snapshot = _snapshot_from_dict(raw.get("shard"))
                if snapshot.key != key:
                    raise ValueError("link-index record key does not match its shard payload")
                if snapshot.key in loaded:
                    raise ValueError(f"duplicate link-index shard {snapshot.key!r}")
                loaded[snapshot.key] = snapshot
            except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
                errors.append(f"{path.name}: {exc}")
        self._shards = loaded
        self._rebuild()
        if errors:
            self._load_error = (
                "Persistent link shard(s) could not be loaded: "
                + "; ".join(errors[:4])
                + ". The next successful reconciliation for each shard will repair it."
            )

    def _write(self, changed_keys: frozenset[str]) -> None:
        shards_root = self.path / "shards"
        shards_root.mkdir(parents=True, exist_ok=True)
        for key in sorted(changed_keys):
            snapshot = self._shards.get(key)
            payload: dict[str, Any] = {
                "schema_version": _SCHEMA_VERSION,
                "key": key,
            }
            if snapshot is None:
                payload["deleted"] = True
            else:
                payload["shard"] = _snapshot_to_dict(snapshot)
            payload["integrity_sha256"] = _record_integrity(payload)
            encoded = (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode()
            target = shards_root / f"{_shard_state_name(key)}.json"
            self._atomic_write(target, encoded)

    @staticmethod
    def _atomic_write(target: Path, encoded: bytes) -> None:
        temporary = target.with_name(f".{target.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        try:
            with temporary.open("wb") as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            temporary.replace(target)
            directory_fd = os.open(target.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            with suppress(FileNotFoundError):
                temporary.unlink()


def _snapshot_fingerprint(nodes: tuple[LinkNode, ...], outbound: tuple[OutboundLink, ...]) -> str:
    payload = {
        "nodes": [asdict(node) for node in nodes],
        "outbound": [asdict(edge) for edge in outbound],
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _shard_state_name(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def _record_integrity(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _edge_sort_key(edge: OutboundLink) -> tuple[str, str, str]:
    return edge.source_mount, edge.source_url, edge.target_url


def _same_snapshot(old: ShardLinkSnapshot | None, replacement: ShardLinkSnapshot | None) -> bool:
    if old is None or replacement is None:
        return old is replacement
    return old.fingerprint == replacement.fingerprint


def _snapshot_to_dict(snapshot: ShardLinkSnapshot) -> dict[str, Any]:
    return {
        "key": snapshot.key,
        "mount": snapshot.mount,
        "edition": snapshot.edition,
        "fingerprint": snapshot.fingerprint,
        "nodes": [asdict(node) for node in snapshot.nodes],
        "outbound": [asdict(edge) for edge in snapshot.outbound],
    }


def _snapshot_from_dict(raw: Any) -> ShardLinkSnapshot:
    if not isinstance(raw, dict):
        raise ValueError("link-index shard must be an object")
    nodes = tuple(LinkNode(**item) for item in raw.get("nodes", ()))
    outbound = tuple(OutboundLink(**item) for item in raw.get("outbound", ()))
    snapshot = ShardLinkSnapshot(
        key=str(raw["key"]),
        mount=str(raw["mount"]),
        edition=str(raw["edition"]),
        fingerprint=str(raw["fingerprint"]),
        nodes=nodes,
        outbound=outbound,
    )
    if snapshot.key != f"{snapshot.mount}:{snapshot.edition}":
        raise ValueError(f"link-index shard key is inconsistent: {snapshot.key!r}")
    return snapshot
