"""Deterministic scaffolds for repository-local presentation packs."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Literal

from furatena import __version__
from furatena.catalog.exceptions import CatalogConfigError
from furatena.catalog.presentation_pack import PRESENTATION_RENDER_CONTEXT_API_VERSION
from furatena.catalog.view_kinds import VIEW_KINDS

ScaffoldKind = Literal["layout", "skin", "override"]

_ID_TOKEN_RE = re.compile(r"[^a-z0-9]+")
_VALID_ID_RE = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")

_SKIN_FILES: dict[str, str] = {
    "tokens.css": """/* Brand tokens — pack-owned design tokens. */
@layer docs.tokens {
  :root {
    --pack-accent: #0e7490;
    --pack-accent-contrast: #ffffff;
  }
}
""",
    "styles.css": """@layer docs.tokens, docs.overrides, docs.directives;

@import url("tokens.css");
@import url("effects.css");
@import url("skin/fonts.css");
@import url("skin/shell.css");
@import url("skin/hero.css");
@import url("skin/chrome.css");
@import url("skin/error.css");
@import url("skin/home.css");

@layer docs.overrides {
  a { color: var(--pack-accent); }
  :focus-visible { outline: 0.2rem solid var(--pack-accent); outline-offset: 0.15rem; }
}
""",
    "directives.css": """@layer docs.directives {
  /* Directive presentation overrides belong here. */
}
""",
    "effects.css": """@layer docs.overrides {
  :root {
    --fura-effect-code-shadow: none;
    --fura-effect-card-shadow: none;
    --fura-effect-hero-shadow: none;
  }
}
""",
    "skin/fonts.css": "@layer docs.overrides {\n  /* Optional @font-face rules. */\n}\n",
    "skin/shell.css": "@layer docs.overrides {\n  /* App shell overrides. */\n}\n",
    "skin/hero.css": "@layer docs.overrides {\n  /* Docs hero overrides. */\n}\n",
    "skin/chrome.css": "@layer docs.overrides {\n  /* Navigation and search chrome. */\n}\n",
    "skin/error.css": "@layer docs.overrides {\n  /* Error-page presentation. */\n}\n",
    "skin/home.css": "@layer docs.overrides {\n  /* Landing-page presentation. */\n}\n",
    "js/README.md": "Add reviewed progressive-enhancement modules here, then grant scripts explicitly.\n",
    "assets/branding/README.md": "Add repository-owned branding assets here and declare them in the manifest.\n",
}

_LAYOUT_SHELL = """{% extends "layouts/fura_shell.html" %}
{% block title %}{{ node.title | default(site_name) }} · {{ site_name }}{% end %}
{% block head %}{% for href in docs_stylesheets() %}<link rel="stylesheet" href="{{ href }}">{% endfor %}{% end %}
{% block scripts %}{% end %}
{% block shell %}
<a class="pack-skip" href="#pack-content">Skip to content</a>
<header class="pack-header"><a href="/">{{ site_name }}</a>
<form action="/search" method="get" role="search"><label for="pack-search">Search documentation</label><input id="pack-search" name="q" type="search"><button type="submit">Search</button></form></header>
<main id="main">{% block page_root %}{% end %}</main>
{% end %}
"""

_LAYOUT_BASE = """{% extends "shell.html" %}
{% block page_root %}
<div id="page-root" class="pack-page" data-fura-surface="{{ chirp_docs_surface | default('catalog') }}">
  <nav aria-label="Documentation navigation"><ul>{% for item in nav_items | default([]) %}<li><a href="{{ item.href | default('') }}">{{ item.title | default('Untitled') }}</a></li>{% endfor %}</ul></nav>
  <div id="pack-content">{% block page_content %}{% block content %}{% end %}{% end %}</div>
</div>
{% end %}
"""

_LAYOUT_VIEW = """{% extends "layouts/base.html" %}
{% block content %}
<article aria-labelledby="page-title">
  <h1 id="page-title">{{ node.title | default('Reference fixture') }}</h1>
  {% if node %}{{ node | doc_body | boost_doc_links }}{% end %}
</article>
{% end %}
"""

_LAYOUT_FILES: dict[str, str] = {
    "shell.html": _LAYOUT_SHELL,
    "layouts/base.html": _LAYOUT_BASE,
    "error.html": """{% extends "layouts/base.html" %}
{% block content %}<section aria-labelledby="error-title"><p>{{ error_status | default(404) }}</p><h1 id="error-title">{{ error_headline | default('Page not found') }}</h1><p>{{ error_message | default(error_lead) }}</p><p><a href="/">Return home</a> or <a href="/search">search documentation</a>.</p></section>{% end %}
""",
    "search.html": """{% extends "layouts/base.html" %}
{% block content %}<section aria-labelledby="search-title"><h1 id="search-title">Search</h1>
<form action="/search" method="get" role="search"><label for="pack-query">Search terms</label><input id="pack-query" name="q" type="search" value="{{ search_query | default('') }}"><button type="submit">Search</button></form>
{% block search_results %}<div id="search-results-panel" aria-live="polite">{% if hits | default([]) %}<ol>{% for hit in hits %}<li><a href="{{ search_hit_url(hit) }}">{{ hit.node.title }}</a></li>{% endfor %}</ol>{% else %}<p role="status">No results.</p>{% end %}</div>{% end %}</section>{% end %}
""",
    "styles.css": """@layer presentation {
  :root { --pack-accent: #0e7490; color-scheme: light dark; }
  body { margin: 0; font: 1rem/1.6 system-ui, sans-serif; }
  .pack-skip { position: absolute; transform: translateY(-150%); }
  .pack-skip:focus { transform: none; }
  .pack-header, .pack-page { max-width: 80rem; margin-inline: auto; padding: 1rem; }
  @media (min-width: 48rem) { .pack-page { display: grid; grid-template-columns: 16rem 1fr; gap: 2rem; } }
  @media print { .pack-header, .pack-page > nav { display: none; } .pack-page { display: block; } }
  @media (prefers-reduced-motion: reduce) { *, *::before, *::after { scroll-behavior: auto !important; } }
}
""",
}

_SUPPORTING_LAYOUT_VIEWS = (
    "author_dashboard",
    "author_studio",
    "develop",
    "develop_export",
    "marketing_page",
)

_OVERRIDE_FILES: dict[str, str] = {
    "templates/views/doc.html": _LAYOUT_VIEW,
    "styles.css": """@layer docs.overrides {
  /* Sparse, project-owned presentation changes. */
}
""",
}


def _normalized_identity(target: Path) -> str:
    identity = _ID_TOKEN_RE.sub("-", target.name.lower()).strip("-")
    if not identity or not identity[0].isalpha():
        identity = f"pack-{identity}".rstrip("-")
    return identity


def _runtime_range() -> str:
    major, minor, *_rest = (int(part) for part in __version__.split(".")[:3])
    upper = f"{major + 1}.0.0" if major else f"0.{minor + 1}.0"
    return f">={major}.{minor}.0,<{upper}"


def _manifest(identity: str, kind: ScaffoldKind) -> str:
    templates: dict[str, str] = {}
    assets: list[dict[str, str]] = []
    capabilities: list[str] = []
    requires_trust: list[str] = []
    payload: dict[str, object] = {
        "schema_version": 1,
        "id": identity,
        "version": "0.1.0",
        "type": kind,
        "render_context_api_version": PRESENTATION_RENDER_CONTEXT_API_VERSION,
        "runtime": _runtime_range(),
        "templates": templates,
        "assets": assets,
        "capabilities": capabilities,
        "requires_trust": requires_trust,
    }
    if kind == "layout":
        templates.update((spec.kind, f"views/{spec.kind}.html") for spec in VIEW_KINDS)
        assets.append({"role": "styles", "path": "styles.css"})
        capabilities.extend(("templates", "styles"))
        payload["contract"] = {
            "render_modes": ["full", "fragment"],
            "slots": ["head", "scripts", "shell", "content"],
            "semantic_hooks": ["main", "page-root"],
            "component_api": "chirp-ui@0.11",
        }
    elif kind == "skin":
        assets.extend(
            (
                {"role": "tokens", "path": "tokens.css"},
                {"role": "styles", "path": "styles.css"},
                {"role": "directives", "path": "directives.css"},
                {"role": "scripts", "path": "js"},
            )
        )
        capabilities.extend(("styles", "scripts"))
        requires_trust.append("scripts")
    else:
        templates["doc"] = "templates/views/doc.html"
        assets.append({"role": "styles", "path": "styles.css"})
        capabilities.extend(("templates", "styles"))
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def _readme(identity: str, kind: ScaffoldKind) -> str:
    selection = {
        "layout": f"presentation:\n  layout: {identity}",
        "skin": (
            "presentation:\n"
            f"  skin: {identity}\n"
            "  trusted_capabilities: [scripts]  # explicit trust required by the skin v1 contract"
        ),
        "override": f"presentation:\n  overrides: [{identity}]",
    }[kind]
    return f"""# {identity}

Repository-local Furatena `{kind}` presentation pack. Select it in `docs.yaml`:

```yaml
{selection}
```

Run `fura theme check .`, `fura theme preview .`, and `fura theme conformance .`
before committing. Keep local packs below the site's `presentation/` directory; Python packaging
is optional and reserved for packs that must be preinstalled through an entry point.
"""


def scaffold_files(target: Path, *, kind: ScaffoldKind, identity: str) -> dict[str, str]:
    """Return the complete, deterministically ordered scaffold source map."""
    files = dict(
        _LAYOUT_FILES if kind == "layout" else _SKIN_FILES if kind == "skin" else _OVERRIDE_FILES
    )
    if kind == "layout":
        files.update((f"views/{spec.kind}.html", _LAYOUT_VIEW) for spec in VIEW_KINDS)
        files.update((f"views/{name}.html", _LAYOUT_VIEW) for name in _SUPPORTING_LAYOUT_VIEWS)
    files["presentation-pack.json"] = _manifest(identity, kind)
    files["README.md"] = _readme(identity, kind)
    return dict(sorted(files.items()))


def init_theme_pack(
    target: Path,
    *,
    force: bool = False,
    kind: ScaffoldKind = "skin",
    identity: str | None = None,
) -> list[Path]:
    """Write a valid presentation-pack scaffold under *target*."""
    target = target.resolve()
    pack_id = identity or _normalized_identity(target)
    if _VALID_ID_RE.fullmatch(pack_id) is None:
        raise CatalogConfigError(
            f"presentation pack id {pack_id!r} must use lowercase kebab-case.",
            path=target / "presentation-pack.json",
            operation="theme init",
        )
    target.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for relative, content in scaffold_files(target, kind=kind, identity=pack_id).items():
        path = target / relative
        if path.exists() and not force:
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        written.append(path)
    return written
