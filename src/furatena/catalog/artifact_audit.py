"""Crawl static artifacts for broken or incorrectly scoped public URLs."""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import unquote, urljoin, urlsplit
from xml.etree import ElementTree

_MARKDOWN_LINK_RE = re.compile(r"\[[^\]]*\]\((?P<url>[^)\s]+)(?:\s+[^)]*)?\)")
_HTML_URL_ATTRS = frozenset(
    {"action", "formaction", "href", "hx-get", "hx-post", "hx-push-url", "src"}
)
_URL_KEYS = frozenset(
    {
        "canonical_url",
        "href",
        "og_url",
        "page_url",
        "self",
        "url",
    }
)
_ARTIFACT_KEYS = frozenset({"artifacts", "paths"})


@dataclass(frozen=True, slots=True)
class ArtifactReference:
    """One URL or artifact path discovered in generated output."""

    source: Path
    referrer: str
    target: str
    kind: str


@dataclass(frozen=True, slots=True)
class ArtifactAuditFinding:
    """One actionable static-artifact URL failure."""

    code: str
    message: str
    source: Path
    referrer: str
    target: str

    def format(self) -> str:
        return f"{self.code}: {self.source} ({self.referrer}) -> {self.target}: {self.message}"


@dataclass(frozen=True, slots=True)
class ArtifactAuditReport:
    """Stable result returned by :func:`audit_static_artifacts`."""

    output_dir: Path
    base_path: str
    site_url: str
    references: tuple[ArtifactReference, ...]
    findings: tuple[ArtifactAuditFinding, ...]

    @property
    def ok(self) -> bool:
        return not self.findings


class _HTMLReferenceParser(HTMLParser):
    def __init__(self, *, source: Path, referrer: str) -> None:
        super().__init__(convert_charrefs=True)
        self.source = source
        self.referrer = referrer
        self.references: list[ArtifactReference] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {name.lower(): value for name, value in attrs if value is not None}
        rel_tokens = set(values.get("rel", "").lower().split())
        for name, value in values.items():
            if name in _HTML_URL_ATTRS:
                if name == "hx-push-url" and value.lower() in {"true", "false"}:
                    continue
                kind = (
                    "canonical" if tag == "link" and "canonical" in rel_tokens else f"html:{name}"
                )
                self._append(value, kind=kind)
            elif name == "srcset":
                for candidate in value.split(","):
                    target = candidate.strip().split(maxsplit=1)[0]
                    self._append(target, kind="html:srcset")
        if tag == "meta" and values.get("property", "").lower() == "og:url":
            self._append(values.get("content", ""), kind="canonical")

    def _append(self, target: str, *, kind: str) -> None:
        target = target.strip()
        if target:
            self.references.append(
                ArtifactReference(
                    source=self.source,
                    referrer=self.referrer,
                    target=target,
                    kind=kind,
                )
            )


def _normalize_base_path(value: str) -> str:
    text = str(value or "").strip()
    if text in {"", "/"}:
        return ""
    return "/" + text.strip("/")


def _public_referrer(source: Path, *, base_path: str) -> str:
    if source.name == "index.html":
        path = source.parent.as_posix().strip("/")
        suffix = f"/{path}/" if path and path != "." else "/"
    else:
        suffix = f"/{source.as_posix().lstrip('/')}"
    return f"{base_path}{suffix}" if base_path else suffix


def _walk_json(
    value: Any,
    *,
    source: Path,
    referrer: str,
    trail: str = "$",
    parent_key: str = "",
    planned: bool = False,
) -> list[ArtifactReference]:
    references: list[ArtifactReference] = []
    if isinstance(value, dict):
        object_planned = planned or str(value.get("status", "")).lower() == "planned"
        for key, item in value.items():
            references.extend(
                _walk_json(
                    item,
                    source=source,
                    referrer=referrer,
                    trail=f"{trail}.{key}",
                    parent_key=str(key),
                    planned=object_planned,
                )
            )
        return references
    if isinstance(value, list):
        for index, item in enumerate(value):
            references.extend(
                _walk_json(
                    item,
                    source=source,
                    referrer=referrer,
                    trail=f"{trail}[{index}]",
                    parent_key=parent_key,
                    planned=planned,
                )
            )
        return references
    if not isinstance(value, str) or planned:
        return references
    target = value.strip()
    if (
        parent_key in _ARTIFACT_KEYS
        and target
        and not target.startswith(("/", "http://", "https://"))
    ):
        kind = "artifact"
    elif parent_key in _URL_KEYS or parent_key.endswith(("_href", "_url")):
        kind = f"json:{trail}"
    else:
        return references
    references.append(ArtifactReference(source=source, referrer=referrer, target=target, kind=kind))
    return references


def _references_from_file(path: Path, *, root: Path, base_path: str) -> list[ArtifactReference]:
    source = path.relative_to(root)
    referrer = _public_referrer(source, base_path=base_path)
    text = path.read_text(encoding="utf-8", errors="replace")
    if path.suffix == ".html":
        if path.name != "index.html":
            return []
        parser = _HTMLReferenceParser(source=source, referrer=referrer)
        parser.feed(text)
        return parser.references
    if path.suffix == ".json":
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            return []
        return _walk_json(payload, source=source, referrer=referrer)
    if path.suffix == ".xml":
        try:
            root_element = ElementTree.fromstring(text)
        except ElementTree.ParseError:
            return []
        references: list[ArtifactReference] = []
        for element in root_element.iter():
            local_name = element.tag.rsplit("}", 1)[-1]
            if local_name == "loc" and element.text:
                references.append(
                    ArtifactReference(source, referrer, element.text.strip(), "sitemap")
                )
            href = element.attrib.get("href")
            if href:
                references.append(ArtifactReference(source, referrer, href.strip(), "sitemap"))
        return references
    if path.suffix == ".txt":
        if path.name == "llms-full.txt":
            return []
        references: list[ArtifactReference] = []
        fence: str | None = None
        for line in text.splitlines():
            stripped = line.lstrip()
            marker = stripped[:3]
            if marker in {"```", "~~~"}:
                fence = None if fence == marker else marker
                continue
            if fence is None:
                navigable = re.sub(r"`+[^`]*`+", "", line)
                references.extend(
                    ArtifactReference(source, referrer, match.group("url"), "markdown")
                    for match in _MARKDOWN_LINK_RE.finditer(navigable)
                )
        for line in text.splitlines():
            if line.lower().startswith("sitemap:"):
                references.append(
                    ArtifactReference(source, referrer, line.split(":", 1)[1].strip(), "sitemap")
                )
        return references
    return []


def _target_file(root: Path, path: str, *, base_path: str) -> Path | None:
    decoded = unquote(path)
    if base_path:
        if decoded == base_path:
            decoded = "/"
        elif decoded.startswith(f"{base_path}/"):
            decoded = decoded[len(base_path) :]
        else:
            return None
    rel = decoded.lstrip("/")
    candidate = root / rel
    if not rel or decoded.endswith("/") or not PurePosixPath(rel).suffix:
        candidate /= "index.html"
    return candidate


def _finding(reference: ArtifactReference, code: str, message: str) -> ArtifactAuditFinding:
    return ArtifactAuditFinding(
        code=code,
        message=message,
        source=reference.source,
        referrer=reference.referrer,
        target=reference.target,
    )


def _audit_reference(
    reference: ArtifactReference,
    *,
    root: Path,
    base_path: str,
    site_url: str,
) -> list[ArtifactAuditFinding]:
    target = reference.target
    if reference.kind == "artifact":
        path = root / target
        return (
            []
            if path.is_file()
            else [_finding(reference, "missing-artifact", "artifact does not exist")]
        )
    if target.startswith(("#", "data:", "mailto:", "tel:", "javascript:")):
        return []
    if any(char.isspace() for char in target) or "\\" in target:
        return [_finding(reference, "malformed-url", "URL contains whitespace or a backslash")]
    try:
        parsed = urlsplit(target)
    except ValueError as exc:
        return [_finding(reference, "malformed-url", str(exc))]
    configured = urlsplit(site_url)
    configured_origin = f"{configured.scheme}://{configured.netloc}" if configured.netloc else ""
    target_origin = f"{parsed.scheme}://{parsed.netloc}" if parsed.netloc else ""
    requires_canonical_origin = reference.kind in {"canonical", "sitemap"}
    if parsed.scheme and parsed.scheme not in {"http", "https"}:
        return []
    if parsed.netloc and target_origin != configured_origin:
        if requires_canonical_origin:
            return [
                _finding(
                    reference,
                    "canonical-origin",
                    f"expected canonical origin {configured_origin or '(not configured)'}",
                )
            ]
        return []
    if requires_canonical_origin and not parsed.netloc:
        return [
            _finding(
                reference,
                "canonical-origin",
                f"expected an absolute URL on {configured_origin or '(configured site)'}",
            )
        ]
    path = parsed.path
    if not parsed.netloc and not path.startswith("/"):
        path = urlsplit(urljoin(reference.referrer, target)).path
    if ".." in PurePosixPath(unquote(path)).parts:
        return [_finding(reference, "malformed-url", "URL contains a parent-directory segment")]
    findings: list[ArtifactAuditFinding] = []
    doubled = f"{base_path}{base_path}" if base_path else ""
    if doubled and (path == doubled or path.startswith(f"{doubled}/")):
        findings.append(
            _finding(reference, "repeated-base-path", f"base path {base_path} appears twice")
        )
        return findings
    if base_path and path != base_path and not path.startswith(f"{base_path}/"):
        findings.append(
            _finding(reference, "escaped-base-path", f"URL must remain under {base_path}")
        )
        return findings
    target_file = _target_file(root, path, base_path=base_path)
    if target_file is not None and not target_file.is_file():
        findings.append(
            _finding(
                reference,
                "missing-target",
                f"resolved artifact {target_file.relative_to(root)} does not exist",
            )
        )
    return findings


def audit_static_artifacts(
    output_dir: Path,
    *,
    base_path: str,
    site_url: str,
) -> ArtifactAuditReport:
    """Parse generated artifacts and validate internal/canonical URL contracts."""
    root = output_dir.resolve()
    normalized_base = _normalize_base_path(base_path)
    references: list[ArtifactReference] = []
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.suffix in {".html", ".json", ".txt", ".xml"}:
            references.extend(_references_from_file(path, root=root, base_path=normalized_base))
    findings: list[ArtifactAuditFinding] = []
    for reference in references:
        findings.extend(
            _audit_reference(
                reference,
                root=root,
                base_path=normalized_base,
                site_url=site_url.rstrip("/"),
            )
        )
    unique = {
        (item.code, item.source, item.referrer, item.target, item.message): item
        for item in findings
    }
    return ArtifactAuditReport(
        output_dir=root,
        base_path=normalized_base,
        site_url=site_url.rstrip("/"),
        references=tuple(references),
        findings=tuple(
            unique[key] for key in sorted(unique, key=lambda item: tuple(map(str, item)))
        ),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--base-path", required=True)
    parser.add_argument("--site-url", required=True)
    args = parser.parse_args(argv)
    report = audit_static_artifacts(
        args.output_dir,
        base_path=args.base_path,
        site_url=args.site_url,
    )
    if report.ok:
        print(f"Audited {len(report.references)} static artifact references: no URL failures")
        return 0
    for finding in report.findings:
        print(f"error: {finding.format()}", file=sys.stderr)
    print(
        f"Static artifact URL audit failed with {len(report.findings)} finding(s)", file=sys.stderr
    )
    return 1


if __name__ == "__main__":  # pragma: no cover - module CLI boundary
    raise SystemExit(main())
