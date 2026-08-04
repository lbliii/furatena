"""Versioned MCP Apps metadata and gateway URI helpers."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlsplit

MCP_APPS_EXTENSION_ID = "io.modelcontextprotocol/ui"
MCP_APPS_SPEC_VERSION = "2026-01-26"
MCP_APPS_CONTRACT_VERSION = 1
MCP_APP_MIME_TYPE = "text/html;profile=mcp-app"
FURATENA_APP_META_KEY = "io.furatena/mcp-app"

_AUTHORITY_RE = re.compile(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?\Z")
_SEGMENT_RE = re.compile(r"[a-z0-9](?:[a-z0-9._~-]*[a-z0-9])?\Z")


class MCPAppContractError(ValueError):
    """Raised when MCP App identity or gateway metadata is not contract-safe."""


def client_supports_mcp_apps(params: dict[str, Any]) -> bool:
    """Return whether initialize params negotiate Furatena's MCP Apps MIME type."""
    capabilities = params.get("capabilities")
    if not isinstance(capabilities, dict):
        return False
    extensions = capabilities.get("extensions")
    if not isinstance(extensions, dict):
        return False
    extension = extensions.get(MCP_APPS_EXTENSION_ID)
    if not isinstance(extension, dict):
        return False
    mime_types = extension.get("mimeTypes")
    return isinstance(mime_types, list) and MCP_APP_MIME_TYPE in mime_types


def negotiated_server_extensions(params: dict[str, Any]) -> dict[str, dict[str, list[str]]]:
    """Return the server extension capability only after client negotiation."""
    if not client_supports_mcp_apps(params):
        return {}
    return {MCP_APPS_EXTENSION_ID: {"mimeTypes": [MCP_APP_MIME_TYPE]}}


def validate_canonical_app_uri(
    uri: str,
    *,
    contract_version: int = MCP_APPS_CONTRACT_VERSION,
) -> None:
    """Validate a canonical, versioned ``ui://`` resource identifier."""
    parsed = urlsplit(uri)
    if parsed.scheme != "ui" or not parsed.netloc:
        raise MCPAppContractError("MCP App URI must use ui:// with a non-empty authority.")
    try:
        port = parsed.port
    except ValueError as exc:
        raise MCPAppContractError("MCP App URI contains an invalid or ambiguous port.") from exc
    if parsed.username or parsed.password or port or parsed.query or parsed.fragment:
        raise MCPAppContractError(
            "MCP App URI must not contain credentials, a port, query parameters, or a fragment."
        )
    if not _AUTHORITY_RE.fullmatch(parsed.netloc):
        raise MCPAppContractError("MCP App URI authority is not a stable lowercase namespace.")
    segments = parsed.path.removeprefix("/").split("/")
    if len(segments) < 2 or any(not _SEGMENT_RE.fullmatch(segment) for segment in segments):
        raise MCPAppContractError(
            "MCP App URI path must contain an app id and version using stable URI segments."
        )
    if segments[-1] != f"v{contract_version}":
        raise MCPAppContractError(
            f"MCP App URI must end in the contract version segment v{contract_version}."
        )


def rewrite_mcp_app_uri(
    canonical_uri: str,
    *,
    gateway_authority: str,
    namespace: str,
) -> str:
    """Namespace a canonical App URI without discarding its upstream authority."""
    validate_canonical_app_uri(canonical_uri)
    if not _AUTHORITY_RE.fullmatch(gateway_authority):
        raise MCPAppContractError("gateway authority is not a stable lowercase namespace.")
    if not _SEGMENT_RE.fullmatch(namespace):
        raise MCPAppContractError("gateway namespace must be one stable URI segment.")
    parsed = urlsplit(canonical_uri)
    return f"ui://{gateway_authority}/{namespace}/{parsed.netloc}{parsed.path}"
