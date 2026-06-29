"""Link frozen catalog IR into a static site for GitHub Pages evaluation."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent
if str(REPO / "src") not in sys.path:
    sys.path.insert(0, str(REPO / "src"))

from furatena.catalog.docs_app import DocsApp
from furatena.catalog.runtime import ServeConfig, ServeMode
from furatena.catalog.static_export import (
    StaticExportOptions,
    export_static_site,
    normalize_base_path,
)

DOCS_CONFIG = ROOT / "docs.yaml"
FROZEN_DIR = ROOT / "frozen"
AUTODOC_CONFIG = REPO / "config" / "autodoc.yaml"
DEFAULT_SITE_URL = "https://lbliii.github.io/furatena"
DEFAULT_BASE_PATH = "/furatena"


def _parse_args(argv: list[str]) -> tuple[StaticExportOptions, Path]:
    import argparse

    parser = argparse.ArgumentParser(description="Export Chirp docs to static HTML")
    parser.add_argument(
        "output",
        nargs="?",
        default=str(ROOT / "public"),
        help="Output directory (default app/public)",
    )
    parser.add_argument(
        "--frozen",
        default=str(FROZEN_DIR),
        help="Frozen catalog directory (default app/frozen)",
    )
    parser.add_argument(
        "--base-path",
        default=DEFAULT_BASE_PATH,
        help="URL path prefix for GitHub Pages project site (default /furatena)",
    )
    parser.add_argument(
        "--site-url",
        default=DEFAULT_SITE_URL,
        help="Public origin for canonical/OG URLs",
    )
    parser.add_argument(
        "--no-index-txt",
        action="store_true",
        help="Skip per-page index.txt sidecars",
    )
    parser.add_argument(
        "--incremental",
        action="store_true",
        help="Only re-export changed pages (requires existing public/)",
    )
    parser.add_argument(
        "--fresh",
        action="store_true",
        help="Run freeze before export",
    )
    args = parser.parse_args(argv)

    frozen_dir = Path(args.frozen)
    if args.fresh or not (frozen_dir / "catalog.json").is_file():
        import freeze_catalog

        sys.argv = ["freeze_catalog.py", str(frozen_dir)]
        freeze_catalog.main()

    options = StaticExportOptions(
        output_dir=Path(args.output),
        base_path=normalize_base_path(args.base_path),
        site_url=args.site_url.rstrip("/"),
        include_index_txt=not args.no_index_txt,
        incremental=args.incremental,
        frozen_dir=frozen_dir,
    )
    return options, frozen_dir


def main(argv: list[str] | None = None) -> None:
    options, frozen_dir = _parse_args(argv or sys.argv[1:])

    docs = DocsApp.from_paths(
        DOCS_CONFIG,
        repo_root=REPO,
        autodoc_config=AUTODOC_CONFIG,
        autodoc=False,
        serve=ServeConfig(ServeMode.PREVIEW, frozen_dir, True, False),
    )
    result = export_static_site(docs, options)
    base = options.base_path or "/"
    print(
        f"Exported {result.page_count} pages + {result.sidecar_count} sidecars "
        f"to {result.output_dir} (base_path={base}, skipped={result.skipped_count})"
    )
    print("Preview: make docs-preview")


if __name__ == "__main__":
    main()
