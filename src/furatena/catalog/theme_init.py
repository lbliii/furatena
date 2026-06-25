"""Scaffold a project-local skin pack directory."""

from __future__ import annotations

from pathlib import Path

_SKIN_FILES: dict[str, str] = {
    "tokens.css": """/* Brand tokens — override packaged lagoon or ship your own palette. */
@layer docs.tokens {
  :root {
    --chirpui-accent: #0e7490;
    --chirpui-accent-secondary: #b45309;
  }
}
""",
    "styles.css": """@layer docs.tokens, docs.overrides, docs.directives;

@import url("effects.css");
@import url("skin/fonts.css");
@import url("skin/shell.css");
@import url("skin/hero.css");
@import url("skin/chrome.css");
@import url("skin/error.css");
@import url("skin/home.css");
""",
    "directives.css": """@layer docs.directives {
  /* Directive skin overrides — see lagoon pack for examples. */
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
    "skin/fonts.css": "@layer docs.overrides {\n  /* Optional @font-face rules */\n}\n",
    "skin/shell.css": "@layer docs.overrides {\n  /* App shell layout overrides */\n}\n",
    "skin/hero.css": "@layer docs.overrides {\n  /* Docs hero accent rail overrides */\n}\n",
    "skin/chrome.css": "@layer docs.overrides {\n  /* TOC, nav, search chrome */\n}\n",
    "skin/error.css": "@layer docs.overrides {\n  /* Error page polish */\n}\n",
    "skin/home.css": "@layer docs.overrides {\n  /* Landing page polish */\n}\n",
    "README.md": """# Custom skin pack

Wire this directory in `docs.yaml`:

```yaml
theme:
  use: lagoon          # start from lagoon, then override files below
  overrides:
    tokens: theme-skin/tokens.css
    styles: theme-skin/styles.css
    directives: theme-skin/directives.css
```

Or register a `furatena.themes` entry point and set `theme.use: your-pack`.

Run `fura theme list` to see built-in docs-core and skin packs.
""",
}


def init_theme_pack(target: Path, *, force: bool = False) -> list[Path]:
    """Write starter skin files under *target* (created if missing)."""
    target = target.resolve()
    target.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for rel, content in _SKIN_FILES.items():
        path = target / rel
        if path.exists() and not force:
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        written.append(path)
    branding = target / "assets" / "branding"
    branding.mkdir(parents=True, exist_ok=True)
    readme = branding / "README.md"
    if force or not readme.is_file():
        readme.write_text(
            "Drop favicon.ico, favicon.svg, and site.webmanifest here.\n",
            encoding="utf-8",
        )
        written.append(readme)
    return written
