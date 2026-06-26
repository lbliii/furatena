"""Furatena — hypermedia documentation app.

Configure the app in ``docs.yaml``. Run::

    ./app/run
    fura serve
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent
if str(REPO / "src") not in sys.path:
    sys.path.insert(0, str(REPO / "src"))


def _require_deps() -> None:
    missing: list[str] = []
    try:
        import chirp_ui  # noqa: F401
    except ImportError:
        missing.append("chirp-ui")
    try:
        import patitas  # noqa: F401
    except ImportError:
        missing.append("patitas (chirp[markdown])")
    try:
        import yaml  # noqa: F401
    except ImportError:
        missing.append("pyyaml")
    if missing:
        print("Missing packages for the docs app:", ", ".join(missing))
        print("From the repo root, run once: make install")
        raise SystemExit(1)


_require_deps()

from furatena.catalog.docs_app import DocsApp
from furatena.catalog.registry import load_mounts
from furatena.catalog.dev_banner import format_serve_startup
from furatena.catalog.runtime import ServeMode, resolve_serve_config

DOCS_CONFIG = ROOT / "docs.yaml"
FROZEN_DIR = ROOT / "frozen"
AUTODOC_CONFIG = REPO / "config" / "autodoc.yaml"


def _env_serve_mode() -> ServeMode | None:
    raw = os.environ.get("FURA_MODE", "").strip().lower()
    if raw in {"author", "hybrid", "preview"}:
        return ServeMode(raw)
    if os.environ.get("FURA_FROZEN"):
        return ServeMode.PREVIEW
    return None


def build_docs_app() -> DocsApp:
    mounts = load_mounts(ROOT / "mounts.yaml", repo_root=REPO)
    content_roots = tuple(m.content_root for m in mounts)
    serve = resolve_serve_config(
        docs_root=ROOT,
        content_roots=content_roots,
        mode=_env_serve_mode(),
        frozen_dir=FROZEN_DIR,
        env_frozen=bool(os.environ.get("FURA_FROZEN")),
    )
    return DocsApp.from_paths(
        DOCS_CONFIG,
        repo_root=REPO,
        autodoc_config=AUTODOC_CONFIG,
        serve=serve,
    )


_docs = build_docs_app()
app = _docs.app
catalog = _docs.catalog
embedding_index = _docs.embedding_index


def create_app():
    """Factory for tests and ``fura serve``."""
    return app


if __name__ == "__main__":
    port = int(os.environ.get("FURA_PORT", "8001"))
    host = os.environ.get("FURA_HOST", "127.0.0.1")
    url = f"http://{host}:{port}/"
    for line in format_serve_startup(
        _docs.serve,
        page_count=len(catalog.nodes),
        mount_count=len(catalog.mounts),
        url=url,
    ):
        print(line)
    _docs.run_serve(port=port, host=host)
