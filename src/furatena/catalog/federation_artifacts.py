"""Executable contracts for published shards and federation hub manifests."""

from __future__ import annotations

import gzip
import hashlib
import io
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

from furatena.catalog.dcp_validate import validate_catalog_payload
from furatena.catalog.paths import schemas_root

PUBLISHED_SHARD_SCHEMA_VERSION = 1
FEDERATION_HUB_SCHEMA_VERSION = 1
SUPPORTED_ARTIFACT_VERSIONS = (1,)
SUPPORTED_REMOTE_DCP_VERSIONS = (2, 3)
SUPPORTED_REMOTE_CONTENT_IR_VERSIONS = (3,)
MAX_PUBLISHED_OBJECT_BYTES = 64 * 1024 * 1024
MAX_PUBLISHED_INVENTORY_ENTRIES = 100_000


def load_published_shard_schema() -> dict[str, Any]:
    return _load_schema("federation/v1/published-shard.schema.json")


def load_federation_hub_schema() -> dict[str, Any]:
    return _load_schema("federation/v1/hub-manifest.schema.json")


def load_federation_discovery_schema() -> dict[str, Any]:
    return _load_schema("federation/v1/channels-extension.schema.json")


def published_manifest_digest(payload: Mapping[str, Any]) -> str:
    """Return the canonical JSON digest used by the hub to anchor a shard manifest."""
    return _digest_json(payload)


def published_artifact_fingerprint(payload: Mapping[str, Any]) -> str:
    """Bind published identity and every inventoried object to one immutable address."""
    return _digest_json(
        {
            "identity": payload.get("identity"),
            "contracts": payload.get("contracts"),
            "source_shard_fingerprint": payload.get("source_shard_fingerprint"),
            "inventory_sha256": (payload.get("integrity") or {}).get("inventory_sha256"),
        }
    )


def hub_payload_digest(payload: Mapping[str, Any]) -> str:
    """Digest the non-circular signed portion of a hub manifest."""
    return _digest_json(
        {
            "schema_version": payload.get("schema_version"),
            "manifest_type": payload.get("manifest_type"),
            "generated_at": payload.get("generated_at"),
            "contracts": payload.get("contracts"),
            "shards": payload.get("shards"),
            "channels": payload.get("channels"),
            "discovery": payload.get("discovery"),
        }
    )


def federation_hub_manifest_digest(payload: Mapping[str, Any]) -> str:
    """Digest the complete canonical hub manifest for channels discovery."""
    return _digest_json(payload)


def inventory_digest(inventory: list[dict[str, Any]]) -> str:
    """Digest the complete ordered object inventory."""
    return _digest_json(inventory)


def validate_published_shard_manifest(
    payload: dict[str, Any],
    *,
    artifact_root: Path | None = None,
) -> list[str]:
    """Validate schema, identity, inventory, and optionally every local object."""
    errors = _schema_errors(payload, load_published_shard_schema())
    if errors:
        return errors

    identity = payload["identity"]
    identity_key = f"{identity['mount']}:{identity['edition']}"
    if identity["key"] != identity_key:
        errors.append(f"identity.key: expected {identity_key!r}")
    fingerprint = payload["fingerprint"]
    if fingerprint != published_artifact_fingerprint(payload):
        errors.append("fingerprint: does not bind the source shard and complete inventory")
    expected_base = f"/sha256/{fingerprint}/"
    if not urlsplit(payload["artifact_base_url"]).path.endswith(expected_base):
        errors.append("artifact_base_url: must end with the immutable /sha256/<fingerprint>/ path")
    errors.extend(_url_errors("artifact_base_url", payload["artifact_base_url"]))
    errors.extend(_url_errors("provenance.repository", payload["provenance"]["repository"]))
    if _unsafe_relative_path(payload["provenance"]["source_path"]):
        errors.append("provenance.source_path: must be a safe relative POSIX path")

    contracts = payload["contracts"]
    if contracts["dcp"] not in SUPPORTED_REMOTE_DCP_VERSIONS:
        errors.append("contracts.dcp: only the proven DCP v2/v3 reader window is supported")
    if contracts["content_ir"] not in SUPPORTED_REMOTE_CONTENT_IR_VERSIONS:
        errors.append("contracts.content_ir: only Content IR v3 is proven for remote shards")

    inventory = payload["inventory"]
    logical_paths = [item["logical_path"] for item in inventory]
    object_urls = [item["object_url"] for item in inventory]
    if logical_paths != sorted(logical_paths):
        errors.append("inventory: entries must be sorted by logical_path")
    if len(set(logical_paths)) != len(logical_paths):
        errors.append("inventory: logical_path values must be unique")
    if len(set(object_urls)) != len(object_urls):
        errors.append("inventory: object_url values must be unique")
    roles = [item["role"] for item in inventory]
    for required in ("catalog", "search", "semantic"):
        if roles.count(required) != 1:
            errors.append(f"inventory: exactly one {required!r} object is required")
    if "fragment" not in roles:
        errors.append("inventory: at least one fragment object is required")
    if len(inventory) > payload["layout"]["max_inventory_entries"]:
        errors.append("inventory: exceeds layout.max_inventory_entries")
    encoding_suffixes = {"identity": ".json", "gzip": ".json.gz", "zstd": ".json.zst"}
    for item in inventory:
        if _unsafe_relative_path(item["logical_path"]):
            errors.append(f"inventory.{item['logical_path']}: unsafe logical_path")
        expected_url = (
            f"objects/sha256/{item['encoded_sha256']}{encoding_suffixes[item['content_encoding']]}"
        )
        if item["object_url"] != expected_url:
            errors.append(
                f"inventory.{item['logical_path']}: object_url must encode the exact "
                "encoded_sha256 and content_encoding"
            )
        if item["content_encoding"] not in payload["compression"]["allowed"]:
            errors.append(
                f"inventory.{item['logical_path']}: content_encoding is not declared allowed"
            )
        if item["uncompressed_size"] > payload["layout"]["max_object_bytes"]:
            errors.append(f"inventory.{item['logical_path']}: exceeds max_object_bytes")
        if (
            item["role"] == "fragment"
            and item["uncompressed_size"] > payload["layout"]["max_fragment_bytes"]
        ):
            errors.append(f"inventory.{item['logical_path']}: exceeds max_fragment_bytes")
        if (
            item["uncompressed_size"] >= payload["compression"]["threshold_bytes"]
            and item["content_encoding"] == "identity"
        ):
            errors.append(
                f"inventory.{item['logical_path']}: identity encoding exceeds compression threshold"
            )

    totals = payload["totals"]
    expected_totals = {
        "object_count": len(inventory),
        "fragment_count": roles.count("fragment"),
        "uncompressed_bytes": sum(item["uncompressed_size"] for item in inventory),
        "encoded_bytes": sum(item["encoded_size"] for item in inventory),
    }
    for key, expected in expected_totals.items():
        if totals[key] != expected:
            errors.append(f"totals.{key}: expected {expected}, got {totals[key]}")
    actual_inventory_digest = inventory_digest(inventory)
    if payload["integrity"]["inventory_sha256"] != actual_inventory_digest:
        errors.append("integrity.inventory_sha256: does not match the canonical inventory")

    for record in (*payload["signatures"], *payload["attestations"]):
        if record["subject_sha256"] != fingerprint:
            errors.append("signature/attestation subject_sha256 must equal the shard fingerprint")
        errors.extend(_url_errors("signature/attestation.url", record["url"]))
    for signature in payload["signatures"]:
        errors.extend(_url_errors("signatures.issuer", signature["issuer"]))
    for attestation in payload["attestations"]:
        errors.extend(_url_errors("attestations.predicate_type", attestation["predicate_type"]))

    expected_class = "moving" if payload["lifecycle"]["status"] == "current" else "release"
    if payload["retention"]["class"] != expected_class:
        errors.append(f"retention.class: expected {expected_class!r}")
    if payload["compression"]["allowed"] != sorted(payload["compression"]["allowed"]):
        errors.append("compression.allowed: values must be sorted for deterministic output")
    if payload["retention"]["pinned_by"] != sorted(payload["retention"]["pinned_by"]):
        errors.append("retention.pinned_by: values must be sorted for deterministic output")
    signature_order = [(item["kind"], item["url"]) for item in payload["signatures"]]
    if signature_order != sorted(signature_order):
        errors.append("signatures: entries must be sorted for deterministic output")
    attestation_order = [item["url"] for item in payload["attestations"]]
    if attestation_order != sorted(attestation_order):
        errors.append("attestations: entries must be sorted for deterministic output")

    if artifact_root is not None:
        errors.extend(_validate_local_objects(payload, artifact_root))
    return errors


def validate_federation_hub_manifest(
    payload: dict[str, Any],
    *,
    published_manifests: Mapping[str, dict[str, Any]] | None = None,
) -> list[str]:
    """Validate the O(1) shard map, channel projection, and optional manifest pairs."""
    errors = _schema_errors(payload, load_federation_hub_schema())
    if errors:
        return errors

    if payload["integrity"]["payload_sha256"] != hub_payload_digest(payload):
        errors.append("integrity.payload_sha256: does not match the canonical hub payload")
    for signature in payload["signatures"]:
        if signature["subject_sha256"] != payload["integrity"]["payload_sha256"]:
            errors.append("signatures: subject_sha256 must equal integrity.payload_sha256")
        errors.extend(_url_errors("signatures.url", signature["url"]))
        errors.extend(_url_errors("signatures.issuer", signature["issuer"]))
    errors.extend(
        _url_errors("discovery.hub_manifest_url", payload["discovery"]["hub_manifest_url"])
    )

    shards = payload["shards"]
    if list(shards) != sorted(shards):
        errors.append("shards: identity keys must be sorted for deterministic output")
    for key, shard in shards.items():
        expected_key = f"{shard['mount']}:{shard['edition']}"
        if key != expected_key:
            errors.append(f"shards.{key}: key must equal {expected_key!r}")
        expected_artifact_path = f"/sha256/{shard['fingerprint']}/manifest.json"
        if not urlsplit(shard["artifact_url"]).path.endswith(expected_artifact_path):
            errors.append(f"shards.{key}.artifact_url: fingerprint path is not content-addressed")
        errors.extend(_url_errors(f"shards.{key}.artifact_url", shard["artifact_url"]))
        errors.extend(
            _url_errors(f"shards.{key}.provenance.repository", shard["provenance"]["repository"])
        )
        if _unsafe_relative_path(shard["provenance"]["source_path"]):
            errors.append(f"shards.{key}.provenance.source_path: unsafe relative path")
        if shard["contracts"]["dcp"] not in SUPPORTED_REMOTE_DCP_VERSIONS:
            errors.append(f"shards.{key}.contracts.dcp: unsupported remote DCP version")
        if shard["contracts"]["content_ir"] not in SUPPORTED_REMOTE_CONTENT_IR_VERSIONS:
            errors.append(f"shards.{key}.contracts.content_ir: unsupported Content IR version")
        expected_class = "moving" if shard["lifecycle"]["status"] == "current" else "release"
        if shard["retention"]["class"] != expected_class:
            errors.append(f"shards.{key}.retention.class: expected {expected_class!r}")

    if list(payload["channels"]) != sorted(payload["channels"]):
        errors.append("channels: mount keys must be sorted for deterministic output")
    hub_signature_order = [(item["kind"], item["url"]) for item in payload["signatures"]]
    if hub_signature_order != sorted(hub_signature_order):
        errors.append("signatures: entries must be sorted for deterministic output")
    for mount, channel in payload["channels"].items():
        identities = channel["editions"]
        if channel["latest"] not in identities:
            errors.append(f"channels.{mount}.latest: must appear in editions")
        if channel["stable"] not in identities:
            errors.append(f"channels.{mount}.stable: must appear in editions")
        if len(set(identities)) != len(identities):
            errors.append(f"channels.{mount}.editions: identities must be unique")
        for identity in identities:
            shard = shards.get(identity)
            if shard is None:
                errors.append(f"channels.{mount}.editions: unknown shard {identity!r}")
            elif shard["mount"] != mount:
                errors.append(f"channels.{mount}.editions: shard {identity!r} crosses mounts")
        latest = shards.get(channel["latest"])
        if latest is not None and latest["lifecycle"]["status"] != "current":
            errors.append(f"channels.{mount}.latest: shard must have current lifecycle status")
        stable = shards.get(channel["stable"])
        if stable is not None and stable["lifecycle"]["status"] in {"preview", "eol"}:
            errors.append(f"channels.{mount}.stable: preview/eol shards cannot be stable")

    if published_manifests is not None:
        for key, manifest in published_manifests.items():
            shard = shards.get(key)
            if shard is None:
                errors.append(f"shards.{key}: published manifest is not referenced by the hub")
                continue
            manifest_errors = validate_published_shard_manifest(manifest)
            errors.extend(f"shards.{key}.manifest: {item}" for item in manifest_errors)
            if manifest.get("identity", {}).get("key") != key:
                errors.append(f"shards.{key}: published manifest identity mismatch")
            if manifest.get("fingerprint") != shard["fingerprint"]:
                errors.append(f"shards.{key}: published manifest fingerprint mismatch")
            if manifest.get("source_shard_fingerprint") != shard["source_shard_fingerprint"]:
                errors.append(f"shards.{key}: source shard fingerprint mismatch")
            if manifest.get("audience") != shard["audience"]:
                errors.append(f"shards.{key}: audience mismatch")
            expected_contracts = {
                name: manifest.get("contracts", {}).get(name)
                for name in ("artifact", "dcp", "content_ir")
            }
            if expected_contracts != shard["contracts"]:
                errors.append(f"shards.{key}: contract versions mismatch")
            expected_provenance = {
                name: manifest.get("provenance", {}).get(name)
                for name in ("provider", "repository", "ref", "resolved_ref", "source_path")
            }
            if expected_provenance != shard["provenance"]:
                errors.append(f"shards.{key}: provenance mismatch")
            if published_manifest_digest(manifest) != shard["integrity"]["manifest_sha256"]:
                errors.append(f"shards.{key}: published manifest digest mismatch")
    return errors


def validate_federation_discovery(payload: dict[str, Any]) -> list[str]:
    """Validate the optional ``channels.json`` remote federation extension."""
    errors = _schema_errors(payload, load_federation_discovery_schema())
    if errors:
        return errors
    errors.extend(_url_errors("hub_manifest_url", payload["hub_manifest_url"]))
    return errors


def _validate_local_objects(payload: dict[str, Any], artifact_root: Path) -> list[str]:
    errors: list[str] = []
    inventory = payload["inventory"]
    expected_urls = {item["object_url"] for item in inventory}
    objects_root = artifact_root / "objects"
    actual_urls = {
        path.relative_to(artifact_root).as_posix()
        for path in objects_root.rglob("*")
        if path.is_file()
    }
    if actual_urls != expected_urls:
        missing = sorted(expected_urls - actual_urls)
        extra = sorted(actual_urls - expected_urls)
        errors.append(f"inventory: object set mismatch (missing={missing}, extra={extra})")
        return errors

    decoded_values: dict[str, dict[str, Any]] = {}
    for item in inventory:
        path = (artifact_root / item["object_url"]).resolve()
        if not path.is_relative_to(artifact_root.resolve()):
            errors.append(f"inventory.{item['logical_path']}: object path escapes artifact root")
            continue
        encoded_size = path.stat().st_size
        if encoded_size > payload["layout"]["max_object_bytes"]:
            errors.append(
                f"inventory.{item['logical_path']}: encoded object exceeds max_object_bytes "
                "before read"
            )
            continue
        if encoded_size > item["encoded_size"]:
            errors.append(
                f"inventory.{item['logical_path']}: encoded object exceeds declared encoded_size "
                "before read"
            )
            continue
        max_bytes = payload["layout"]["max_object_bytes"]
        with path.open("rb") as stream:
            raw = stream.read(max_bytes + 1)
        if len(raw) > max_bytes:
            errors.append(
                f"inventory.{item['logical_path']}: encoded object exceeds max_object_bytes "
                "during bounded read"
            )
            continue
        if len(raw) != item["encoded_size"]:
            errors.append(f"inventory.{item['logical_path']}: encoded_size mismatch")
        if _digest_bytes(raw) != item["encoded_sha256"]:
            errors.append(f"inventory.{item['logical_path']}: encoded_sha256 mismatch")
        try:
            decoded = _decode_object(
                raw,
                item["content_encoding"],
                max_bytes=max_bytes,
            )
        except (OSError, ValueError) as exc:
            errors.append(f"inventory.{item['logical_path']}: decode failed: {exc}")
            continue
        if len(decoded) != item["uncompressed_size"]:
            errors.append(f"inventory.{item['logical_path']}: uncompressed_size mismatch")
        if _digest_bytes(decoded) != item["sha256"]:
            errors.append(f"inventory.{item['logical_path']}: sha256 mismatch")
        errors.extend(_validate_role_payload(payload, item, decoded))
        try:
            value = json.loads(decoded)
        except UnicodeDecodeError, json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            decoded_values[item["logical_path"]] = value
    errors.extend(_validate_object_set_closure(inventory, decoded_values))
    return errors


def _validate_role_payload(
    manifest: dict[str, Any], item: dict[str, Any], decoded: bytes
) -> list[str]:
    try:
        value = json.loads(decoded)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return [f"inventory.{item['logical_path']}: invalid JSON: {exc}"]
    if not isinstance(value, dict):
        return [f"inventory.{item['logical_path']}: expected a JSON object"]
    identity = manifest["identity"]
    errors: list[str] = []
    if value.get("mount") != identity["mount"]:
        errors.append(f"inventory.{item['logical_path']}: mount identity mismatch")
    if value.get("edition") != identity["edition"]:
        errors.append(f"inventory.{item['logical_path']}: edition identity mismatch")
    if item["role"] == "catalog":
        if value.get("schema_version") != manifest["contracts"]["dcp"]:
            errors.append(f"inventory.{item['logical_path']}: DCP contract version mismatch")
        errors.extend(
            f"inventory.{item['logical_path']}: DCP {error}"
            for error in validate_catalog_payload(value)
        )
        for page in value.get("pages", []):
            if isinstance(page, dict):
                errors.extend(_validate_public_node(identity, item["logical_path"], page))
        for namespace in value.get("namespaces", []):
            if isinstance(namespace, dict) and (
                namespace.get("mount") != identity["mount"]
                or namespace.get("edition") != identity["edition"]
            ):
                errors.append(f"inventory.{item['logical_path']}: namespace identity mismatch")
        return errors
    if item["role"] == "fragment" and not str(value.get("node_id") or "").startswith(
        f"{identity['mount']}:{identity['edition']}:"
    ):
        errors.append(f"inventory.{item['logical_path']}: node identity mismatch")
    if item["role"] == "fragment":
        fragment_catalog = {
            "schema_version": manifest["contracts"]["dcp"],
            "channel": identity["edition"],
            "edition": identity["edition"],
            "mount": identity["mount"],
            "page_count": 1,
            "pages": [value],
            "edges": [],
            "namespaces": [],
        }
        errors.extend(
            f"inventory.{item['logical_path']}: DCP fragment {error}"
            for error in validate_catalog_payload(fragment_catalog)
        )
        errors.extend(_validate_public_node(identity, item["logical_path"], value))
    if item["role"] in {"search", "semantic"}:
        records_key = "documents" if item["role"] == "search" else "records"
        for record in value.get(records_key, []):
            if not isinstance(record, dict) or not str(record.get("node_id") or "").startswith(
                f"{identity['mount']}:{identity['edition']}:"
            ):
                errors.append(f"inventory.{item['logical_path']}: node identity mismatch")
    return errors


def _validate_public_node(
    identity: dict[str, Any], logical_path: str, value: dict[str, Any]
) -> list[str]:
    errors: list[str] = []
    prefix = f"{identity['mount']}:{identity['edition']}:"
    if value.get("mount") != identity["mount"] or value.get("edition") != identity["edition"]:
        errors.append(f"inventory.{logical_path}: page identity mismatch")
    if not str(value.get("node_id") or "").startswith(prefix):
        errors.append(f"inventory.{logical_path}: node identity mismatch")
    visibility = value.get("visibility")
    if visibility not in (None, "public"):
        errors.append(f"inventory.{logical_path}: non-public visibility is forbidden")
    if value.get("draft") is True or value.get("archived_at") is not None or value.get("access"):
        errors.append(
            f"inventory.{logical_path}: restricted lifecycle/access metadata is forbidden"
        )
    source_path = value.get("source_path")
    if isinstance(source_path, str) and _unsafe_relative_path(source_path):
        errors.append(f"inventory.{logical_path}: unsafe source_path")
    url = value.get("url")
    if isinstance(url, str) and _unsafe_page_url(url):
        errors.append(f"inventory.{logical_path}: unsafe page URL")
    return errors


def _validate_object_set_closure(
    inventory: list[dict[str, Any]], decoded_values: Mapping[str, dict[str, Any]]
) -> list[str]:
    role_values: dict[str, list[dict[str, Any]]] = {}
    for item in inventory:
        value = decoded_values.get(item["logical_path"])
        if value is not None:
            role_values.setdefault(item["role"], []).append(value)
    catalog_values = role_values.get("catalog", [])
    if len(catalog_values) != 1:
        return []
    catalog_ids = {
        str(page.get("node_id"))
        for page in catalog_values[0].get("pages", [])
        if isinstance(page, dict) and page.get("node_id")
    }
    sets = {
        "fragment": {
            str(value.get("node_id"))
            for value in role_values.get("fragment", [])
            if value.get("node_id")
        },
        "search": {
            str(record.get("node_id"))
            for value in role_values.get("search", [])
            for record in value.get("documents", [])
            if isinstance(record, dict) and record.get("node_id")
        },
        "semantic": {
            str(record.get("node_id"))
            for value in role_values.get("semantic", [])
            for record in value.get("records", [])
            if isinstance(record, dict) and record.get("node_id")
        },
    }
    return [
        f"inventory: {role} node set does not match the public catalog"
        for role, node_ids in sets.items()
        if node_ids != catalog_ids
    ]


def _decode_object(raw: bytes, encoding: str, *, max_bytes: int) -> bytes:
    if encoding == "identity":
        decoded = raw
    elif encoding == "gzip":
        with gzip.GzipFile(fileobj=io.BytesIO(raw)) as stream:
            decoded = stream.read(max_bytes + 1)
    elif encoding == "zstd":
        from compression import zstd

        with zstd.open(io.BytesIO(raw), "rb") as stream:
            decoded = stream.read(max_bytes + 1)
    else:  # pragma: no cover - rejected by the schema
        raise ValueError(f"unsupported content encoding {encoding!r}")
    if len(decoded) > max_bytes:
        raise ValueError(f"decoded object exceeds {max_bytes} bytes")
    return decoded


def _schema_errors(payload: dict[str, Any], schema: dict[str, Any]) -> list[str]:
    try:
        import jsonschema
    except ImportError:
        return ["jsonschema is required for federation contract validation"]
    validator = jsonschema.Draft202012Validator(schema)
    return [
        f"{'.'.join(str(part) for part in error.path) or 'root'}: {error.message}"
        for error in sorted(validator.iter_errors(payload), key=lambda item: list(item.path))
    ]


def _url_errors(label: str, value: str) -> list[str]:
    parsed = urlsplit(value)
    errors: list[str] = []
    if parsed.scheme != "https" or not parsed.hostname:
        errors.append(f"{label}: must be an absolute HTTPS URL")
    if parsed.username is not None or parsed.password is not None:
        errors.append(f"{label}: embedded credentials are forbidden")
    if parsed.query or parsed.fragment:
        errors.append(f"{label}: query strings and fragments are forbidden in immutable URLs")
    if _unsafe_url_path(parsed.path):
        errors.append(f"{label}: URL path traversal is forbidden")
    return errors


def _unsafe_relative_path(value: str) -> bool:
    return (
        not value
        or value.startswith("/")
        or "\\" in value
        or any(part in {"", ".", ".."} for part in value.split("/"))
    )


def _unsafe_url_path(value: str) -> bool:
    decoded = unquote(value)
    return "\\" in decoded or any(part in {".", ".."} for part in decoded.split("/"))


def _unsafe_page_url(value: str) -> bool:
    parsed = urlsplit(value)
    return (
        not parsed.path.startswith("/")
        or bool(parsed.scheme or parsed.netloc or parsed.query or parsed.fragment)
        or _unsafe_url_path(parsed.path)
    )


def _load_schema(relative: str) -> dict[str, Any]:
    path = schemas_root() / relative
    if not path.is_file():
        raise FileNotFoundError(f"federation schema missing: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _digest_json(value: Any) -> str:
    return _digest_bytes(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    )


def _digest_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()
