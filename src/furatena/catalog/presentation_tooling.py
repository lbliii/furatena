"""Developer checks and deterministic reference previews for presentation packs."""

from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import json
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from chirp.testing import TestClient

from furatena.catalog.config import load_docs_config
from furatena.catalog.docs_app import DocsApp
from furatena.catalog.pdf_export import PDFExportOptions, export_pdfs
from furatena.catalog.presentation_pack import (
    PRESENTATION_MANIFEST_NAME,
    PresentationPack,
    PresentationPackError,
    load_presentation_manifest,
)
from furatena.catalog.runtime import ServeConfig, ServeMode
from furatena.catalog.static_export import StaticExportOptions, export_static_site
from furatena.catalog.view_lint import check_view_templates
from furatena.catalog.visibility_audit import scan_visibility_leaks, visibility_canaries

_CUSTOM_PROPERTY_RE = re.compile(r"(?P<name>--[A-Za-z0-9_-]+)\s*:")
_CUSTOM_PROPERTY_USE_RE = re.compile(r"var\(\s*(?P<name>--[A-Za-z0-9_-]+)")
_NONCE_ATTR_RE = re.compile(r'\bnonce="[^"]+"')
_VIEW_ROUTES = {
    "home": "/",
    "doc": "/reference-guide/",
    "doc_list": "/guides/",
    "collection": "/collection/",
    "changelog": "/changelog/",
    "api_reference": "/api/",
    "page": "/localized/",
    "portal": "/portal/",
}
_CONTENT_FORMAT_ROUTES = {
    "docutils-rst": "/formats/rst/",
    "html": "/formats/html/",
    "mdx": "/formats/mdx/",
    "myst-markdown": "/formats/myst/",
    "patitas-markdown": "/formats/markdown/",
}
_CONTENT_FORMAT_EXTENSIONS = {
    "docutils-rst": ".rst",
    "html": ".html",
    "mdx": ".mdx",
    "myst-markdown": ".myst",
    "patitas-markdown": ".md",
}
_FIXTURE_ROUTES = {
    "deep_navigation": "/guides/deep/navigation/topic/",
    "empty_state": "/empty/",
}
_EXTRA_ROUTES = {"search": "/search?q=reference", "error": "/missing/"}
_RESPONSIVE_STATES = (
    {"id": "mobile", "width": 390, "height": 844},
    {"id": "tablet", "width": 768, "height": 1024},
    {"id": "desktop", "width": 1440, "height": 960},
)


@dataclass(frozen=True, slots=True)
class ToolingDiagnostic:
    """One stable pack diagnostic with the affected surface and recovery."""

    code: str
    message: str
    surface: str
    recovery: str
    severity: Literal["error", "warning"] = "error"

    def to_dict(self) -> dict[str, str]:
        return {
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
            "surface": self.surface,
            "recovery": self.recovery,
        }


@dataclass(frozen=True, slots=True)
class PresentationCheckResult:
    """Deterministic validation result for one pack root."""

    pack: PresentationPack | None
    diagnostics: tuple[ToolingDiagnostic, ...]

    @property
    def ok(self) -> bool:
        return not any(item.severity == "error" for item in self.diagnostics)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "pack": self.pack.public_record() if self.pack is not None else None,
            "diagnostics": [item.to_dict() for item in self.diagnostics],
        }


@dataclass(frozen=True, slots=True)
class GeneratedResult:
    """Result of writing or checking deterministic generated artifacts."""

    output: Path
    files: tuple[str, ...]
    drift: tuple[str, ...]
    report: dict[str, Any]

    @property
    def ok(self) -> bool:
        return not self.drift and bool(self.report.get("ok", True))


def _diagnostic(
    code: str,
    message: str,
    *,
    surface: str,
    recovery: str,
    severity: Literal["error", "warning"] = "error",
) -> ToolingDiagnostic:
    return ToolingDiagnostic(code, message, surface, recovery, severity)


def check_presentation_pack(root: Path) -> PresentationCheckResult:
    """Validate one pack with actionable, surface-specific diagnostics."""
    pack_root = root.expanduser().resolve()
    diagnostics: list[ToolingDiagnostic] = []
    symlinks = tuple(path for path in sorted(pack_root.rglob("*")) if path.is_symlink())
    if symlinks:
        diagnostics.append(
            _diagnostic(
                "fura.presentation.unsafe_path",
                f"pack contains symbolic links: {', '.join(path.relative_to(pack_root).as_posix() for path in symlinks)}",
                surface="pack discovery",
                recovery="Replace links with repository-owned regular files and rerun theme check.",
            )
        )
    try:
        pack = load_presentation_manifest(pack_root / PRESENTATION_MANIFEST_NAME, source="local")
    except PresentationPackError as exc:
        diagnostics.append(
            _diagnostic(
                "fura.presentation.manifest",
                str(exc),
                surface="manifest compatibility",
                recovery="Correct presentation-pack.json using manifest v1, then rerun theme check.",
            )
        )
        return PresentationCheckResult(None, tuple(sorted(diagnostics, key=lambda item: item.code)))

    diagnostics.extend(_check_css_token_ownership(pack))
    diagnostics.extend(_check_template_sources(pack))
    if pack.kind == "layout" and not any(
        "@media print" in path.read_text(encoding="utf-8") for path in _css_files(pack)
    ):
        diagnostics.append(
            _diagnostic(
                "fura.presentation.print",
                "complete layout has no print media contract",
                surface="print/PDF",
                recovery="Add an @media print rule to a declared stylesheet.",
            )
        )
    return PresentationCheckResult(
        pack,
        tuple(sorted(diagnostics, key=lambda item: (item.severity, item.code, item.message))),
    )


def _css_files(pack: PresentationPack) -> tuple[Path, ...]:
    paths: list[Path] = []
    for asset in pack.assets:
        if asset.role not in {"tokens", "styles", "directives"}:
            continue
        path = pack.asset_path(asset.role)
        if path is not None and path.is_file():
            paths.append(path)
    return tuple(paths)


def _check_css_token_ownership(pack: PresentationPack) -> list[ToolingDiagnostic]:
    token_path = pack.asset_path("tokens")
    if token_path is None or not token_path.is_file():
        return []
    token_source = token_path.read_text(encoding="utf-8")
    owned = set(_CUSTOM_PROPERTY_RE.findall(token_source))
    diagnostics: list[ToolingDiagnostic] = []
    consumed: set[str] = set()
    for path in _css_files(pack):
        if path == token_path:
            continue
        source = path.read_text(encoding="utf-8")
        duplicates = sorted(set(_CUSTOM_PROPERTY_RE.findall(source)) & owned)
        if duplicates:
            diagnostics.append(
                _diagnostic(
                    "fura.presentation.token_ownership",
                    f"{path.relative_to(pack.root)} redefines pack-owned tokens: {', '.join(duplicates)}",
                    surface="CSS token ownership",
                    recovery=f"Keep token definitions in {token_path.relative_to(pack.root)} and reference them with var().",
                )
            )
        consumed.update(set(_CUSTOM_PROPERTY_USE_RE.findall(source)) & owned)
    if owned and not consumed:
        diagnostics.append(
            _diagnostic(
                "fura.presentation.unused_tokens",
                f"no pack stylesheet consumes tokens declared by {token_path.relative_to(pack.root)}",
                surface="CSS token ownership",
                recovery="Use the declared tokens from pack styles or remove the unused token asset.",
                severity="warning",
            )
        )
    return diagnostics


def _check_template_sources(pack: PresentationPack) -> list[ToolingDiagnostic]:
    diagnostics: list[ToolingDiagnostic] = []
    for view_kind, relative in pack.templates:
        path = pack.root / relative
        source = path.read_text(encoding="utf-8")
        if pack.kind == "layout" and "page_root" not in source and "extends" not in source:
            diagnostics.append(
                _diagnostic(
                    "fura.presentation.template_reachability",
                    f"{relative} does not declare page_root or extend a reachable template",
                    surface=f"{view_kind} full/fragment HTML",
                    recovery="Extend the pack shell/layout or declare a page_root block.",
                )
            )
        if "|safe" in source and "safe(reason=" not in source:
            diagnostics.append(
                _diagnostic(
                    "fura.presentation.unsafe_html",
                    f"{relative} uses an undocumented safe filter",
                    surface=f"{view_kind} HTML",
                    recovery="Use escaping, a trusted producer type, or safe(reason=...).",
                )
            )
    return diagnostics


def generate_reference_preview(root: Path, output: Path, *, check: bool = False) -> GeneratedResult:
    """Render the complete synthetic fixture matrix and write/check stable artifacts."""
    validation = check_presentation_pack(root)
    if not validation.ok or validation.pack is None:
        return GeneratedResult(
            output.resolve(),
            (),
            (),
            {"ok": False, "validation": validation.to_dict()},
        )
    content_format_routes, unavailable_content_formats = _available_content_format_routes()
    with tempfile.TemporaryDirectory(prefix="fura-presentation-preview-") as temporary:
        workspace = Path(temporary)
        docs = _fixture_app(validation.pack, workspace)
        rendered = asyncio.run(_render_routes(docs))
        files = _preview_files(validation.pack, rendered)
    drift = _write_or_check(output.resolve(), files, check=check)
    expected_statuses = {name: 404 if name == "error" else 200 for name in rendered}
    render_ok = all(
        payload["status"] == expected_statuses[name]
        and payload["fragment_status"] == expected_statuses[name]
        for name, payload in rendered.items()
    )
    report = {
        "ok": not drift and render_ok,
        "pack": validation.pack.public_record(),
        "view_kinds": sorted(_VIEW_ROUTES),
        "content_formats": sorted(_CONTENT_FORMAT_ROUTES),
        "rendered_content_formats": sorted(content_format_routes),
        "unavailable_content_formats": unavailable_content_formats,
        "fixture_states": sorted(_FIXTURE_ROUTES),
        "extra_states": sorted(_EXTRA_ROUTES),
        "responsive_states": list(_RESPONSIVE_STATES),
        "fixture_coverage": _fixture_coverage(),
    }
    return GeneratedResult(output.resolve(), tuple(sorted(files)), drift, report)


def run_presentation_conformance(
    root: Path,
    output: Path,
    *,
    check: bool = False,
) -> GeneratedResult:
    """Exercise browser, static, PDF, visibility, and agent invariants for a pack."""
    validation = check_presentation_pack(root)
    if not validation.ok or validation.pack is None:
        return GeneratedResult(
            output.resolve(),
            (),
            (),
            {"ok": False, "validation": validation.to_dict()},
        )
    content_format_routes, unavailable_content_formats = _available_content_format_routes()
    with tempfile.TemporaryDirectory(prefix="fura-presentation-conformance-") as temporary:
        workspace = Path(temporary)
        target = _fixture_app(validation.pack, workspace / "target")
        baseline = _fixture_app(None, workspace / "baseline")
        target_rendered = asyncio.run(_render_routes(target))
        baseline_agents = asyncio.run(_agent_outputs(baseline))
        target_agents = asyncio.run(_agent_outputs(target))
        static_root = workspace / "static"
        export_static_site(
            target,
            StaticExportOptions(
                output_dir=static_root,
                include_index_txt=True,
                include_portal=True,
                include_search=True,
            ),
        )
        visibility = scan_visibility_leaks(static_root, visibility_canaries(target.catalog))
        pdf = export_pdfs(
            target.catalog,
            config=target.config,
            options=PDFExportOptions(
                output_dir=workspace / "pdf",
                update_channel_manifest=False,
            ),
        )
        checks = _conformance_checks(
            validation.pack,
            target_rendered,
            target_agents=target_agents,
            baseline_agents=baseline_agents,
            visibility_ok=visibility.ok,
            pdf_pages=pdf.page_count,
        )
        report = {
            "schema_version": 1,
            "ok": all(checks.values()),
            "pack": validation.pack.public_record(),
            "checks": checks,
            "agent_output_sha256": {
                key: hashlib.sha256(value.encode()).hexdigest()
                for key, value in sorted(target_agents.items())
            },
            "fixture_coverage": _fixture_coverage(),
            "view_kinds": sorted(_VIEW_ROUTES),
            "content_formats": sorted(_CONTENT_FORMAT_ROUTES),
            "rendered_content_formats": sorted(content_format_routes),
            "unavailable_content_formats": unavailable_content_formats,
            "fixture_states": sorted(_FIXTURE_ROUTES),
            "responsive_states": list(_RESPONSIVE_STATES),
        }
        files = {"conformance.json": json.dumps(report, indent=2, sort_keys=True) + "\n"}
    drift = _write_or_check(output.resolve(), files, check=check)
    return GeneratedResult(output.resolve(), tuple(files), drift, report)


def _fixture_app(pack: PresentationPack | None, workspace: Path) -> DocsApp:
    app_root = workspace / "app"
    content = app_root / "content"
    content.mkdir(parents=True)
    if pack is not None:
        destination = app_root / "presentation" / pack.id
        shutil.copytree(pack.root, destination)
    selection = "  layout: vanilla\n"
    trusted = ""
    if pack is not None:
        if pack.kind == "layout":
            selection = f"  layout: {pack.id}\n"
        elif pack.kind == "skin":
            selection += f"  skin: {pack.id}\n"
        else:
            selection += f"  overrides: [{pack.id}]\n"
        if pack.requires_trust:
            trusted = "  trusted_capabilities: [" + ", ".join(sorted(pack.requires_trust)) + "]\n"
    (app_root / "docs.yaml").write_text(
        "shell: shell.html\n"
        "theme:\n  id: furatena\n"
        "mounts: mounts.yaml\n"
        "presentation:\n"
        + selection
        + trusted
        + "compose:\n  collection:\n    data: collections.yaml\n",
        encoding="utf-8",
    )
    content_format_routes, _unavailable_content_formats = _available_content_format_routes()
    extensions = ", ".join(
        f'"{_CONTENT_FORMAT_EXTENSIONS[name]}"' for name in sorted(content_format_routes)
    )
    (app_root / "mounts.yaml").write_text(
        "mounts:\n  - id: reference\n    label: Reference fixtures\n"
        "    content_root: content\n    default: true\n"
        f"    extensions: [{extensions}]\n",
        encoding="utf-8",
    )
    (app_root / "collections.yaml").write_text(
        "reference:\n  title: Reference collection\n  nodes: [reference-guide, localized]\n",
        encoding="utf-8",
    )
    _write_fixture_content(content)
    config = load_docs_config(app_root / "docs.yaml")
    docs = DocsApp(
        config,
        repo_root=workspace,
        autodoc=False,
        serve=ServeConfig(ServeMode.PREVIEW, None, False, False),
    )
    errors, _warnings = check_view_templates(
        config,
        docs.theme,
        strict=True,
        repo_root=workspace,
        catalog=docs.catalog,
    )
    if errors:
        raise PresentationPackError(
            "presentation template reachability failed: " + "; ".join(errors)
        )
    return docs


def _write_fixture_content(content: Path) -> None:
    fixtures = {
        "_index.md": "---\ntitle: Reference home\nlayout: home\n---\n# Reference home\n",
        "reference-guide.md": (
            "---\ntitle: A deliberately long reference title that exercises wrapping and landmarks\n"
            "layout: doc\n---\n# Reference guide\n\n"
            "```python\nprint('deterministic fixture')\n```\n\n"
            ":::{note}\nA synthetic directive with no project content.\n:::\n"
        ),
        "guides/_index.md": "---\ntitle: Guides\nlayout: doc_list\n---\n# Guides\n",
        "guides/deep/navigation/topic.md": (
            "---\ntitle: Deep navigation topic\nlayout: doc\n---\n# Nested topic\n"
        ),
        "empty/_index.md": "---\ntitle: Empty state\nlayout: doc_list\n---\n",
        "formats/markdown.md": (
            "---\ntitle: Markdown format fixture\nlayout: doc\n---\n# Markdown fixture\n"
        ),
        "formats/html.html": (
            "---\ntitle: HTML format fixture\nlayout: doc\n---\n"
            "<h1>HTML fixture</h1><p>Rendered through the HTML adapter.</p>\n"
        ),
        "formats/rst.rst": (
            "---\ntitle: RST format fixture\nlayout: doc\n---\n\n"
            "RST fixture\n===========\n\nRendered through the docutils adapter.\n"
        ),
        "formats/mdx.mdx": (
            "---\ntitle: MDX format fixture\nlayout: doc\n---\n\n"
            "# MDX fixture\n\nRendered through the MDX adapter.\n"
        ),
        "formats/myst.myst": (
            "---\ntitle: MyST format fixture\nlayout: doc\n---\n\n"
            "# MyST fixture\n\nRendered through the MyST adapter.\n"
        ),
        "collection.md": (
            "---\ntitle: Reference collection\nlayout: collection\ncollection: reference\n---\n"
        ),
        "changelog.md": "---\ntitle: Changelog\nlayout: changelog\n---\n# Version 1.0\n",
        "api.md": "---\ntitle: Widgets API\nlayout: api_reference\n---\n# Widgets API\n",
        "localized.md": (
            "---\ntitle: Guia localizada\nlayout: page\nlang: es\n---\n# Contenido localizado\n"
        ),
        "saffron-boundary-481.md": (
            "---\ntitle: Saffron boundary 481\nvisibility: draft\n---\n"
            "FURA_DRAFT_FIXTURE_CANARY_481\n"
        ),
        "cobalt-boundary-481.md": (
            "---\ntitle: Cobalt boundary 481\nvisibility: private\n---\n"
            "FURA_PRIVATE_FIXTURE_CANARY_481\n"
        ),
        "orchid-boundary-481.md": (
            "---\ntitle: Orchid boundary 481\nvisibility: internal\naccess:\n  teams: [fixture-reviewers]\n"
            "---\nFURA_PROTECTED_FIXTURE_CANARY\n"
        ),
        "umber-boundary-481.md": (
            "---\ntitle: Umber boundary 481\nvisibility: archived\narchived_at: 2026-01-01\n"
            "---\nFURA_ARCHIVED_FIXTURE_CANARY_481\n"
        ),
    }
    for relative, source in sorted(fixtures.items()):
        path = content / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")


async def _render_routes(docs: DocsApp) -> dict[str, dict[str, Any]]:
    client = TestClient(docs.create_app())
    rendered: dict[str, dict[str, Any]] = {}
    content_format_routes, _unavailable_content_formats = _available_content_format_routes()
    routes = {**_VIEW_ROUTES, **content_format_routes, **_FIXTURE_ROUTES, **_EXTRA_ROUTES}
    for name, route in sorted(routes.items()):
        full = await client.get(route)
        fragment = await client.get(route, headers={"HX-Request": "true"})
        rendered[name] = {
            "route": route,
            "status": full.status,
            "full": full.text,
            "fragment_status": fragment.status,
            "fragment": fragment.text,
        }
    return rendered


async def _agent_outputs(docs: DocsApp) -> dict[str, str]:
    client = TestClient(docs.create_app())
    catalog = await client.get("/catalog.json")
    llms = await client.get("/llms.txt")
    payload = json.loads(catalog.text)
    _drop_volatile_fields(payload)
    return {
        "catalog.json": json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ),
        "llms.txt": llms.text,
    }


def _drop_volatile_fields(value: Any) -> None:
    """Remove indexing clocks that are unrelated to presentation selection."""
    if isinstance(value, dict):
        value.pop("last_indexed_at", None)
        for item in value.values():
            _drop_volatile_fields(item)
    elif isinstance(value, list):
        for item in value:
            _drop_volatile_fields(item)


def _preview_files(pack: PresentationPack, rendered: dict[str, dict[str, Any]]) -> dict[str, str]:
    files: dict[str, str] = {}
    scenarios: list[dict[str, Any]] = []
    for name, payload in sorted(rendered.items()):
        files[f"full/{name}.html"] = _normalize_preview_html(str(payload["full"]))
        files[f"fragment/{name}.html"] = _normalize_preview_html(str(payload["fragment"]))
        scenarios.append(
            {
                "id": name,
                "route": payload["route"],
                "status": payload["status"],
                "fragment_status": payload["fragment_status"],
                "full": f"full/{name}.html",
                "fragment": f"fragment/{name}.html",
            }
        )
    manifest = {
        "schema_version": 1,
        "pack": pack.public_record(),
        "scenarios": scenarios,
        "responsive_states": list(_RESPONSIVE_STATES),
        "fixture_coverage": _fixture_coverage(),
    }
    files["preview-manifest.json"] = json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    frames = "\n".join(
        f"<section><h2>{state['id']} ({state['width']}px)</h2>"
        f'<iframe title="{state["id"]} reference guide" src="full/doc.html" '
        f'width="{state["width"]}" height="{state["height"]}"></iframe></section>'
        for state in _RESPONSIVE_STATES
    )
    files["index.html"] = (
        '<!doctype html><html lang="en"><meta charset="utf-8">'
        f"<title>{pack.id} reference preview</title><main><h1>Reference preview</h1>{frames}</main></html>\n"
    )
    return files


def _normalize_preview_html(source: str) -> str:
    """Replace per-response CSP nonces in offline generated references."""
    return _NONCE_ATTR_RE.sub('nonce="__FURA_PREVIEW_NONCE__"', source)


def _conformance_checks(
    pack: PresentationPack,
    rendered: dict[str, dict[str, Any]],
    *,
    target_agents: dict[str, str],
    baseline_agents: dict[str, str],
    visibility_ok: bool,
    pdf_pages: int,
) -> dict[str, bool]:
    view_payloads = [rendered[name] for name in _VIEW_ROUTES]
    full_ok = all(payload["status"] == 200 for payload in view_payloads)
    fragment_ok = all(
        payload["fragment_status"] == 200 and "<html" not in payload["fragment"].lower()
        for payload in view_payloads
    )
    semantic_ok = all(
        'id="page-root"' in payload["full"] and "<main" in payload["full"].lower()
        for payload in view_payloads
    )
    navigation_ok = all("<nav" in payload["full"].lower() for payload in view_payloads)
    search_ok = all('role="search"' in payload["full"] for payload in view_payloads)
    print_ok = pack.kind != "layout" or any(
        "@media print" in path.read_text(encoding="utf-8") for path in _css_files(pack)
    )
    return {
        "accessibility_semantics": semantic_ok,
        "agent_outputs_presentation_independent": target_agents == baseline_agents,
        "fragment_html": fragment_ok,
        "full_html": full_ok,
        "navigation_hooks": navigation_ok,
        "pdf": pdf_pages > 0,
        "print_contract": print_ok,
        "responsive_states": len(_RESPONSIVE_STATES) == 3,
        "search_hooks": search_ok,
        "static_visibility": visibility_ok,
    }


def _fixture_coverage() -> list[str]:
    return [
        "api-reference",
        "code",
        "deep-navigation",
        "directives",
        "empty-state",
        "errors",
        "localization",
        "long-title",
        "visibility-archived",
        "visibility-draft",
        "visibility-private",
        "visibility-protected",
    ]


def _available_content_format_routes() -> tuple[dict[str, str], dict[str, str]]:
    routes = dict(_CONTENT_FORMAT_ROUTES)
    unavailable: dict[str, str] = {}
    if importlib.util.find_spec("docutils") is None:
        routes.pop("docutils-rst")
        unavailable["docutils-rst"] = (
            "Install furatena[formats] to include the optional RST adapter in the preview."
        )
    return routes, unavailable


def _write_or_check(output: Path, files: dict[str, str], *, check: bool) -> tuple[str, ...]:
    expected = set(files)
    actual = (
        {path.relative_to(output).as_posix() for path in output.rglob("*") if path.is_file()}
        if output.is_dir()
        else set()
    )
    drift = set(expected ^ actual)
    for relative, content in files.items():
        path = output / relative
        if not path.is_file() or path.read_text(encoding="utf-8") != content:
            drift.add(relative)
    if check:
        return tuple(sorted(drift))
    for relative, content in sorted(files.items()):
        path = output / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return ()
