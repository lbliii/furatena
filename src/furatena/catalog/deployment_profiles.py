"""Deployment profile manifest for product packaging and automation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class DeploymentProfile:
    """One supported deployment shape for Furatena."""

    id: str
    label: str
    audience: str
    summary: str
    services: tuple[str, ...]
    storage: str
    auth: str
    sync: str
    publishing: str
    happy_path: tuple[str, ...]
    tradeoffs: tuple[str, ...]
    agent_modes: tuple[str, ...]
    default: bool = False

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "audience": self.audience,
            "summary": self.summary,
            "default": self.default,
            "requirements": {
                "services": list(self.services),
                "storage": self.storage,
                "auth": self.auth,
                "sync": self.sync,
                "publishing": self.publishing,
            },
            "happy_path": list(self.happy_path),
            "tradeoffs": list(self.tradeoffs),
            "agent_modes": list(self.agent_modes),
        }


DEPLOYMENT_PROFILES: tuple[DeploymentProfile, ...] = (
    DeploymentProfile(
        id="local-author",
        label="Local authoring",
        audience="Solo authors, early product teams, and repo-local docs work.",
        summary="Run the live Chirp app from one checkout with no cloud dependency.",
        default=True,
        services=("Python runtime", "local filesystem", "browser"),
        storage="Repository checkout plus app/frozen for optional preview snapshots.",
        auth="Local process only; author tools are not exposed beyond localhost by default.",
        sync="Filesystem watcher and author invalidation rebuild only changed catalog regions.",
        publishing="None required; use freeze/export when a public artifact is needed.",
        happy_path=(
            "uv sync",
            "uv run fura serve",
            "edit content/ and use author chrome or author studio for validation",
        ),
        tradeoffs=(
            "Fastest feedback loop and safest default.",
            "Not a public deployment until a static or live profile is added.",
        ),
        agent_modes=("local_cli", "local_mcp"),
    ),
    DeploymentProfile(
        id="static-pages",
        label="Static publishing",
        audience="Public docs on GitHub Pages, CDNs, or immutable object storage.",
        summary="Freeze the catalog and export HTML plus machine-readable sidecars.",
        services=("Python build runner", "static host or CDN"),
        storage="app/frozen for portable IR and app/public for deployable output.",
        auth="Public read-only output; protected or draft content is excluded from export.",
        sync="CI or local build runs fura freeze and fura export from source control.",
        publishing="Upload app/public, including channels.json and deployment-profiles.json.",
        happy_path=(
            "export FURA_BASE_URL=https://example.com/docs",
            "uv run fura freeze",
            "uv run fura export",
        ),
        tradeoffs=(
            "Lowest operational burden and best cacheability.",
            "No live authoring, server-side search mutation, or protected author tools.",
        ),
        agent_modes=("static_only",),
    ),
    DeploymentProfile(
        id="cloud-live",
        label="Cloud live app",
        audience="Teams that need live search, previews, and app-hosted MCP surfaces.",
        summary="Run the Chirp app as a service while keeping source in git-backed mounts.",
        services=("Python app service", "reverse proxy", "git or filesystem source mount"),
        storage="Mounted content source plus optional frozen cache for fast startup.",
        auth="Put the app behind the platform's identity layer before exposing author routes.",
        sync="Pull or mount source updates, then let live indexing refresh the catalog graph.",
        publishing="Serve live routes directly; optionally export static/PDF artifacts per release.",
        happy_path=(
            "deploy the app with FURA_BASE_URL set",
            "run uv run fura serve behind the platform process manager",
            "use /channels.json and /deployment-profiles.json for release automation",
        ),
        tradeoffs=(
            "Enables dynamic app and MCP use cases.",
            "Requires service health, access control, and source-sync operations.",
        ),
        agent_modes=("deployed_app_mcp",),
    ),
    DeploymentProfile(
        id="self-hosted-enterprise",
        label="Self-hosted enterprise",
        audience="Multi-team docs platforms with private content and governance requirements.",
        summary="Operate Furatena with explicit tenant/site boundaries and privileged author MCP.",
        services=("Python app service", "identity provider", "source sync", "observability"),
        storage="Tenant-scoped source mounts, frozen/public artifacts, and audit/report outputs.",
        auth="External SSO or gateway auth plus privileged tokens for sensitive author MCP tools.",
        sync="Controlled source providers publish mount health, drift, and stale-impact reports.",
        publishing="Promote reviewed static, PDF, agent, and live channels per tenant or site.",
        happy_path=(
            "configure mounts with tenant/site identity",
            "run fura check --agent --json in CI",
            "run fura mcp --author --include-private only in trusted local or protected sessions",
        ),
        tradeoffs=(
            "Best fit for governance, private content, and multi-team scale.",
            "Highest operational responsibility: identity, audit review, sync repair, and rollout policy.",
        ),
        agent_modes=("self_hosted_enterprise_mcp", "local_cli"),
    ),
)


def deployment_profiles_manifest(*, base_url: str = "") -> dict[str, Any]:
    """Return the stable deployment profile manifest."""
    origin = base_url.rstrip("/")
    return {
        "schema_version": 1,
        "default_profile": "local-author",
        "profiles": [profile.to_json() for profile in DEPLOYMENT_PROFILES],
        "agent_modes": {
            "local_cli": "Local fura CLI commands over the repository checkout.",
            "local_mcp": "Local Milo MCP session for trusted authoring and retrieval.",
            "deployed_app_mcp": "App-hosted MCP/read surfaces behind deployment auth.",
            "self_hosted_enterprise_mcp": "Privileged enterprise MCP with tenant/site policy.",
            "static_only": "Read-only files such as catalog.json, llms.txt, and channels.json.",
        },
        "links": {
            "self": f"{origin}/deployment-profiles.json" if origin else "/deployment-profiles.json",
            "docs": (
                f"{origin}/develop/deployment-profiles/"
                if origin
                else "/develop/deployment-profiles/"
            ),
            "channels": f"{origin}/channels.json" if origin else "/channels.json",
        },
    }
