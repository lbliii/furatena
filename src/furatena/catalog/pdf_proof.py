"""Deterministic structural and raster diagnostics for PDF proof artifacts."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from collections import Counter
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from PIL import Image
from pypdf import PdfReader


def inspect_pdf(
    path: Path,
    *,
    raster_dir: Path,
    sentinels: Iterable[str] = (),
    forbidden_sentinels: Iterable[str] = (),
    require_tagged: bool = False,
    require_outline: bool = False,
    require_annotations: bool = False,
    required_structure_types: Iterable[str] = (),
    required_metadata: Mapping[str, str] | None = None,
    reported_page_count: int | None = None,
    render: bool = True,
) -> dict[str, Any]:
    """Inspect one PDF and return JSON-serializable proof evidence."""

    reader = PdfReader(str(path))
    page_text = [page.extract_text() or "" for page in reader.pages]
    text = "\n".join(page_text)
    root = _resolved(reader.trailer["/Root"])
    mark_info = _resolved(root.get("/MarkInfo")) or {}
    struct_root = _resolved(root.get("/StructTreeRoot")) or {}
    outline_count = _outline_count(reader.outline)
    annotations = [
        _resolved(annotation)
        for page in reader.pages
        for annotation in (_resolved(page.get("/Annots")) or ())
    ]
    annotation_count = len(annotations)
    annotation_urls = sorted(
        str(uri)
        for annotation in annotations
        if isinstance(annotation, Mapping)
        for action in (_resolved(annotation.get("/A")),)
        if isinstance(action, Mapping)
        for uri in (action.get("/URI"),)
        if uri
    )
    tagged = bool(mark_info.get("/Marked")) and bool(struct_root)
    structure_types = _structure_type_counts(struct_root)
    metadata = {
        str(key).removeprefix("/"): str(value)
        for key, value in (reader.metadata or {}).items()
        if value is not None
    }
    diagnostics: list[dict[str, Any]] = []

    if require_tagged and not tagged:
        diagnostics.append(_diagnostic("pdf.tagged", "PDF is not structurally tagged"))
    if require_outline and outline_count == 0:
        diagnostics.append(_diagnostic("pdf.outline", "PDF has no document outline"))
    if require_annotations and annotation_count == 0:
        diagnostics.append(_diagnostic("pdf.annotations", "PDF has no link annotations"))
    tracked_urls = [
        url for url in annotation_urls if re.search(r"[?&](?:utm_[^=]+|fbclid|gclid)=", url, re.I)
    ]
    if tracked_urls:
        diagnostics.append(
            _diagnostic(
                "pdf.tracking-parameters",
                f"link annotations retain tracking parameters: {tracked_urls}",
            )
        )
    missing_structure_types = sorted(set(required_structure_types) - set(structure_types))
    if missing_structure_types:
        diagnostics.append(
            _diagnostic(
                "pdf.structure-types",
                f"PDF is missing required structure types: {missing_structure_types}",
            )
        )
    for key, expected in (required_metadata or {}).items():
        if metadata.get(key) != expected:
            diagnostics.append(
                _diagnostic(
                    "pdf.metadata",
                    f"metadata {key} is {metadata.get(key)!r}, expected {expected!r}",
                )
            )
    if reported_page_count is not None and reported_page_count != len(reader.pages):
        diagnostics.append(
            _diagnostic(
                "pdf.reported-page-count",
                f"export reported {reported_page_count} page(s), PDF contains {len(reader.pages)}",
            )
        )

    sentinel_status: dict[str, bool] = {}
    sentinel_counts: dict[str, int] = {}
    for sentinel in sentinels:
        count = text.count(sentinel)
        present = count == 1
        sentinel_status[sentinel] = present
        sentinel_counts[sentinel] = count
        if count == 0:
            diagnostics.append(
                _diagnostic("pdf.missing-sentinel", f"missing public sentinel: {sentinel}")
            )
        elif count > 1:
            diagnostics.append(
                _diagnostic(
                    "pdf.duplicate-sentinel",
                    f"public sentinel appears {count} times: {sentinel}",
                )
            )
    for sentinel in forbidden_sentinels:
        count = text.count(sentinel)
        sentinel_status[sentinel] = count == 0
        sentinel_counts[sentinel] = count
        if count:
            diagnostics.append(
                _diagnostic("pdf.protected-sentinel", f"protected sentinel leaked: {sentinel}")
            )

    rasters: list[dict[str, Any]] = []
    if render:
        raster_paths = render_pdf(path, raster_dir)
        rasters = [
            analyze_raster(item, text=page_text[index]) for index, item in enumerate(raster_paths)
        ]
        diagnostics.extend(_raster_diagnostics(rasters))

    return {
        "path": str(path),
        "bytes": path.stat().st_size,
        "page_count": len(reader.pages),
        "reported_page_count": reported_page_count,
        "metadata": metadata,
        "pdfinfo": pdfinfo(path),
        "outline_count": outline_count,
        "annotation_count": annotation_count,
        "annotation_urls": annotation_urls,
        "tagged": tagged,
        "structure": {
            "mark_info_marked": bool(mark_info.get("/Marked")),
            "struct_tree_root": bool(struct_root),
            "role_map": sorted(str(key) for key in (_resolved(struct_root.get("/RoleMap")) or {})),
            "type_counts": structure_types,
        },
        "text_characters": len(text),
        "sentinels": sentinel_status,
        "sentinel_counts": sentinel_counts,
        "rasters": rasters,
        "diagnostics": diagnostics,
    }


def render_pdf(path: Path, output_dir: Path, *, dpi: int = 96) -> tuple[Path, ...]:
    """Render all PDF pages to stable PNG paths with Poppler."""

    executable = shutil.which("pdftoppm")
    if executable is None:
        raise RuntimeError("pdftoppm is required for PDF proof raster checks")
    output_dir.mkdir(parents=True, exist_ok=True)
    prefix = output_dir / path.stem
    subprocess.run(
        [executable, "-png", "-r", str(dpi), str(path), str(prefix)],
        check=True,
        capture_output=True,
        text=True,
    )
    return tuple(sorted(output_dir.glob(f"{path.stem}-*.png")))


def analyze_raster(path: Path, *, text: str = "") -> dict[str, Any]:
    """Measure page color, visible ink, and edge-contact ratios."""

    with Image.open(path) as source:
        image = source.convert("L")
        histogram = image.histogram()
        pixels = image.width * image.height
        ink_ratio = sum(histogram[:245]) / pixels
        dark_ratio = sum(histogram[:72]) / pixels
        edge = max(2, min(image.width, image.height) // 250)
        right = image.crop((image.width - edge, 0, image.width, image.height))
        left = image.crop((0, 0, edge, image.height))
        edge_pixels = edge * image.height
        edge_ink_ratio = max(
            sum(right.histogram()[:245]) / edge_pixels,
            sum(left.histogram()[:245]) / edge_pixels,
        )
        return {
            "path": str(path),
            "width": image.width,
            "height": image.height,
            "ink_ratio": round(ink_ratio, 6),
            "dark_ratio": round(dark_ratio, 6),
            "edge_ink_ratio": round(edge_ink_ratio, 6),
            "text_characters": len(text.strip()),
        }


def pdfinfo(path: Path) -> dict[str, str]:
    """Return Poppler metadata when pdfinfo is available."""

    executable = shutil.which("pdfinfo")
    if executable is None:
        return {}
    completed = subprocess.run(
        [executable, str(path)],
        check=True,
        capture_output=True,
        text=True,
    )
    result: dict[str, str] = {}
    for line in completed.stdout.splitlines():
        key, separator, value = line.partition(":")
        if separator:
            result[key.strip()] = value.strip()
    return result


def apply_baseline(
    records: Iterable[Mapping[str, Any]], baseline: Mapping[str, Any]
) -> dict[str, Any]:
    """Classify diagnostics as known gaps or blocking regressions."""

    allowed = baseline.get("allowed_failures", {})
    blocking: list[dict[str, str]] = []
    known: list[dict[str, str]] = []
    for record in records:
        head = str(record.get("head") or "")
        scope = str(record.get("scope") or "")
        allowed_rules = {
            str(rule)
            for key in (head, f"{head}:{scope}")
            for rule in (allowed.get(key, ()) if isinstance(allowed, Mapping) else ())
        }
        for diagnostic in record.get("diagnostics", ()):
            item = {
                "artifact": str(record.get("path") or ""),
                "head": head,
                "scope": scope,
                "rule": str(diagnostic.get("rule") or ""),
                "message": str(diagnostic.get("message") or ""),
            }
            (known if item["rule"] in allowed_rules else blocking).append(item)
    return {"ok": not blocking, "blocking": blocking, "known_gaps": known}


def write_report(path: Path, payload: Mapping[str, Any]) -> None:
    """Write stable, reviewable JSON proof output."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _raster_diagnostics(rasters: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not rasters:
        return [_diagnostic("raster.missing", "PDF produced no raster pages")]
    diagnostics: list[dict[str, Any]] = []
    dark_pages = [index + 1 for index, item in enumerate(rasters) if item["dark_ratio"] > 0.35]
    light_pages = [index + 1 for index, item in enumerate(rasters) if item["dark_ratio"] < 0.08]
    if dark_pages:
        diagnostics.append(
            _diagnostic("raster.dark-background", f"dark print background on pages {dark_pages}")
        )
    if dark_pages and light_pages:
        diagnostics.append(
            _diagnostic("raster.mixed-theme", "PDF mixes dark and light page themes")
        )
    low_contrast = [
        index + 1
        for index, item in enumerate(rasters)
        if item["text_characters"] >= 100 and item["ink_ratio"] < 0.002
    ]
    if low_contrast:
        diagnostics.append(
            _diagnostic(
                "raster.low-contrast-text",
                f"text-bearing pages have too little visible ink: {low_contrast}",
            )
        )
    clipped = [index + 1 for index, item in enumerate(rasters) if item["edge_ink_ratio"] > 0.015]
    if clipped:
        diagnostics.append(
            _diagnostic("raster.edge-clipping", f"content touches a page edge: {clipped}")
        )
    final = rasters[-1]
    if len(rasters) > 1 and final["ink_ratio"] < 0.001 and final["text_characters"] < 80:
        diagnostics.append(_diagnostic("raster.blank-final-page", "final PDF page is mostly blank"))
    return diagnostics


def _diagnostic(rule: str, message: str) -> dict[str, str]:
    return {"rule": rule, "message": message}


def _outline_count(items: Iterable[Any]) -> int:
    count = 0
    for item in items:
        if isinstance(item, list):
            count += _outline_count(item)
        else:
            count += 1
    return count


def _structure_type_counts(struct_root: Mapping[str, Any]) -> dict[str, int]:
    counts: Counter[str] = Counter()
    pending = list(_as_items(struct_root.get("/K")))
    seen: set[int] = set()
    while pending:
        raw = pending.pop()
        item = _resolved(raw)
        identity = id(item)
        if identity in seen or not isinstance(item, Mapping):
            continue
        seen.add(identity)
        structure_type = item.get("/S")
        if structure_type:
            counts[str(structure_type).removeprefix("/")] += 1
        pending.extend(_as_items(item.get("/K")))
    return dict(sorted(counts.items()))


def _as_items(value: Any) -> tuple[Any, ...]:
    value = _resolved(value)
    if value is None:
        return ()
    if isinstance(value, list):
        return tuple(value)
    return (value,)


def _resolved(value: Any) -> Any:
    getter = getattr(value, "get_object", None)
    return getter() if callable(getter) else value
