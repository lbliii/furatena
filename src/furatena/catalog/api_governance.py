"""OpenAPI linting and change summaries for API documentation governance."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

HTTP_METHODS = {"delete", "get", "head", "options", "patch", "post", "put", "trace"}


def lint_openapi_autodoc_config(config_path: Path | None, *, repo_root: Path) -> tuple[list[str], list[str]]:
    """Lint OpenAPI specs referenced by an autodoc config."""
    if config_path is None or not config_path.is_file():
        return [], []
    try:
        config = _load_yaml(config_path)
    except Exception as exc:
        return [f"{config_path}: invalid autodoc config: {exc}"], []
    autodoc = config.get("autodoc") if isinstance(config.get("autodoc"), dict) else config
    openapi = autodoc.get("openapi") if isinstance(autodoc.get("openapi"), dict) else {}
    if not openapi or openapi.get("enabled") is False:
        return [], []
    errors: list[str] = []
    warnings: list[str] = []
    for raw in openapi.get("specs") or openapi.get("sources") or ():
        spec_path = _resolve_spec_path(raw, repo_root=repo_root)
        if spec_path is None:
            errors.append(f"{config_path}: OpenAPI spec path is empty")
            continue
        spec_errors, spec_warnings = lint_openapi_spec(spec_path)
        errors.extend(spec_errors)
        warnings.extend(spec_warnings)
    return sorted(errors), sorted(warnings)


def lint_openapi_spec(spec_path: Path) -> tuple[list[str], list[str]]:
    """Return errors and warnings for one OpenAPI spec."""
    if not spec_path.is_file():
        return [f"{spec_path}: OpenAPI spec not found"], []
    try:
        spec = _load_yaml(spec_path)
    except Exception as exc:
        return [f"{spec_path}: invalid OpenAPI spec: {exc}"], []
    if not isinstance(spec, dict) or not spec.get("openapi"):
        return [f"{spec_path}: invalid OpenAPI spec: missing openapi version"], []
    errors: list[str] = []
    warnings: list[str] = []
    operations = _operation_records(spec)
    if not operations:
        warnings.append(f"{spec_path}: OpenAPI spec defines no operations")
    for operation in operations:
        prefix = f"{spec_path}: {operation['method']} {operation['path']}"
        body = operation["operation"]
        if not body.get("operationId"):
            warnings.append(f"{prefix}: missing operationId")
        if not body.get("summary") and not body.get("description"):
            warnings.append(f"{prefix}: missing summary or description for agent/tool readiness")
        errors.extend(_broken_examples(spec_path, operation))
        for ref in sorted(_iter_refs(body)):
            if ref.startswith("#/") and _resolve_json_pointer(spec, ref) is None:
                errors.append(f"{prefix}: unresolved schema reference {ref}")
    return sorted(errors), sorted(warnings)


def diff_openapi_specs(old_path: Path, new_path: Path) -> dict[str, Any]:
    """Compare two OpenAPI specs by operation and summarize changes."""
    old_spec = _load_yaml(old_path)
    new_spec = _load_yaml(new_path)
    old_ops = _operation_map(old_spec)
    new_ops = _operation_map(new_spec)
    added_keys = sorted(set(new_ops) - set(old_ops))
    removed_keys = sorted(set(old_ops) - set(new_ops))
    shared_keys = sorted(set(old_ops) & set(new_ops))
    changed: list[dict[str, Any]] = []
    breaking: list[dict[str, Any]] = []
    for key in shared_keys:
        old = old_ops[key]
        new = new_ops[key]
        changes = _operation_changes(old, new)
        if changes:
            changed.append({**_operation_summary(new), "changes": changes})
        breaking_changes = _breaking_changes(old, new)
        if breaking_changes:
            breaking.append({**_operation_summary(new), "changes": breaking_changes})
    for key in removed_keys:
        breaking.append({**_operation_summary(old_ops[key]), "changes": ["operation removed"]})
    return {
        "schema_version": 1,
        "old_spec": str(old_path),
        "new_spec": str(new_path),
        "added": [_operation_summary(new_ops[key]) for key in added_keys],
        "removed": [_operation_summary(old_ops[key]) for key in removed_keys],
        "changed": changed,
        "breaking": breaking,
        "summary": {
            "added": len(added_keys),
            "removed": len(removed_keys),
            "changed": len(changed),
            "breaking": len(breaking),
        },
    }


def _load_yaml(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def _resolve_spec_path(raw: Any, *, repo_root: Path) -> Path | None:
    if raw in (None, ""):
        return None
    path = Path(str(raw)).expanduser()
    return path if path.is_absolute() else repo_root / path


def _operation_records(spec: dict[str, Any]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    paths = spec.get("paths") if isinstance(spec.get("paths"), dict) else {}
    for path, item in sorted(paths.items()):
        if not isinstance(item, dict):
            continue
        for method, operation in sorted(item.items()):
            if method.lower() not in HTTP_METHODS or not isinstance(operation, dict):
                continue
            records.append(
                {
                    "method": method.upper(),
                    "path": str(path),
                    "operation": operation,
                }
            )
    return records


def _operation_map(spec: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    return {
        (record["method"], record["path"]): record
        for record in _operation_records(spec)
    }


def _operation_summary(record: dict[str, Any]) -> dict[str, Any]:
    operation = record["operation"]
    return {
        "method": record["method"],
        "path": record["path"],
        "operation_id": operation.get("operationId"),
        "summary": operation.get("summary") or operation.get("description") or "",
    }


def _iter_refs(value: Any):
    if isinstance(value, dict):
        ref = value.get("$ref")
        if isinstance(ref, str):
            yield ref
        for nested in value.values():
            yield from _iter_refs(nested)
    elif isinstance(value, list):
        for item in value:
            yield from _iter_refs(item)


def _resolve_json_pointer(spec: dict[str, Any], ref: str) -> Any:
    current: Any = spec
    for raw_part in ref.removeprefix("#/").split("/"):
        part = raw_part.replace("~1", "/").replace("~0", "~")
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return None
    return current


def _broken_examples(spec_path: Path, record: dict[str, Any]) -> list[str]:
    operation = record["operation"]
    errors: list[str] = []
    for section_name in ("requestBody", "responses"):
        section = operation.get(section_name)
        for content_type, media in _content_items(section):
            examples = media.get("examples") if isinstance(media, dict) else None
            if isinstance(examples, dict):
                for name, example in examples.items():
                    if not isinstance(example, dict) or not (
                        "value" in example or "externalValue" in example or "$ref" in example
                    ):
                        errors.append(
                            f"{spec_path}: {record['method']} {record['path']}: "
                            f"broken example {section_name}.{content_type}.{name}"
                        )
            example = media.get("example") if isinstance(media, dict) else None
            if isinstance(example, dict) and "$ref" in example:
                continue
    return errors


def _content_items(section: Any):
    if isinstance(section, dict) and "$ref" in section:
        return
    if isinstance(section, dict) and "content" in section:
        content = section.get("content")
        if isinstance(content, dict):
            for content_type, media in content.items():
                yield str(content_type), media
        return
    if isinstance(section, dict):
        for response in section.values():
            if not isinstance(response, dict):
                continue
            content = response.get("content")
            if isinstance(content, dict):
                for content_type, media in content.items():
                    yield str(content_type), media


def _operation_signature(record: dict[str, Any]) -> dict[str, Any]:
    operation = record["operation"]
    return {
        "operation_id": operation.get("operationId"),
        "summary": operation.get("summary"),
        "request_refs": sorted(_refs_under(operation.get("requestBody"))),
        "response_codes": sorted(str(key) for key in (operation.get("responses") or {})),
        "response_refs": sorted(_refs_under(operation.get("responses"))),
        "security": operation.get("security"),
    }


def _refs_under(value: Any) -> set[str]:
    return {ref for ref in _iter_refs(value)}


def _operation_changes(old: dict[str, Any], new: dict[str, Any]) -> list[str]:
    old_sig = _operation_signature(old)
    new_sig = _operation_signature(new)
    changes: list[str] = []
    for key, label in (
        ("operation_id", "operationId changed"),
        ("summary", "summary changed"),
        ("request_refs", "request schema references changed"),
        ("response_codes", "response codes changed"),
        ("response_refs", "response schema references changed"),
        ("security", "security requirements changed"),
    ):
        if old_sig[key] != new_sig[key]:
            changes.append(label)
    return changes


def _breaking_changes(old: dict[str, Any], new: dict[str, Any]) -> list[str]:
    old_sig = _operation_signature(old)
    new_sig = _operation_signature(new)
    changes: list[str] = []
    removed_codes = set(old_sig["response_codes"]) - set(new_sig["response_codes"])
    if removed_codes:
        changes.append(f"response codes removed: {', '.join(sorted(removed_codes))}")
    removed_response_refs = set(old_sig["response_refs"]) - set(new_sig["response_refs"])
    if removed_response_refs:
        changes.append(f"response schemas removed: {', '.join(sorted(removed_response_refs))}")
    if old_sig["request_refs"] != new_sig["request_refs"]:
        changes.append("request schema references changed")
    if not old_sig["security"] and new_sig["security"]:
        changes.append("security requirement added")
    return changes
