"""Versioned presentation-pack discovery, validation, and immutable resolution."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from functools import lru_cache
from importlib.metadata import entry_points
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, cast

from jsonschema import Draft202012Validator

from furatena import __version__
from furatena.catalog.application_roots import ApplicationRoots
from furatena.catalog.view_kinds import VIEW_KINDS

if TYPE_CHECKING:
    from furatena.catalog.config import DocsConfig

PRESENTATION_MANIFEST_SCHEMA_VERSION = 1
PRESENTATION_RENDER_CONTEXT_API_VERSION = "1"
PRESENTATION_ENTRY_POINT_GROUP = "furatena.presentation_packs"
PRESENTATION_MANIFEST_NAME = "presentation-pack.json"

PackKind = Literal["layout", "skin", "override"]
PackSource = Literal["local", "packaged", "entry-point", "compatibility"]

_SEMVER_RE = re.compile(
    r"^(?P<major>0|[1-9][0-9]*)\.(?P<minor>0|[1-9][0-9]*)\."
    r"(?P<patch>0|[1-9][0-9]*)(?:-[0-9A-Za-z.-]+)?$"
)
_RANGE_RE = re.compile(r"^(>=|>|<=|<|==)([0-9]+\.[0-9]+\.[0-9]+)$")
_REQUIRED_LAYOUT_MODES = frozenset({"full", "fragment"})
_REQUIRED_LAYOUT_SLOTS = frozenset({"head", "scripts", "shell", "content"})
_REQUIRED_LAYOUT_HOOKS = frozenset({"main", "page-root"})
_TRUST_CAPABILITIES = frozenset({"scripts", "route-hooks", "asset-hooks"})


class PresentationPackError(ValueError):
    """A presentation manifest or selection violates the public contract."""


@dataclass(frozen=True, slots=True)
class PresentationAsset:
    """One declared presentation asset or asset directory."""

    role: str
    path: str


@dataclass(frozen=True, slots=True)
class PresentationPack:
    """One validated immutable presentation pack."""

    id: str
    version: str
    kind: PackKind
    render_context_api_version: str
    runtime: str
    root: Path
    source: PackSource
    templates: tuple[tuple[str, str], ...]
    assets: tuple[PresentationAsset, ...]
    capabilities: frozenset[str]
    requires_trust: frozenset[str]
    content_digest: str
    deprecated: bool = False
    migration: str | None = None

    def template_roots(self) -> tuple[Path, ...]:
        shadows = self.root / "templates"
        return (shadows, self.root) if shadows.is_dir() else (self.root,)

    def asset_path(self, role: str) -> Path | None:
        asset = next((item for item in self.assets if item.role == role), None)
        return _safe_pack_path(self.root, asset.path, label=f"{self.id} asset") if asset else None

    def public_record(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "version": self.version,
            "type": self.kind,
            "source": self.source,
            "render_context_api_version": self.render_context_api_version,
            "runtime": self.runtime,
            "capabilities": sorted(self.capabilities),
            "requires_trust": sorted(self.requires_trust),
            "content_digest": self.content_digest,
            "deprecated": self.deprecated,
        }


@dataclass(frozen=True, slots=True)
class PresentationRecord:
    """Public, path-free identity for one completely resolved presentation."""

    layout: PresentationPack
    skin: PresentationPack | None
    overrides: tuple[PresentationPack, ...]
    capabilities: frozenset[str]
    trusted_capabilities: frozenset[str]
    content_digest: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": PRESENTATION_MANIFEST_SCHEMA_VERSION,
            "render_context_api_version": PRESENTATION_RENDER_CONTEXT_API_VERSION,
            "runtime_version": __version__,
            "layout": self.layout.public_record(),
            "skin": self.skin.public_record() if self.skin is not None else None,
            "overrides": [pack.public_record() for pack in self.overrides],
            "capabilities": sorted(self.capabilities),
            "trusted_capabilities": sorted(self.trusted_capabilities),
            "content_digest": self.content_digest,
        }


@dataclass(frozen=True, slots=True)
class ResolvedPresentation:
    """Immutable site-global presentation selection and its private pack roots."""

    record: PresentationRecord
    layout: PresentationPack
    skin: PresentationPack | None
    overrides: tuple[PresentationPack, ...]
    explicit_layout: bool

    @property
    def override_template_roots(self) -> tuple[Path, ...]:
        return tuple(
            dict.fromkeys(
                root for pack in self.overrides if pack.templates for root in pack.template_roots()
            )
        )

    @property
    def layout_template_roots(self) -> tuple[Path, ...]:
        return self.layout.template_roots() if self.explicit_layout else ()

    @property
    def template_roots(self) -> tuple[Path, ...]:
        roots = list(self.override_template_roots)
        roots.extend(self.layout_template_roots)
        return tuple(roots)


def load_presentation_manifest(path: Path, *, source: PackSource) -> PresentationPack:
    """Load and validate one strict v1 manifest without exposing its filesystem path."""
    manifest_path = path.expanduser()
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise PresentationPackError(
            f"The presentation pack manifest is missing or resolves through a symlink: {path}."
        )
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PresentationPackError(
            f"The presentation pack manifest contains invalid JSON and cannot be loaded: "
            f"{path.name}."
        ) from exc
    if not isinstance(payload, Mapping):
        raise PresentationPackError(
            "The presentation pack manifest must contain one top-level JSON object."
        )
    errors = sorted(_manifest_validator().iter_errors(payload), key=lambda item: list(item.path))
    if errors:
        error = errors[0]
        location = ".".join(str(part) for part in error.path) or "manifest"
        raise PresentationPackError(
            f"The presentation manifest {location} field failed validation: {error.message}."
        )

    root = manifest_path.parent.resolve()
    templates = tuple(
        sorted((str(name), str(relative)) for name, relative in payload["templates"].items())
    )
    assets = tuple(
        PresentationAsset(role=str(item["role"]), path=str(item["path"]))
        for item in payload["assets"]
    )
    pack = PresentationPack(
        id=str(payload["id"]),
        version=str(payload["version"]),
        kind=cast(PackKind, str(payload["type"])),
        render_context_api_version=str(payload["render_context_api_version"]),
        runtime=str(payload["runtime"]),
        root=root,
        source=source,
        templates=templates,
        assets=assets,
        capabilities=frozenset(str(value) for value in payload["capabilities"]),
        requires_trust=frozenset(str(value) for value in payload["requires_trust"]),
        content_digest=_tree_digest(root),
        deprecated=bool(payload.get("deprecated")),
        migration=str(payload.get("migration") or "") or None,
    )
    _validate_pack(pack, contract=payload.get("contract"))
    return pack


def discover_presentation_packs(
    docs: DocsConfig,
    *,
    roots: ApplicationRoots | None = None,
) -> tuple[PresentationPack, ...]:
    """Discover local, packaged, and preinstalled entry-point packs deterministically."""
    roots = roots or ApplicationRoots.from_environment(docs.root)
    candidates: list[PresentationPack] = []
    local_root = docs.root / "presentation"
    if local_root.is_dir():
        symlink = next((path for path in sorted(local_root.rglob("*")) if path.is_symlink()), None)
        if symlink is not None:
            raise PresentationPackError(
                "Repository-local presentation packs cannot contain any symbolic link entries."
            )
        for manifest in sorted(local_root.rglob(PRESENTATION_MANIFEST_NAME)):
            if roots.managed:
                roots.require_site_path(manifest, label="presentation manifest")
            _reject_symlink_components(manifest, boundary=docs.root)
            candidates.append(load_presentation_manifest(manifest, source="local"))

    packaged_root = Path(__file__).resolve().parents[1] / "themes"
    for manifest in sorted(packaged_root.glob(f"*/{PRESENTATION_MANIFEST_NAME}")):
        candidates.append(load_presentation_manifest(manifest, source="packaged"))

    for entry in _presentation_entry_points():
        loaded = entry.load()
        pack = _entry_point_pack(entry.name, loaded)
        candidates.append(pack)
    from furatena.catalog.theme_pack import _theme_entry_points

    for entry in sorted(_theme_entry_points(), key=lambda item: (item.name, item.value)):
        candidates.append(_entry_point_pack(entry.name, entry.load()))

    by_id: dict[str, PresentationPack] = {}
    for pack in sorted(candidates, key=lambda item: (item.id, item.source, item.content_digest)):
        previous = by_id.get(pack.id)
        if previous is not None:
            raise PresentationPackError(
                f"The duplicate presentation pack identity {pack.id!r} comes from "
                f"{previous.source} and {pack.source}; rename one pack."
            )
        by_id[pack.id] = pack
    return tuple(by_id[key] for key in sorted(by_id))


def resolve_presentation(
    docs: DocsConfig,
    *,
    roots: ApplicationRoots | None = None,
) -> ResolvedPresentation:
    """Resolve exactly one layout, optional skin, and ordered sparse overrides."""
    roots = roots or ApplicationRoots.from_environment(docs.root)
    config = docs.presentation
    unknown_trust = config.trusted_capabilities - _TRUST_CAPABILITIES
    if unknown_trust:
        raise PresentationPackError(
            "The presentation.trusted_capabilities setting contains unknown capability values: "
            + ", ".join(sorted(unknown_trust))
            + "."
        )
    registry = {pack.id: pack for pack in discover_presentation_packs(docs, roots=roots)}

    if config.layout:
        layout = _selected_pack(registry, config.layout, expected="layout")
        explicit_layout = True
    else:
        default_layout = registry.get("docs")
        if default_layout is not None and default_layout.kind == "layout":
            layout = default_layout
            explicit_layout = True
        else:
            layout = _compatibility_layout(docs, roots)
            explicit_layout = False

    if config.skin:
        skin = _selected_pack(registry, config.skin, expected="skin")
    elif not (config.layout or config.overrides) and docs.theme.use:
        skin = _legacy_skin(docs.theme.use)
    else:
        skin = None

    overrides = tuple(
        _selected_pack(registry, identity, expected="override") for identity in config.overrides
    )
    if len({pack.id for pack in overrides}) != len(overrides):
        raise PresentationPackError(
            "The presentation.overrides setting must not repeat any presentation pack identity."
        )

    layers = (layout, *((skin,) if skin is not None else ()), *overrides)
    required_trust = frozenset().union(*(pack.requires_trust for pack in layers))
    missing_trust = required_trust - config.trusted_capabilities
    strict_missing = {
        capability
        for capability in missing_trust
        if any(
            pack.source != "compatibility" and capability in pack.requires_trust for pack in layers
        )
    }
    if strict_missing:
        names = ", ".join(sorted(strict_missing))
        raise PresentationPackError(
            "Presentation packs require explicit trust for capabilities "
            f"{names}; add them to presentation.trusted_capabilities after review."
        )

    capabilities = frozenset().union(*(pack.capabilities for pack in layers))
    digest = hashlib.sha256(
        "\n".join(
            f"{pack.kind}:{pack.id}:{pack.version}:{pack.content_digest}" for pack in layers
        ).encode("utf-8")
    ).hexdigest()
    trusted = frozenset(config.trusted_capabilities) | frozenset(
        capability
        for pack in layers
        if pack.source == "compatibility"
        for capability in pack.requires_trust
    )
    record = PresentationRecord(
        layout=layout,
        skin=skin,
        overrides=overrides,
        capabilities=capabilities,
        trusted_capabilities=trusted,
        content_digest=digest,
    )
    return ResolvedPresentation(
        record=record,
        layout=layout,
        skin=skin,
        overrides=overrides,
        explicit_layout=explicit_layout,
    )


def _selected_pack(
    registry: Mapping[str, PresentationPack], identity: str, *, expected: PackKind
) -> PresentationPack:
    pack = registry.get(identity)
    if pack is None:
        available = ", ".join(sorted(registry)) or "(none)"
        raise PresentationPackError(
            f"unknown presentation {expected} {identity!r}; available packs: {available}"
        )
    if pack.kind != expected:
        raise PresentationPackError(
            f"presentation {identity!r} is type {pack.kind!r}, expected {expected!r}"
        )
    return pack


def _validate_pack(pack: PresentationPack, *, contract: object) -> None:
    if pack.render_context_api_version != PRESENTATION_RENDER_CONTEXT_API_VERSION:
        raise PresentationPackError(
            f"presentation pack {pack.id!r} requires render-context API "
            f"{pack.render_context_api_version}, runtime provides "
            f"{PRESENTATION_RENDER_CONTEXT_API_VERSION}"
        )
    if not _runtime_matches(__version__, pack.runtime):
        raise PresentationPackError(
            f"presentation pack {pack.id!r} requires Furatena {pack.runtime}; "
            f"runtime is {__version__}"
        )
    template_names = {name for name, _path in pack.templates}
    required_views = {spec.kind for spec in VIEW_KINDS}
    if pack.kind == "layout" and template_names != required_views:
        missing = ", ".join(sorted(required_views - template_names)) or "none"
        extra = ", ".join(sorted(template_names - required_views)) or "none"
        raise PresentationPackError(
            f"complete layout {pack.id!r} must own every VIEW_KINDS template "
            f"(missing: {missing}; unknown: {extra})"
        )
    if pack.kind == "skin" and pack.templates:
        raise PresentationPackError(
            f"skin {pack.id!r} cannot own view templates; use a layout or sparse override"
        )
    if pack.templates and "templates" not in pack.capabilities:
        raise PresentationPackError(
            f"presentation pack {pack.id!r} declares templates without capability 'templates'"
        )
    asset_roles = [asset.role for asset in pack.assets]
    if len(set(asset_roles)) != len(asset_roles):
        raise PresentationPackError(f"presentation pack {pack.id!r} repeats an asset role")
    for name, relative in pack.templates:
        path = _safe_pack_path(pack.root, relative, label=f"{pack.id} template {name}")
        if not path.is_file():
            raise PresentationPackError(f"presentation template is missing: {pack.id}:{name}")
    for asset in pack.assets:
        path = _safe_pack_path(pack.root, asset.path, label=f"{pack.id} {asset.role}")
        if not path.exists():
            raise PresentationPackError(f"presentation asset is missing: {pack.id}:{asset.role}")
    role_capability = {
        "tokens": "styles",
        "styles": "styles",
        "directives": "styles",
        "scripts": "scripts",
        "fonts": "fonts",
        "branding": "branding",
    }
    for role in asset_roles:
        capability = role_capability[role]
        if capability not in pack.capabilities:
            raise PresentationPackError(
                f"presentation pack {pack.id!r} asset role {role!r} requires "
                f"capability {capability!r}"
            )
    if "scripts" in asset_roles and (
        "scripts" not in pack.capabilities or "scripts" not in pack.requires_trust
    ):
        raise PresentationPackError(
            f"presentation pack {pack.id!r} scripts require capability and trust 'scripts'"
        )
    script_roots = tuple(
        _safe_pack_path(pack.root, asset.path, label=f"{pack.id} scripts")
        for asset in pack.assets
        if asset.role == "scripts"
    )
    undeclared_scripts = [
        path
        for path in sorted(pack.root.rglob("*.js"))
        if not any(path == root or path.is_relative_to(root) for root in script_roots)
    ]
    if undeclared_scripts:
        raise PresentationPackError(
            f"presentation pack {pack.id!r} contains undeclared scripts; "
            "declare the scripts asset role and explicit trust"
        )
    undeclared_trust = pack.requires_trust - pack.capabilities
    if undeclared_trust:
        raise PresentationPackError(
            f"presentation pack {pack.id!r} requires undeclared capabilities: "
            + ", ".join(sorted(undeclared_trust))
        )
    if pack.requires_trust - _TRUST_CAPABILITIES:
        raise PresentationPackError(f"presentation pack {pack.id!r} has invalid trust requirements")
    if pack.kind == "skin":
        required_assets = {"tokens", "styles", "directives", "scripts"}
        missing_assets = required_assets - set(asset_roles)
        if missing_assets:
            raise PresentationPackError(
                f"skin {pack.id!r} is missing required asset roles: "
                + ", ".join(sorted(missing_assets))
            )
    if pack.kind == "layout":
        if not isinstance(contract, Mapping):
            raise PresentationPackError(f"complete layout {pack.id!r} must declare contract")
        modes = _string_set(contract.get("render_modes"))
        slots = _string_set(contract.get("slots"))
        hooks = _string_set(contract.get("semantic_hooks"))
        if not modes >= _REQUIRED_LAYOUT_MODES:
            raise PresentationPackError(
                f"complete layout {pack.id!r} must support full and fragment"
            )
        if not slots >= _REQUIRED_LAYOUT_SLOTS:
            raise PresentationPackError(
                f"complete layout {pack.id!r} is missing required named slots"
            )
        if not hooks >= _REQUIRED_LAYOUT_HOOKS:
            raise PresentationPackError(
                f"complete layout {pack.id!r} is missing main/page-root semantic hooks"
            )


def _compatibility_layout(docs: DocsConfig, roots: ApplicationRoots) -> PresentationPack:
    theme_roots = tuple(
        path
        for path in dict.fromkeys((docs.theme_dir.resolve(), (roots.platform / "theme").resolve()))
        if path.is_dir()
    )
    digest = hashlib.sha256(
        "\n".join(_tree_digest(path) for path in theme_roots).encode("utf-8")
    ).hexdigest()
    return PresentationPack(
        id=f"{docs.theme.id}-compat-layout",
        version=__version__,
        kind="layout",
        render_context_api_version=PRESENTATION_RENDER_CONTEXT_API_VERSION,
        runtime=f"=={__version__}",
        root=theme_roots[0] if theme_roots else docs.theme_dir.resolve(),
        source="compatibility",
        templates=tuple((spec.kind, spec.default_template) for spec in VIEW_KINDS),
        assets=(),
        capabilities=frozenset({"templates"}),
        requires_trust=frozenset(),
        content_digest=digest,
        deprecated=True,
        migration="Set presentation.layout to a v1 complete-layout pack.",
    )


def _legacy_skin(name: str) -> PresentationPack:
    from furatena.catalog.theme_pack import load_theme_pack

    legacy = load_theme_pack(name)
    assets = (
        PresentationAsset("tokens", legacy.tokens),
        PresentationAsset("styles", legacy.styles),
        PresentationAsset("directives", legacy.directives),
        PresentationAsset("scripts", legacy.js_dir),
        PresentationAsset("fonts", legacy.fonts_dir),
    )
    return PresentationPack(
        id=legacy.name,
        version="0.0.0",
        kind="skin",
        render_context_api_version=PRESENTATION_RENDER_CONTEXT_API_VERSION,
        runtime="*",
        root=legacy.root.resolve(),
        source="compatibility",
        templates=(),
        assets=assets,
        capabilities=frozenset({"styles", "scripts", "fonts"}),
        requires_trust=frozenset({"scripts"}),
        content_digest=_tree_digest(legacy.root),
        deprecated=True,
        migration=f"Replace theme.use: {name} with presentation.skin: {name}.",
    )


def _entry_point_pack(name: str, loaded: object) -> PresentationPack:
    from furatena.catalog.theme_pack import ThemePack

    if isinstance(loaded, PresentationPack):
        return loaded
    if isinstance(loaded, ThemePack):
        assets = (
            PresentationAsset("tokens", loaded.tokens),
            PresentationAsset("styles", loaded.styles),
            PresentationAsset("directives", loaded.directives),
            PresentationAsset("scripts", loaded.js_dir),
            PresentationAsset("fonts", loaded.fonts_dir),
        )
        return PresentationPack(
            id=loaded.name or name,
            version="0.0.0",
            kind="skin",
            render_context_api_version=PRESENTATION_RENDER_CONTEXT_API_VERSION,
            runtime="*",
            root=loaded.root.resolve(),
            source="entry-point",
            templates=(),
            assets=assets,
            capabilities=frozenset({"styles", "scripts", "fonts"}),
            requires_trust=frozenset({"scripts"}),
            content_digest=_tree_digest(loaded.root),
            deprecated=True,
            migration="Publish a presentation-pack.json manifest from this entry point.",
        )
    root = Path(str(loaded)).expanduser()
    manifest = (
        root if root.name == PRESENTATION_MANIFEST_NAME else root / PRESENTATION_MANIFEST_NAME
    )
    return load_presentation_manifest(manifest, source="entry-point")


def _presentation_entry_points() -> tuple[Any, ...]:
    selected = entry_points(group=PRESENTATION_ENTRY_POINT_GROUP)
    return tuple(sorted(selected, key=lambda entry: (entry.name, entry.value)))


@lru_cache(maxsize=1)
def _manifest_validator() -> Any:
    schema_path = Path(__file__).resolve().parent / "schemas" / "presentation-pack-v1.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    return Draft202012Validator(schema)


def _safe_pack_path(root: Path, relative: str, *, label: str) -> Path:
    raw = Path(relative)
    if raw.is_absolute() or ".." in raw.parts or not raw.parts:
        raise PresentationPackError(f"{label} must use a safe relative path")
    current = root.resolve()
    for part in raw.parts:
        current = current / part
        if current.is_symlink():
            raise PresentationPackError(f"{label} cannot traverse a symbolic link")
    resolved = (root / raw).resolve()
    if not resolved.is_relative_to(root.resolve()):
        raise PresentationPackError(f"{label} escapes its presentation pack")
    return resolved


def _reject_symlink_components(path: Path, *, boundary: Path) -> None:
    try:
        relative = path.relative_to(boundary)
    except ValueError as exc:
        raise PresentationPackError("local presentation manifest escapes the site root") from exc
    current = boundary
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise PresentationPackError("local presentation manifests cannot traverse symlinks")


def _runtime_matches(version: str, expression: str) -> bool:
    if expression == "*":
        return True
    current = _semver(version)
    for clause in expression.split(","):
        match = _RANGE_RE.fullmatch(clause)
        if match is None:
            return False
        operator, expected_raw = match.groups()
        expected = _semver(expected_raw)
        if operator == ">=" and not current >= expected:
            return False
        if operator == ">" and not current > expected:
            return False
        if operator == "<=" and not current <= expected:
            return False
        if operator == "<" and not current < expected:
            return False
        if operator == "==" and current != expected:
            return False
    return True


def _semver(value: str) -> tuple[int, int, int]:
    match = _SEMVER_RE.fullmatch(value)
    if match is None:
        raise PresentationPackError(f"invalid semantic version: {value!r}")
    return (
        int(match.group("major")),
        int(match.group("minor")),
        int(match.group("patch")),
    )


def _string_set(value: object) -> frozenset[str]:
    if not isinstance(value, list | tuple | set | frozenset):
        return frozenset()
    return frozenset(str(item) for item in value)


def _tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    resolved = root.resolve()
    if not resolved.is_dir():
        return digest.hexdigest()
    for path in sorted(resolved.rglob("*")):
        if not path.is_file() or path.is_symlink() or "__pycache__" in path.parts:
            continue
        relative = path.relative_to(resolved).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def presentation_content_digest(packs: Iterable[PresentationPack]) -> str:
    """Return a deterministic digest for an already-resolved immutable pack tuple."""
    return hashlib.sha256(
        "\n".join(pack.content_digest for pack in packs).encode("utf-8")
    ).hexdigest()
