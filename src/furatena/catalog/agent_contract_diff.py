"""Semantic compatibility diffs for versioned agent-output contract fixtures."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_IDENTITY_KEYS = ("node_id", "name", "id", "uri", "path", "operation_id")


def load_agent_contract_fixture(path: Path) -> dict[str, Any]:
    """Load and minimally validate one versioned agent contract fixture."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path}: agent contract fixture must be a JSON object")
    version = payload.get("fixture_version")
    if not isinstance(version, int) or version < 1:
        raise ValueError(f"{path}: fixture_version must be a positive integer")
    if not isinstance(payload.get("surfaces"), dict):
        raise ValueError(f"{path}: surfaces must be a JSON object")
    return payload


def diff_agent_contract_fixtures(
    old_path: Path,
    new_path: Path,
    *,
    compatibility_decision: str | None = None,
) -> dict[str, Any]:
    """Return an identity-aware semantic diff between two agent contract fixtures."""
    old = load_agent_contract_fixture(old_path)
    new = load_agent_contract_fixture(new_path)
    changes: dict[str, list[dict[str, Any]]] = {
        "added": [],
        "removed": [],
        "changed": [],
        "breaking": [],
    }
    _walk(old, new, path="$", changes=changes)
    for items in changes.values():
        items.sort(key=lambda item: (str(item.get("path")), str(item.get("reason", ""))))

    decision = str(compatibility_decision or "").strip()
    if changes["breaking"]:
        status = "accepted" if decision else "requires-decision"
    else:
        status = "compatible"
    return {
        "schema_version": 1,
        "old_fixture": str(old_path),
        "new_fixture": str(new_path),
        "old_fixture_version": old["fixture_version"],
        "new_fixture_version": new["fixture_version"],
        **changes,
        "summary": {key: len(changes[key]) for key in changes},
        "compatibility": {
            "status": status,
            "decision": decision or None,
            "required": bool(changes["breaking"]),
        },
    }


def _walk(old: Any, new: Any, *, path: str, changes: dict[str, list[dict[str, Any]]]) -> None:
    if type(old) is not type(new):
        _changed(changes, path, old, new)
        _breaking(changes, path, old, new, "value type changed")
        return
    if isinstance(old, dict):
        old_keys = set(old)
        new_keys = set(new)
        for key in sorted(new_keys - old_keys):
            changes["added"].append({"path": _field_path(path, key), "value": new[key]})
        for key in sorted(old_keys - new_keys):
            item_path = _field_path(path, key)
            changes["removed"].append({"path": item_path, "value": old[key]})
            _breaking(changes, item_path, old[key], None, "published field removed")
        for key in sorted(old_keys & new_keys):
            _walk(old[key], new[key], path=_field_path(path, key), changes=changes)
        return
    if isinstance(old, list):
        identity_key = _identity_key(old, new)
        if identity_key is not None:
            _walk_keyed_list(
                old,
                new,
                path=path,
                identity_key=identity_key,
                changes=changes,
            )
        else:
            _walk_value_list(old, new, path=path, changes=changes)
        return
    if old != new:
        _changed(changes, path, old, new)
        reason = _scalar_breaking_reason(path)
        if reason is not None:
            _breaking(changes, path, old, new, reason)


def _walk_keyed_list(
    old: list[Any],
    new: list[Any],
    *,
    path: str,
    identity_key: str,
    changes: dict[str, list[dict[str, Any]]],
) -> None:
    old_items = {str(item[identity_key]): item for item in old}
    new_items = {str(item[identity_key]): item for item in new}
    for identity in sorted(set(new_items) - set(old_items)):
        item_path = f"{path}[{identity_key}={identity}]"
        changes["added"].append({"path": item_path, "value": new_items[identity]})
    for identity in sorted(set(old_items) - set(new_items)):
        item_path = f"{path}[{identity_key}={identity}]"
        changes["removed"].append({"path": item_path, "value": old_items[identity]})
        _breaking(
            changes,
            item_path,
            old_items[identity],
            None,
            f"published {identity_key} record removed",
        )
    for identity in sorted(set(old_items) & set(new_items)):
        _walk(
            old_items[identity],
            new_items[identity],
            path=f"{path}[{identity_key}={identity}]",
            changes=changes,
        )


def _walk_value_list(
    old: list[Any],
    new: list[Any],
    *,
    path: str,
    changes: dict[str, list[dict[str, Any]]],
) -> None:
    old_values = {_canonical(item): item for item in old}
    new_values = {_canonical(item): item for item in new}
    for key in sorted(set(new_values) - set(old_values)):
        changes["added"].append({"path": path, "value": new_values[key]})
    for key in sorted(set(old_values) - set(new_values)):
        value = old_values[key]
        changes["removed"].append({"path": path, "value": value})
        _breaking(changes, path, value, None, "published list member removed")


def _identity_key(old: list[Any], new: list[Any]) -> str | None:
    items = [*old, *new]
    if not items or not all(isinstance(item, dict) for item in items):
        return None
    for key in _IDENTITY_KEYS:
        old_values = [str(item[key]) for item in old if key in item]
        new_values = [str(item[key]) for item in new if key in item]
        if (
            len(old_values) == len(old)
            and len(new_values) == len(new)
            and len(old_values) == len(set(old_values))
            and len(new_values) == len(set(new_values))
        ):
            return key
    return None


def _scalar_breaking_reason(path: str) -> str | None:
    field = path.rsplit(".", 1)[-1]
    if field in {"schema_version", "fixture_version", "version", "protocolVersion"}:
        return "published version changed"
    if field == "uri" or field == "url" or field.endswith("_url"):
        return "published URL or URI changed"
    return None


def _field_path(path: str, key: str) -> str:
    return f"{path}.{key}"


def _changed(
    changes: dict[str, list[dict[str, Any]]],
    path: str,
    old: Any,
    new: Any,
) -> None:
    changes["changed"].append({"path": path, "old": old, "new": new})


def _breaking(
    changes: dict[str, list[dict[str, Any]]],
    path: str,
    old: Any,
    new: Any,
    reason: str,
) -> None:
    changes["breaking"].append({"path": path, "old": old, "new": new, "reason": reason})


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))
