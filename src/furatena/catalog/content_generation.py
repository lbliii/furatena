"""Versioned integrity contracts for immutable managed-content generations."""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import yaml

from furatena.catalog.lifecycle import is_public_meta
from furatena.catalog.renderer_fingerprint import read_renderer_fingerprint
from furatena.catalog.visibility_audit import (
    VisibilityCanary,
    scan_visibility_leaks,
)

GENERATION_CONTRACT_VERSION = 1
_CONFIG_SUFFIXES = frozenset({".yaml", ".yml", ".toml"})
_REQUIRED_FROZEN = ("catalog.json", "search.json", "semantic.json", "llms-full.txt")


class GenerationContractError(RuntimeError):
    """A candidate or recorded generation failed a bounded integrity check."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


def verify_public_projection(source_root: Path, output_root: Path) -> dict[str, int | str]:
    """Run the reusable bounded visibility-canary scan for public artifacts."""
    return _privacy_scan(source_root, output_root)


def prepare_generation_contract(
    generation_root: Path,
    *,
    source_root: Path,
    app_root: Path,
    frozen_root: Path,
    generation: str,
    repository: str,
    requested_ref: str,
    requested_commit: str | None,
    resolved_commit: str,
    image_digest: str,
    build_commit: str,
    actor: str,
    operation: Mapping[str, Any] | None,
    page_count: int,
    verified_at: str,
    max_files: int,
    max_bytes: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Validate, inventory, write, and round-trip verify one staged generation."""
    config_digest, presentation_digest = _validate_config(app_root)
    smoke = _representative_smoke(frozen_root, page_count=page_count)
    privacy = _privacy_scan(source_root, frozen_root)
    artifacts = _inventory(
        generation_root,
        (source_root, frozen_root),
        max_files=max_files * 5,
        max_bytes=max_bytes * 5,
    )
    freeze_fingerprint = _digest(
        [
            {"path": item["path"], "sha256": item["sha256"]}
            for item in artifacts
            if item["kind"] == "frozen"
        ]
    )
    renderer = read_renderer_fingerprint(frozen_root) or "unknown"
    manifest = {
        "schema_version": GENERATION_CONTRACT_VERSION,
        "record_type": "furatena.content-generation.manifest",
        "generation": generation,
        "source": {
            "repository": _normalize_repository(repository),
            "ref": requested_ref.strip(),
            "requested_commit": requested_commit,
            "resolved_commit": resolved_commit,
        },
        "config_digest": config_digest,
        "runtime": {"image_digest": image_digest, "build_commit": build_commit},
        "fingerprints": {
            "renderer": renderer,
            "presentation": presentation_digest,
            "freeze": freeze_fingerprint,
        },
        "artifacts": artifacts,
        "lifecycle_state": "verified",
        "actor": actor,
        "operation_receipt": dict(operation or {}),
        "verified_at": verified_at,
    }
    manifest_path = generation_root / "manifest.json"
    _write_json(manifest_path, manifest)
    verification = {
        "schema_version": GENERATION_CONTRACT_VERSION,
        "record_type": "furatena.content-generation.verification",
        "generation": generation,
        "status": "verified",
        "manifest_digest": _file_digest(manifest_path),
        "artifact_count": len(artifacts),
        "artifact_bytes": sum(int(item["bytes"]) for item in artifacts),
        "checks": {
            "content_config": "pass",
            "privacy_canaries": privacy,
            "freeze_completeness": "pass",
            "representative_surfaces": smoke,
            "artifact_integrity": "pass",
        },
        "actor": actor,
        "operation_receipt": dict(operation or {}),
        "verified_at": verified_at,
    }
    _write_json(generation_root / "verification.json", verification)
    verify_generation_contract(
        generation_root,
        full=True,
        allow_legacy=False,
        expected_generation=generation,
    )
    return manifest, verification


def verify_generation_contract(
    generation_root: Path,
    *,
    full: bool,
    allow_legacy: bool,
    image_digest: str | None = None,
    build_commit: str | None = None,
    expected_generation: str | None = None,
) -> dict[str, Any]:
    """Verify a generation contract without exposing filesystem paths."""
    manifest_path = generation_root / "manifest.json"
    verification_path = generation_root / "verification.json"
    manifest = _read_json(manifest_path)
    verification = _read_json(verification_path)
    if manifest is None or verification is None:
        if allow_legacy and _legacy_generation_valid(generation_root):
            return {
                "status": "legacy_v1",
                "lifecycle_state": "active",
                "generation": generation_root.name,
                "verified": False,
                "compatible": True,
            }
        raise GenerationContractError(
            code="generation_manifest_invalid",
            message="The generation integrity contract is missing or invalid.",
        )
    generation = expected_generation or generation_root.name
    if (
        manifest.get("schema_version") != GENERATION_CONTRACT_VERSION
        or manifest.get("record_type") != "furatena.content-generation.manifest"
        or verification.get("schema_version") != GENERATION_CONTRACT_VERSION
        or verification.get("record_type") != "furatena.content-generation.verification"
        or manifest.get("generation") != generation
        or verification.get("generation") != generation
        or manifest.get("lifecycle_state") != "verified"
        or verification.get("status") != "verified"
        or verification.get("manifest_digest") != _file_digest(manifest_path)
    ):
        raise GenerationContractError(
            code="generation_manifest_invalid",
            message="The generation manifest or verification receipt failed validation.",
        )
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        raise GenerationContractError(
            code="generation_manifest_invalid",
            message="The generation artifact inventory is incomplete.",
        )
    if int(verification.get("artifact_count") or -1) != len(artifacts):
        raise GenerationContractError(
            code="generation_manifest_invalid",
            message="The generation artifact count does not match.",
        )
    if not all(
        isinstance(item, Mapping) and isinstance(item.get("bytes"), int) for item in artifacts
    ):
        raise GenerationContractError(
            code="generation_manifest_invalid",
            message="The generation artifact inventory is invalid.",
        )
    artifact_bytes = sum(int(item["bytes"]) for item in artifacts)
    if (
        int(verification.get("artifact_bytes") or -1) != artifact_bytes
        or verification.get("actor") != manifest.get("actor")
        or verification.get("operation_receipt") != manifest.get("operation_receipt")
        or verification.get("verified_at") != manifest.get("verified_at")
    ):
        raise GenerationContractError(
            code="generation_manifest_invalid",
            message="The generation verification receipt does not match its manifest.",
        )
    if full:
        _verify_inventory(generation_root, artifacts)
    runtime = manifest.get("runtime")
    if not isinstance(runtime, Mapping):
        raise GenerationContractError(
            code="generation_manifest_invalid",
            message="The generation runtime identity is missing.",
        )
    compatible = True
    if image_digest and image_digest != "unknown":
        compatible = compatible and runtime.get("image_digest") == image_digest
    if build_commit and build_commit != "unknown":
        compatible = compatible and runtime.get("build_commit") == build_commit
    if not compatible:
        raise GenerationContractError(
            code="generation_runtime_incompatible",
            message="The generation was verified under a different image or build identity.",
        )
    return {
        "status": "verified",
        "lifecycle_state": str(manifest.get("lifecycle_state")),
        "generation": generation,
        "manifest_digest": str(verification.get("manifest_digest")),
        "artifact_count": len(artifacts),
        "verified": True,
        "compatible": True,
    }


def _validate_config(app_root: Path) -> tuple[str, str]:
    docs_config = app_root / "docs.yaml"
    try:
        config = yaml.safe_load(docs_config.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise GenerationContractError(
            "content_config_invalid", "The staged docs configuration is invalid."
        ) from exc
    if not isinstance(config, Mapping):
        raise GenerationContractError(
            "content_config_invalid", "The staged docs configuration must be an object."
        )
    records = []
    for path in sorted(app_root.rglob("*")):
        if path.is_file() and path.suffix.lower() in _CONFIG_SUFFIXES:
            records.append(
                {"path": path.relative_to(app_root).as_posix(), "sha256": _file_digest(path)}
            )
    presentation = {
        key: config.get(key) for key in ("presentation", "theme", "views") if key in config
    }
    return _digest(records), _digest(presentation)


def _representative_smoke(frozen_root: Path, *, page_count: int) -> dict[str, str]:
    values: dict[str, Any] = {}
    for name in _REQUIRED_FROZEN[:3]:
        try:
            values[name] = json.loads((frozen_root / name).read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise GenerationContractError(
                "representative_surface_failed",
                "A representative frozen JSON surface is missing or invalid.",
            ) from exc
    try:
        llms = (frozen_root / "llms-full.txt").read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError) as exc:
        raise GenerationContractError(
            "representative_surface_failed", "The representative agent surface is unavailable."
        ) from exc
    catalog = values["catalog.json"]
    pages = catalog.get("pages") if isinstance(catalog, Mapping) else catalog
    if page_count < 1 or not isinstance(pages, list) or not pages or not llms:
        raise GenerationContractError(
            "representative_surface_failed",
            "Representative reader, catalog, search, or agent content is empty.",
        )
    return {"reader": "pass", "search": "pass", "catalog": "pass", "agent": "pass"}


def _privacy_scan(source_root: Path, frozen_root: Path) -> dict[str, int | str]:
    records: list[tuple[Path, dict[str, Any], str]] = []
    public_text: list[str] = []
    for path in sorted(source_root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in {".md", ".mdx"} or ".git" in path.parts:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeError:
            continue
        meta, body = _frontmatter(text)
        records.append((path, meta, body))
        if is_public_meta(meta):
            public_text.append(text)
    public = "\n".join(public_text)
    canaries = []
    for path, meta, body in records:
        if is_public_meta(meta):
            continue
        candidates = [str(meta.get("title") or "")]
        candidates.extend(line.strip().strip("#").strip() for line in body.splitlines())
        tokens = tuple(
            dict.fromkeys(
                token for token in candidates if 12 <= len(token) <= 240 and token not in public
            )
        )
        if tokens:
            canaries.append(
                VisibilityCanary(
                    source_path=path.relative_to(source_root).as_posix(),
                    boundary=str(meta.get("visibility") or "protected"),
                    tokens=tokens,
                )
            )
    report = scan_visibility_leaks(frozen_root, canaries)
    if not report.ok:
        raise GenerationContractError(
            "privacy_canary_failed",
            "A protected-content canary appeared in a public generation artifact.",
        )
    return {"status": "pass", "canaries": len(canaries), "artifacts": report.scanned_artifacts}


def _frontmatter(text: str) -> tuple[dict[str, Any], str]:
    if not text.startswith("---\n"):
        return {}, text
    parts = text.split("\n---\n", 1)
    if len(parts) != 2:
        return {}, text
    try:
        raw = yaml.safe_load(parts[0][4:]) or {}
    except yaml.YAMLError:
        return {}, text
    return (dict(raw) if isinstance(raw, Mapping) else {}), parts[1]


def _inventory(
    generation_root: Path,
    roots: tuple[Path, Path],
    *,
    max_files: int,
    max_bytes: int,
) -> list[dict[str, Any]]:
    artifacts = []
    total = 0
    for root in roots:
        kind = root.name
        for path in sorted(root.rglob("*")):
            if ".git" in path.relative_to(root).parts:
                continue
            if path.is_symlink():
                raise GenerationContractError(
                    "artifact_integrity_failed", "Generation artifacts cannot be symbolic links."
                )
            if not path.is_file():
                continue
            size = path.stat().st_size
            total += size
            artifacts.append(
                {
                    "path": path.relative_to(generation_root).as_posix(),
                    "kind": kind,
                    "bytes": size,
                    "sha256": _file_digest(path),
                }
            )
            if len(artifacts) > max_files or total > max_bytes:
                raise GenerationContractError(
                    "artifact_inventory_limit",
                    "The complete generation artifact inventory exceeds configured bounds.",
                )
    if not artifacts:
        raise GenerationContractError(
            "artifact_integrity_failed", "The generation artifact inventory is empty."
        )
    return artifacts


def _verify_inventory(generation_root: Path, artifacts: list[Any]) -> None:
    seen: set[str] = set()
    total = 0
    for raw in artifacts:
        if not isinstance(raw, Mapping):
            raise GenerationContractError("artifact_integrity_failed", "Invalid artifact record.")
        relative = Path(str(raw.get("path") or ""))
        normalized = relative.as_posix()
        kind = str(raw.get("kind") or "")
        if (
            not normalized
            or relative.is_absolute()
            or ".." in relative.parts
            or normalized in seen
            or kind not in {"source", "frozen"}
            or relative.parts[0] != kind
        ):
            raise GenerationContractError("artifact_integrity_failed", "Unsafe artifact identity.")
        seen.add(normalized)
        path = generation_root / relative
        expected_bytes = raw.get("bytes")
        if (
            not isinstance(expected_bytes, int)
            or not path.is_file()
            or path.is_symlink()
            or path.stat().st_size != expected_bytes
            or _file_digest(path) != raw.get("sha256")
        ):
            raise GenerationContractError(
                "artifact_integrity_failed",
                "A generation artifact failed size or SHA-256 verification.",
            )
        total += path.stat().st_size
    actual: set[str] = set()
    for root_name in ("source", "frozen"):
        root = generation_root / root_name
        for path in sorted(root.rglob("*")):
            if ".git" in path.relative_to(root).parts:
                continue
            if path.is_symlink():
                raise GenerationContractError(
                    "artifact_integrity_failed",
                    "Generation artifacts cannot be symbolic links.",
                )
            if path.is_file():
                actual.add(path.relative_to(generation_root).as_posix())
    if actual != seen:
        raise GenerationContractError(
            "artifact_integrity_failed",
            "The generation artifact inventory does not match the complete artifact tree.",
        )
    if total < 1:
        raise GenerationContractError(
            "artifact_integrity_failed", "Generation artifacts are empty."
        )


def _legacy_generation_valid(root: Path) -> bool:
    receipt = _read_json(root / "receipt.json")
    return bool(
        receipt
        and receipt.get("generation_contract_version") is None
        and receipt.get("generation") in {None, root.name}
        and (root / "source").is_dir()
        and all((root / "frozen" / name).is_file() for name in _REQUIRED_FROZEN)
    )


def _normalize_repository(value: str) -> str:
    parsed = urlsplit(value.strip())
    return urlunsplit(
        (parsed.scheme.lower(), (parsed.hostname or "").lower(), parsed.path.rstrip("/"), "", "")
    )


def _digest(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except OSError, UnicodeError, json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    import uuid

    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            handle.write(json.dumps(dict(value), indent=2, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
