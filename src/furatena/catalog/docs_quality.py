"""Actionable documentation completeness gates and reasoned exemptions."""

from __future__ import annotations

import json
import re
import shlex
import subprocess
from contextlib import suppress
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Any

from furatena.catalog.access import AccessPermission, accessible_nodes
from furatena.catalog.check import check_broken_internal_links
from furatena.catalog.docs_inventory import build_documentation_inventory
from furatena.catalog.graph import build_federated_backlinks

_DISPOSITIONS = {"deferred", "internal"}
_SHELL_FENCE = re.compile(r"^\s*```(bash|sh|shell|console)\s*$")
_OWNER_BY_SURFACE = {
    "cli_command": "platform-docs",
    "config_field": "platform-docs",
    "deployment_profile": "security-operations",
    "diagnostic": "platform-docs",
    "mcp_resource": "agent-platform",
    "mcp_tool": "agent-platform",
    "route": "docs-product",
    "sidecar": "agent-platform",
}


@dataclass(frozen=True, slots=True)
class DocsQualityFinding:
    """One actionable documentation completeness finding."""

    kind: str
    target: str
    owner: str
    recommended_page_type: str
    message: str
    next_action: str
    source_path: str | None = None

    @property
    def id(self) -> str:
        return f"{self.kind}:{self.target}"

    @property
    def rule_id(self) -> str:
        return f"fura.docs_quality.{self.kind}"

    def to_dict(self) -> dict[str, object]:
        return {"id": self.id, "rule_id": self.rule_id, **asdict(self)}


def load_docs_quality_exemptions(path: Path | None) -> tuple[dict[str, str], ...]:
    """Load strict internal/deferred exemptions from a JSON contract."""

    if path is None or not path.is_file():
        return ()
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != 1:
        raise ValueError("docs-quality exemptions require schema_version 1")
    exemptions: list[dict[str, str]] = []
    for index, raw in enumerate(payload.get("exemptions", [])):
        finding = str(raw.get("finding") or "").strip()
        disposition = str(raw.get("disposition") or "").strip()
        reason = str(raw.get("reason") or "").strip()
        if not finding or disposition not in _DISPOSITIONS or not reason:
            raise ValueError(
                f"docs-quality exemption {index} requires finding, "
                "internal/deferred disposition, and reason"
            )
        exemptions.append(
            {
                "finding": finding,
                "disposition": disposition,
                "reason": reason,
            }
        )
    return tuple(exemptions)


def _nav_urls(items: list[dict[str, Any]]) -> set[str]:
    urls: set[str] = set()
    for item in items:
        href = str(item.get("href") or "")
        if href:
            urls.add(href)
        urls.update(_nav_urls(list(item.get("children") or ())))
    return urls


def _page_type(value: str) -> str:
    path = value.lower()
    if "/get-started/" in path:
        return "tutorial"
    if "/concepts/" in path or "/about/" in path:
        return "explanation"
    if "/reference/" in path:
        return "reference"
    return "how-to"


def _owner(node: Any | None) -> str:
    if node is None:
        return "docs-product"
    meta = getattr(node, "meta", {})
    return str(meta.get("owner") or meta.get("team") or "docs-product")


def _broken_link_findings(catalog: Any) -> list[DocsQualityFinding]:
    by_source = {str(node.source_path or node.slug or node.url): node for node in catalog.nodes}
    findings: list[DocsQualityFinding] = []
    for message in check_broken_internal_links(catalog):
        source = message.split(":", 1)[0]
        node = by_source.get(source)
        findings.append(
            DocsQualityFinding(
                kind="link",
                target=message,
                owner=_owner(node),
                recommended_page_type=_page_type(getattr(node, "url", source)),
                message=message,
                next_action="Fix the target or link, then rerun fura docs-quality.",
                source_path=source,
            )
        )
    return findings


def _navigation_findings(docs_app: Any) -> list[DocsQualityFinding]:
    catalog = docs_app.catalog
    public = accessible_nodes(
        catalog,
        catalog.nodes,
        permission=AccessPermission.READ,
    )
    nav_urls = _nav_urls(catalog.nav_tree())
    default_mount = str(getattr(catalog.default_mount, "id", ""))
    return [
        DocsQualityFinding(
            kind="navigation",
            target=node.url,
            owner=_owner(node),
            recommended_page_type=_page_type(node.url),
            message=f"public page {node.url} is missing from default catalog navigation",
            next_action="Add the page to generated navigation or record a reasoned exemption.",
            source_path=node.source_path,
        )
        for node in public
        if node.mount == default_mount and node.url != "/" and node.url not in nav_urls
    ]


def _orphan_findings(docs_app: Any) -> list[DocsQualityFinding]:
    catalog = docs_app.catalog
    public = accessible_nodes(
        catalog,
        catalog.nodes,
        permission=AccessPermission.READ,
    )
    nav_urls = _nav_urls(catalog.nav_tree())
    backlinks = build_federated_backlinks(list(public), catalog=catalog)
    return [
        DocsQualityFinding(
            kind="orphan",
            target=node.url,
            owner=_owner(node),
            recommended_page_type=_page_type(node.url),
            message=f"public page {node.url} has no inbound content or navigation link",
            next_action="Link the page from its owning journey or record a reasoned exemption.",
            source_path=node.source_path,
        )
        for node in public
        if node.url != "/" and node.url not in nav_urls and not backlinks.get(node.url)
    ]


def _public_feature_findings(inventory: dict[str, object]) -> list[DocsQualityFinding]:
    findings: list[DocsQualityFinding] = []
    for record in inventory["surfaces"]:
        if record["coverage"] not in {"missing", "stale"}:
            continue
        kind = str(record["kind"])
        target = str(record["id"])
        state = str(record["coverage"])
        findings.append(
            DocsQualityFinding(
                kind="public_feature",
                target=target,
                owner=_OWNER_BY_SURFACE.get(kind, "docs-product"),
                recommended_page_type="reference",
                message=f"{target} has {state} public documentation coverage",
                next_action=(
                    "Add or refresh reference coverage with the stable identifier, "
                    "or record a reasoned exemption."
                ),
                source_path=str(record["implementation"]),
            )
        )
    return findings


def _shell_blocks(path: Path) -> list[tuple[int, str, str]]:
    blocks: list[tuple[int, str, str]] = []
    language = ""
    start = 0
    body: list[str] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not language:
            match = _SHELL_FENCE.match(line)
            if match:
                language = match.group(1)
                start = line_number + 1
                body = []
            continue
        if line.strip() == "```":
            text = "\n".join(body)
            if language == "console":
                text = "\n".join(item[2:] for item in body if item.startswith(("$ ", "> ")))
            blocks.append((start, language, text))
            language = ""
            body = []
        else:
            body.append(line)
    return blocks


def _logical_shell_lines(text: str) -> list[str]:
    logical: list[str] = []
    pending = ""
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        pending = f"{pending} {line}".strip()
        if pending.endswith("\\"):
            pending = pending[:-1].rstrip()
            continue
        logical.append(pending)
        pending = ""
    if pending:
        logical.append(pending)
    return logical


def _cli_contracts() -> tuple[set[str], dict[tuple[str, ...], set[str]]]:
    from furatena.catalog.docs_reference import cli_reference_records

    root_options: set[str] = set()
    commands: dict[tuple[str, ...], set[str]] = {}
    for record in cli_reference_records():
        path = tuple(str(record["command"]).split()[1:])
        options = {
            option
            for item in record["options"]
            for option in str(item["label"]).split(", ")
            if option.startswith("--")
        }
        if not path:
            root_options = options | {"--help"}
        else:
            commands[path] = options | root_options | {"--help"}
    return root_options, commands


def _snippet_semantic_errors(text: str, *, make_targets: set[str]) -> list[str]:
    root_options, commands = _cli_contracts()
    top_commands = {path[0] for path in commands}
    errors: list[str] = []
    for line in _logical_shell_lines(text):
        try:
            tokens = shlex.split(line, comments=True)
        except ValueError:
            continue
        if "fura" in tokens:
            tail = tokens[tokens.index("fura") + 1 :]
            command_index = next(
                (index for index, token in enumerate(tail) if token in top_commands),
                None,
            )
            if command_index is None:
                unknown = [
                    token.split("=", 1)[0]
                    for token in tail
                    if token.startswith("--") and token.split("=", 1)[0] not in root_options
                ]
                errors.extend(f"unknown root fura option {option}" for option in unknown)
                continue
            command_tail = tail[command_index:]
            matches = [path for path in commands if tuple(command_tail[: len(path)]) == path]
            command = max(matches, key=len) if matches else (command_tail[0],)
            allowed = commands.get(command, root_options)
            for token in tail:
                option = token.split("=", 1)[0]
                if option.startswith("--") and option not in allowed:
                    errors.append(f"unknown option {option} for fura {' '.join(command)}")
        if "make" in tokens:
            tail = tokens[tokens.index("make") + 1 :]
            candidates = [token for token in tail if token and not token.startswith(("-", "$"))]
            if candidates:
                target = candidates[-1]
                if "=" not in target and target not in make_targets:
                    errors.append(f"unknown make target {target}")
    return errors


def _snippet_findings(
    documentation_roots: tuple[Path, ...],
) -> tuple[list[DocsQualityFinding], dict[str, int]]:
    makefile = Path.cwd() / "Makefile"
    make_targets = (
        set(re.findall(r"^([A-Za-z0-9_.-]+):", makefile.read_text(encoding="utf-8"), re.MULTILINE))
        if makefile.is_file()
        else set()
    )
    findings: list[DocsQualityFinding] = []
    file_count = 0
    block_count = 0
    for root in documentation_roots:
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*.md")):
            blocks = _shell_blocks(path)
            if not blocks:
                continue
            file_count += 1
            for line, _language, text in blocks:
                block_count += 1
                normalized = re.sub(r"<[A-Za-z_][^>\n]*>", "placeholder", text)
                syntax = subprocess.run(
                    ("bash", "-n"),
                    input=normalized,
                    text=True,
                    capture_output=True,
                    check=False,
                )
                errors = []
                if syntax.returncode:
                    errors.append((syntax.stderr or "invalid shell syntax").strip())
                errors.extend(_snippet_semantic_errors(normalized, make_targets=make_targets))
                for error in errors:
                    findings.append(
                        DocsQualityFinding(
                            kind="snippet",
                            target=f"{path.as_posix()}:{line}",
                            owner="docs-product",
                            recommended_page_type=_page_type(path.as_posix()),
                            message=error,
                            next_action="Fix or retag the shell snippet, then rerun fura docs-quality.",
                            source_path=path.as_posix(),
                        )
                    )
    return findings, {"file_count": file_count, "block_count": block_count}


def _freshness_findings(
    docs_app: Any,
    *,
    freshness_days: int,
    today: date,
) -> list[DocsQualityFinding]:
    findings: list[DocsQualityFinding] = []
    for node in docs_app.catalog.nodes:
        if not node.url.startswith(("/docs/operations/", "/docs/reference/")):
            continue
        owner = str(node.meta.get("owner") or "").strip()
        reviewed_raw = node.meta.get("reviewed_at")
        reviewed = None
        if reviewed_raw:
            with suppress(ValueError):
                reviewed = date.fromisoformat(str(reviewed_raw))
        problems: list[str] = []
        if not owner:
            problems.append("owner is missing")
        if reviewed is None:
            problems.append("reviewed_at is missing or not ISO YYYY-MM-DD")
        elif (today - reviewed).days > freshness_days:
            problems.append(
                f"reviewed_at is {(today - reviewed).days} days old (limit {freshness_days})"
            )
        if problems:
            findings.append(
                DocsQualityFinding(
                    kind="freshness",
                    target=node.url,
                    owner=owner or "docs-product",
                    recommended_page_type=_page_type(node.url),
                    message=f"{node.url}: {'; '.join(problems)}",
                    next_action="Assign an owner, review the page, and update reviewed_at.",
                    source_path=node.source_path,
                )
            )
    return findings


def build_docs_quality_report(
    docs_app: Any,
    *,
    documentation_roots: tuple[Path, ...],
    exemptions: tuple[dict[str, str], ...] = (),
    previous_inventory: dict[str, object] | None = None,
    freshness_days: int = 180,
    today: date | None = None,
) -> dict[str, object]:
    """Build the unified docs-quality report."""

    inventory = build_documentation_inventory(
        docs_app,
        documentation_roots=documentation_roots,
        previous=previous_inventory,
    )
    snippet_findings, snippet_summary = _snippet_findings(documentation_roots)
    findings = sorted(
        [
            *_broken_link_findings(docs_app.catalog),
            *_navigation_findings(docs_app),
            *_orphan_findings(docs_app),
            *_public_feature_findings(inventory),
            *snippet_findings,
            *_freshness_findings(
                docs_app,
                freshness_days=freshness_days,
                today=today or date.today(),
            ),
        ],
        key=lambda item: item.id,
    )
    by_id = {item.id: item for item in findings}
    exemption_by_id = {item["finding"]: item for item in exemptions}
    active = [item for item in findings if item.id not in exemption_by_id]
    applied = [
        {**exemption_by_id[item.id], "finding_detail": item.to_dict()}
        for item in findings
        if item.id in exemption_by_id
    ]
    unused = [item for item in exemptions if item["finding"] not in by_id]
    return {
        "schema_version": 1,
        "ok": not active and not unused,
        "summary": {
            "finding_count": len(findings),
            "active_count": len(active),
            "exempted_count": len(applied),
            "unused_exemption_count": len(unused),
        },
        "findings": [item.to_dict() for item in active],
        "exemptions": applied,
        "unused_exemptions": list(unused),
        "inventory_summary": inventory["summary"],
        "snippet_summary": snippet_summary,
    }
