#!/usr/bin/env python3
"""Generate and verify the shared browser/native PDF proof corpus."""

from __future__ import annotations

import argparse
import contextlib
import functools
import json
import socketserver
import subprocess
import sys
import threading
from collections.abc import Iterator, Mapping
from http.server import SimpleHTTPRequestHandler
from pathlib import Path
from typing import Any

from playwright.sync_api import sync_playwright

from furatena.catalog.pdf_proof import apply_baseline, inspect_pdf, write_report

PUBLIC_SENTINEL = "FURA_PDF_PUBLIC_SENTINEL_434"
PROTECTED_SENTINEL = "FURA_PDF_PROTECTED_SENTINEL_434"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app-root", type=Path, default=Path("app"))
    parser.add_argument("--site-root", type=Path)
    parser.add_argument("--browser-url", default="")
    parser.add_argument("--route", default="/proof/pdf-stress/")
    parser.add_argument("--collection", default="proof")
    parser.add_argument("--output", type=Path, default=Path("pdf-proof/artifacts"))
    parser.add_argument("--baseline", type=Path, default=Path("config/pdf-proof-baseline.json"))
    parser.add_argument("--hosted-only", action="store_true")
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()

    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, Any]] = []
    if not args.hosted_only:
        records.extend(
            _native_records(
                args.app_root.resolve(),
                output,
                args.route,
                args.collection,
            )
        )

    if args.browser_url:
        records.append(_browser_record(args.browser_url, output, scope="hosted"))
    elif args.site_root:
        with _site_server(args.site_root.resolve()) as origin:
            records.append(_browser_record(f"{origin}{args.route}", output, scope="local"))
    else:
        parser.error("provide --site-root or --browser-url")

    baseline = {} if args.strict else _load_baseline(args.baseline)
    outcome = apply_baseline(records, baseline)
    payload = {
        "schema_version": 1,
        "fixture": {
            "route": args.route,
            "collection": args.collection,
            "public_sentinel": PUBLIC_SENTINEL,
            "protected_sentinel": PROTECTED_SENTINEL,
        },
        "records": records,
        "outcome": outcome,
    }
    write_report(output / "report.json", payload)
    (output / "summary.md").write_text(_summary(payload), encoding="utf-8")
    print(json.dumps({"ok": outcome["ok"], "report": str(output / "report.json")}))
    return 0 if outcome["ok"] else 1


def _native_records(
    app_root: Path,
    output: Path,
    route: str,
    collection: str,
) -> list[dict[str, Any]]:
    native_dir = output / "native"
    commands = (
        ("page", ["--page", route]),
        ("collection", ["--collection", collection]),
        ("site", []),
    )
    records: list[dict[str, Any]] = []
    for scope, selectors in commands:
        command = [
            sys.executable,
            "-m",
            "furatena.cli.main",
            "--app-root",
            str(app_root),
            "pdf",
            str(native_dir),
            *selectors,
            "--live",
            "--json",
            "--no-autodoc",
            "--no-channels",
        ]
        completed = subprocess.run(command, check=True, capture_output=True, text=True)
        result = json.loads(completed.stdout)
        data = result["data"]
        pdf_path = Path(data["paths"][0])
        record = inspect_pdf(
            pdf_path,
            raster_dir=output / "rasters" / f"native-{scope}",
            sentinels=(PUBLIC_SENTINEL,),
            forbidden_sentinels=(PROTECTED_SENTINEL,),
            require_tagged=True,
            require_outline=True,
            require_annotations=True,
            reported_page_count=int(data["page_count"]),
        )
        record.update({"head": "native", "scope": scope})
        records.append(record)
    return records


def _browser_record(url: str, output: Path, *, scope: str) -> dict[str, Any]:
    browser_dir = output / "browser"
    browser_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = browser_dir / f"{scope}-pdf-stress.pdf"
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        response = page.goto(url, wait_until="networkidle")
        if response is None or not response.ok:
            status = response.status if response is not None else "no response"
            raise RuntimeError(f"browser proof page failed to load: {url} ({status})")
        provenance = {
            "source_url": page.url,
            "title": page.title(),
            "canonical_url": page.locator('link[rel="canonical"]').first.get_attribute("href"),
        }
        page.pdf(
            path=str(pdf_path),
            format="Letter",
            print_background=True,
            prefer_css_page_size=True,
            tagged=True,
            outline=True,
        )
        browser.close()
    record = inspect_pdf(
        pdf_path,
        raster_dir=output / "rasters" / f"browser-{scope}",
        sentinels=(PUBLIC_SENTINEL,),
        forbidden_sentinels=(PROTECTED_SENTINEL,),
        require_tagged=True,
        require_outline=True,
        require_annotations=True,
    )
    record.update({"head": "browser", "scope": scope, "provenance": provenance})
    return record


@contextlib.contextmanager
def _site_server(root: Path) -> Iterator[str]:
    handler = functools.partial(SimpleHTTPRequestHandler, directory=str(root))
    with socketserver.TCPServer(("127.0.0.1", 0), handler) as server:
        server.daemon_threads = True
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            host, port = server.server_address
            yield f"http://{host}:{port}"
        finally:
            server.shutdown()
            thread.join(timeout=5)


def _load_baseline(path: Path) -> Mapping[str, Any]:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _summary(payload: Mapping[str, Any]) -> str:
    outcome = payload["outcome"]
    lines = [
        "# PDF proof summary",
        "",
        f"Status: {'pass' if outcome['ok'] else 'fail'}",
        "",
        "| Head | Scope | Pages | Tagged | Outline | Links | Diagnostics |",
        "|---|---|---:|---:|---:|---:|---:|",
    ]
    for record in payload["records"]:
        lines.append(
            "| {head} | {scope} | {pages} | {tagged} | {outline} | {links} | {diagnostics} |".format(
                head=record["head"],
                scope=record["scope"],
                pages=record["page_count"],
                tagged="yes" if record["tagged"] else "no",
                outline=record["outline_count"],
                links=record["annotation_count"],
                diagnostics=len(record["diagnostics"]),
            )
        )
    lines.extend(
        [
            "",
            f"Blocking regressions: {len(outcome['blocking'])}",
            f"Known native-renderer gaps: {len(outcome['known_gaps'])}",
            "",
        ]
    )
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
