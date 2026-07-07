"""Machine-readable inventory of public surfaces and their documentation coverage."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
from dataclasses import MISSING, asdict, dataclass, fields, is_dataclass
from pathlib import Path
from typing import Any, get_args, get_origin, get_type_hints

from furatena.catalog.config import DocsConfig
from furatena.catalog.deployment_profiles import DEPLOYMENT_PROFILES
from furatena.catalog.mcp import FuraMCPServer
from furatena.catalog.registry import MountConfig
from furatena.catalog.route_manifest import route_manifest_entries


@dataclass(frozen=True, slots=True)
class PublicSurface:
    kind: str
    name: str
    implementation: str
    aliases: tuple[str, ...]
    contract: dict[str, object]

    @property
    def id(self) -> str:
        return f"{self.kind}:{self.name}"


def _jsonable(value: object) -> object:
    if isinstance(value, Path):
        if value.is_absolute():
            try:
                return value.relative_to(Path.cwd()).as_posix()
            except ValueError:
                return value.as_posix()
        return value.as_posix()
    if isinstance(value, str) and Path(value).is_absolute():
        try:
            return Path(value).relative_to(Path.cwd()).as_posix()
        except ValueError:
            return value
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    return str(value)


def _fingerprint(value: object) -> str:
    payload = json.dumps(_jsonable(value), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def _parser_contract(parser: argparse.ArgumentParser) -> dict[str, object]:
    options: list[dict[str, object]] = []
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction) or action.dest == "help":
            continue
        options.append(
            {
                "dest": action.dest,
                "options": list(action.option_strings),
                "required": bool(action.required),
                "default": _jsonable(action.default),
                "choices": _jsonable(action.choices),
                "nargs": _jsonable(action.nargs),
                "type": getattr(action.type, "__name__", str(action.type or "")),
                "help": action.help or "",
            }
        )
    return {"description": parser.description or "", "options": options}


def _cli_surfaces() -> list[PublicSurface]:
    from furatena.cli.main import _build_parser

    surfaces: list[PublicSurface] = []

    def walk(parser: argparse.ArgumentParser, prefix: tuple[str, ...] = ()) -> None:
        for action in parser._actions:
            if not isinstance(action, argparse._SubParsersAction):
                continue
            seen: set[int] = set()
            for name, child in sorted(action.choices.items()):
                if id(child) in seen:
                    continue
                seen.add(id(child))
                path = (*prefix, name)
                command = " ".join(path)
                surfaces.append(
                    PublicSurface(
                        kind="cli_command",
                        name=command,
                        implementation=f"furatena.cli:fura {command}",
                        aliases=(f"fura {command}",),
                        contract=_parser_contract(child),
                    )
                )
                walk(child, path)

    walk(_build_parser())
    return surfaces


def _route_surfaces(docs_app: Any) -> tuple[list[PublicSurface], list[PublicSurface]]:
    routes: list[PublicSurface] = []
    sidecars: list[PublicSurface] = []
    for entry in route_manifest_entries(docs_app.create_app(), catalog=docs_app.catalog):
        contract = entry.to_dict()
        methods = ",".join(entry.methods)
        name = f"{methods} {entry.path}"
        routes.append(
            PublicSurface(
                kind="route",
                name=name,
                implementation=entry.handler_origin,
                aliases=tuple(
                    alias for alias in (entry.path, entry.route_name or "", entry.handler) if alias
                ),
                contract=contract,
            )
        )
        if "GET" in entry.methods and entry.response_contract in {
            "binary",
            "event-stream",
            "json",
            "text",
            "xml",
        }:
            sidecars.append(
                PublicSurface(
                    kind="sidecar",
                    name=entry.path,
                    implementation=entry.handler_origin,
                    aliases=(entry.path, Path(entry.path).name),
                    contract={
                        "response_contract": entry.response_contract,
                        "route_name": entry.route_name,
                    },
                )
            )
    return routes, sidecars


def _config_name(field_name: str) -> str:
    return field_name.removesuffix("_path")


def _config_surfaces() -> list[PublicSurface]:
    surfaces: list[PublicSurface] = []

    def nested_types(annotation: object) -> tuple[tuple[type[object], bool], ...]:
        if isinstance(annotation, type) and is_dataclass(annotation):
            return ((annotation, False),)
        origin = get_origin(annotation)
        is_collection = origin in {dict, list, set, tuple, frozenset}
        nested: list[tuple[type[object], bool]] = []
        for argument in get_args(annotation):
            for nested_type, child_collection in nested_types(argument):
                nested.append((nested_type, is_collection or child_collection))
        return tuple(nested)

    def walk(config_type: type[object], prefix: tuple[str, ...] = ()) -> None:
        hints = get_type_hints(config_type)
        for item in fields(config_type):
            if not prefix and item.name == "root":
                continue
            name = _config_name(item.name)
            path = (*prefix, name)
            dotted = ".".join(path)
            required = item.default is MISSING and item.default_factory is MISSING
            if item.default is not MISSING:
                default = _jsonable(item.default)
            elif item.default_factory is not MISSING:
                try:
                    default = _jsonable(item.default_factory())
                except TypeError:
                    default = None
            else:
                default = None
            surfaces.append(
                PublicSurface(
                    kind="config_field",
                    name=dotted,
                    implementation=(
                        f"{config_type.__module__}:{config_type.__name__}.{item.name}"
                    ),
                    aliases=(dotted,),
                    contract={
                        "type": str(item.type),
                        "default": default,
                        "required": required,
                    },
                )
            )
            for nested_type, collection in nested_types(hints.get(item.name, item.type)):
                nested_prefix = (*prefix, f"{name}[]" if collection else name)
                walk(nested_type, nested_prefix)

    walk(DocsConfig)
    walk(MountConfig, ("mounts[]",))
    return surfaces


def _mcp_surfaces(docs_app: Any) -> list[PublicSurface]:
    server = FuraMCPServer(docs_app)
    surfaces = [
        PublicSurface(
            kind="mcp_tool",
            name=str(tool["name"]),
            implementation="furatena.catalog.mcp:FuraMCPServer.list_tools",
            aliases=(str(tool["name"]), f"MCP tool {tool['name']}"),
            contract={str(key): _jsonable(value) for key, value in tool.items()},
        )
        for tool in server.list_tools()
    ]
    for resource in server.list_resources():
        uri = str(resource["uri"])
        if uri.startswith("fura://catalog/nodes/"):
            continue
        surfaces.append(
            PublicSurface(
                kind="mcp_resource",
                name=uri,
                implementation="furatena.catalog.mcp:FuraMCPServer.list_resources",
                aliases=(uri, str(resource.get("name") or "")),
                contract={str(key): _jsonable(value) for key, value in resource.items()},
            )
        )
    return surfaces


def _diagnostic_value(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        value = node.value
        return value if value.startswith(("fura.", "chirp.")) and not value.endswith(".") else None
    if isinstance(node, ast.JoinedStr):
        pieces = [part.value if isinstance(part, ast.Constant) else "*" for part in node.values]
        value = "".join(str(piece) for piece in pieces)
        return value if value.startswith(("fura.", "chirp.")) else None
    return None


def _diagnostic_surfaces(package_root: Path) -> list[PublicSurface]:
    found: dict[str, set[str]] = {}
    for path in package_root.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            value = _diagnostic_value(node)
            if value is None:
                continue
            source = path.relative_to(package_root.parent).as_posix()
            found.setdefault(value, set()).add(f"{source}:{getattr(node, 'lineno', 1)}")
    return [
        PublicSurface(
            kind="diagnostic",
            name=name,
            implementation=",".join(sorted(sources)),
            aliases=(name,),
            contract={"rule_id": name},
        )
        for name, sources in sorted(found.items())
    ]


def _deployment_surfaces() -> list[PublicSurface]:
    return [
        PublicSurface(
            kind="deployment_profile",
            name=profile.id,
            implementation="furatena.catalog.deployment_profiles:DEPLOYMENT_PROFILES",
            aliases=(profile.id, profile.label),
            contract=asdict(profile),
        )
        for profile in DEPLOYMENT_PROFILES
    ]


def collect_public_surfaces(docs_app: Any) -> tuple[PublicSurface, ...]:
    """Collect stable public contracts from their implementation metadata."""

    routes, sidecars = _route_surfaces(docs_app)
    package_root = Path(__file__).resolve().parents[1]
    surfaces = [
        *_cli_surfaces(),
        *routes,
        *_config_surfaces(),
        *_mcp_surfaces(docs_app),
        *sidecars,
        *_diagnostic_surfaces(package_root),
        *_deployment_surfaces(),
    ]
    by_id = {surface.id: surface for surface in surfaces}
    return tuple(by_id[key] for key in sorted(by_id))


def _documentation_files(roots: tuple[Path, ...]) -> dict[str, str]:
    documents: dict[str, str] = {}
    common = Path.cwd().resolve()
    for root in roots:
        root = root.resolve()
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            if path.suffix.lower() not in {".html", ".md", ".rst"} or not path.is_file():
                continue
            try:
                label = path.resolve().relative_to(common).as_posix()
            except ValueError:
                label = path.as_posix()
            documents[label] = path.read_text(encoding="utf-8").lower()
    return documents


def build_documentation_inventory(
    docs_app: Any,
    *,
    documentation_roots: tuple[Path, ...],
    previous: dict[str, object] | None = None,
) -> dict[str, object]:
    """Link public surfaces to docs and identify missing or stale coverage."""

    documents = _documentation_files(documentation_roots)
    previous_items = {
        str(item["id"]): item
        for item in (previous or {}).get("surfaces", [])
        if isinstance(item, dict) and item.get("id")
    }
    records: list[dict[str, object]] = []
    for surface in collect_public_surfaces(docs_app):
        aliases = tuple(alias.lower() for alias in surface.aliases if len(alias.strip()) >= 3)
        matched = sorted(
            path for path, text in documents.items() if any(alias in text for alias in aliases)
        )
        documentation_fingerprint = _fingerprint(
            {path: documents[path] for path in matched}
        )
        implementation_fingerprint = _fingerprint(surface.contract)
        prior = previous_items.get(surface.id)
        stale = bool(
            matched
            and prior
            and prior.get("implementation_fingerprint") != implementation_fingerprint
            and prior.get("documentation_fingerprint") == documentation_fingerprint
        )
        coverage = "stale" if stale else ("documented" if matched else "missing")
        records.append(
            {
                "id": surface.id,
                "kind": surface.kind,
                "name": surface.name,
                "implementation": surface.implementation,
                "implementation_fingerprint": implementation_fingerprint,
                "documentation": matched,
                "documentation_fingerprint": documentation_fingerprint,
                "coverage": coverage,
            }
        )

    kinds: dict[str, dict[str, int]] = {}
    for record in records:
        counts = kinds.setdefault(str(record["kind"]), {"total": 0, "documented": 0, "missing": 0, "stale": 0})
        counts["total"] += 1
        counts[str(record["coverage"])] += 1
    return {
        "schema_version": 1,
        "surface_count": len(records),
        "documentation_file_count": len(documents),
        "summary": {
            "documented": sum(record["coverage"] == "documented" for record in records),
            "missing": sum(record["coverage"] == "missing" for record in records),
            "stale": sum(record["coverage"] == "stale" for record in records),
            "by_kind": kinds,
        },
        "missing": [record["id"] for record in records if record["coverage"] == "missing"],
        "stale": [record["id"] for record in records if record["coverage"] == "stale"],
        "surfaces": records,
    }
