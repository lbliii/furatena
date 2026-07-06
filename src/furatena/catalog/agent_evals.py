"""Deterministic golden-path evaluations for agent retrieval and tool use."""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from furatena.catalog.lifecycle import is_public_node


@dataclass(frozen=True, slots=True)
class AgentEvalExpectation:
    """Expected evidence for one deterministic agent eval case."""

    tool: str | None = None
    node_ids: tuple[str, ...] = ()
    citations: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class AgentEvalCase:
    """One fixture-style agent evaluation case."""

    id: str
    category: str
    prompt: str
    description: str
    expectation: AgentEvalExpectation


@dataclass(frozen=True, slots=True)
class AgentEvalResult:
    """Result for one agent evaluation case."""

    id: str
    category: str
    status: str
    message: str
    expectation: AgentEvalExpectation
    observed: dict[str, Any]

    @property
    def ok(self) -> bool:
        return self.status in {"pass", "skip"}

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "category": self.category,
            "status": self.status,
            "message": self.message,
            "expected": {
                "tool": self.expectation.tool,
                "node_ids": list(self.expectation.node_ids),
                "citations": list(self.expectation.citations),
            },
            "observed": self.observed,
        }


def run_agent_evaluations(
    docs_app: Any,
    *,
    include_private: bool = False,
    categories: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Run a lightweight deterministic eval suite through the Milo MCP adapter."""
    from milo.testing import MCPClient

    from furatena.catalog.mcp import FuraMCPServer, build_milo_cli

    selected = {item.strip() for item in categories or () if item.strip()}
    public_server = FuraMCPServer(docs_app, include_private=False)
    public_client = MCPClient(build_milo_cli(public_server))
    private_server = FuraMCPServer(docs_app, include_private=True)
    private_client = MCPClient(build_milo_cli(private_server))
    active_client = private_client if include_private else public_client

    cases = _build_cases(docs_app.catalog)
    results: list[AgentEvalResult] = []
    for case in cases:
        if selected and case.category not in selected and case.id not in selected:
            continue
        if case.id == "prose-doc-retrieval":
            results.append(_eval_prose_retrieval(case, active_client))
        elif case.id == "api-operation-discovery":
            results.append(_eval_api_operations(case, active_client))
        elif case.id == "private-content-boundary":
            results.append(_eval_private_boundary(case, public_client, private_client, include_private))
        elif case.id == "versioned-channel-discovery":
            results.append(_eval_channels(case, active_client))
        elif case.id == "stale-content-report":
            results.append(_eval_stale_report(case, active_client))
        elif case.id == "multi-mount-hub-discovery":
            results.append(_eval_multi_mounts(case, active_client))
        elif case.id in {"tool-selection-search", "tool-selection-author-edit"}:
            results.append(_eval_tool_selection(case, public_client))
        elif case.id == "author-draft-dry-run":
            results.append(_eval_author_draft_dry_run(case, private_client, include_private))
        elif case.id == "author-publish-dry-run":
            results.append(_eval_author_publish_dry_run(case, private_client, include_private))
        elif case.id == "author-publish-remediation":
            results.append(_eval_author_publish_remediation(case, private_client, include_private))
        elif case.id == "author-validation-repair":
            results.append(_eval_author_validation_repair(case, private_client, include_private))
        elif case.id == "author-publish-round-trip":
            results.append(
                _eval_author_publish_round_trip(case, public_client, private_client, include_private)
            )
    fail_count = sum(1 for result in results if result.status == "fail")
    skip_count = sum(1 for result in results if result.status == "skip")
    pass_count = sum(1 for result in results if result.status == "pass")
    return {
        "schema_version": 1,
        "ok": fail_count == 0,
        "case_count": len(results),
        "pass_count": pass_count,
        "fail_count": fail_count,
        "skip_count": skip_count,
        "include_private": include_private,
        "categories": sorted({result.category for result in results}),
        "results": [result.to_dict() for result in results],
    }


def _build_cases(catalog: Any) -> tuple[AgentEvalCase, ...]:
    public_nodes = [node for node in catalog.doc_nodes() if is_public_node(node)]
    prose_node = _pick_node(
        public_nodes,
        preferred_urls=(
            "/docs/get-started/installation/",
            "/docs/get-started/",
            "/chirp/docs/get-started/installation/",
        ),
    ) or (public_nodes[0] if public_nodes else None)
    api_node = next(
        (
            node
            for node in public_nodes
            if "api" in {tag.lower() for tag in node.tags} or node.meta.get("source") == "autodoc"
        ),
        None,
    )
    private_node = next(
        (
            node
            for node in catalog.doc_nodes()
            if not is_public_node(node)
        ),
        None,
    )
    stale_entries = catalog.author_stale_entries() if hasattr(catalog, "author_stale_entries") else []
    stale_slug = str(stale_entries[0]["slug"]) if stale_entries else ""

    return (
        AgentEvalCase(
            id="prose-doc-retrieval",
            category="prose_docs",
            prompt=_node_query(prose_node, fallback="Find the installation or first-run guide for this docs site."),
            description="Semantic retrieval returns the expected prose documentation node.",
            expectation=AgentEvalExpectation(
                tool="semantic_search",
                node_ids=tuple([prose_node.node_id] if prose_node is not None else []),
                citations=tuple([prose_node.url] if prose_node is not None else []),
            ),
        ),
        AgentEvalCase(
            id="api-operation-discovery",
            category="api_operations",
            prompt="Find an API operation or API-tagged reference entry.",
            description="The API operations resource exposes operation node ids for agents.",
            expectation=AgentEvalExpectation(
                tool="resource:fura://catalog/api-operations",
                node_ids=tuple([api_node.node_id] if api_node is not None else []),
                citations=tuple([api_node.url] if api_node is not None else []),
            ),
        ),
        AgentEvalCase(
            id="private-content-boundary",
            category="private_content",
            prompt="Verify private or draft docs are hidden from public agent retrieval.",
            description="Public MCP cannot retrieve private nodes, while include-private author MCP can.",
            expectation=AgentEvalExpectation(
                tool="retrieve_node",
                node_ids=tuple([private_node.node_id] if private_node is not None else []),
                citations=tuple([private_node.url] if private_node is not None else []),
            ),
        ),
        AgentEvalCase(
            id="versioned-channel-discovery",
            category="versioned_content",
            prompt="Inspect version channel metadata for mounted documentation.",
            description="Channel resource exposes active and configured version channels.",
            expectation=AgentEvalExpectation(tool="resource:fura://catalog/channels"),
        ),
        AgentEvalCase(
            id="stale-content-report",
            category="stale_content",
            prompt="Explain stale author content and impacted refresh targets.",
            description="Stale-impact tool returns structured entries or a clean zero-stale report.",
            expectation=AgentEvalExpectation(
                tool="explain_stale_impact",
                citations=tuple([stale_slug] if stale_slug else ()),
            ),
        ),
        AgentEvalCase(
            id="multi-mount-hub-discovery",
            category="multi_mount_hubs",
            prompt="Discover all mounted documentation hubs.",
            description="Source-health tool returns mount ids and page counts for each hub.",
            expectation=AgentEvalExpectation(tool="inspect_source_health"),
        ),
        AgentEvalCase(
            id="tool-selection-search",
            category="tool_selection",
            prompt="I need to find docs by natural language query.",
            description="Tool metadata lets an agent select semantic_search for retrieval.",
            expectation=AgentEvalExpectation(tool="semantic_search"),
        ),
        AgentEvalCase(
            id="tool-selection-author-edit",
            category="tool_selection",
            prompt="I need to preview an exact source edit before writing files.",
            description="Tool metadata lets an agent select author_propose_edit for non-mutating edit previews.",
            expectation=AgentEvalExpectation(tool="author_propose_edit"),
        ),
        AgentEvalCase(
            id="author-draft-dry-run",
            category="author_workflows",
            prompt="Create a draft page only after previewing the write.",
            description="Author draft creation returns a dry-run result without writing source files.",
            expectation=AgentEvalExpectation(tool="author_create_draft"),
        ),
        AgentEvalCase(
            id="author-publish-dry-run",
            category="author_workflows",
            prompt="Preview publishing a private draft without changing public output.",
            description="Author publish defaults to dry-run and reports publication impact.",
            expectation=AgentEvalExpectation(
                tool="author_publish",
                node_ids=tuple([private_node.node_id] if private_node is not None else []),
                citations=tuple([_author_target(private_node)] if private_node is not None else []),
            ),
        ),
        AgentEvalCase(
            id="author-publish-remediation",
            category="author_workflows",
            prompt="Recover from a failed publish attempt with diagnostics and a reviewed repair preview.",
            description="Failed publish remediation stays non-mutating and proposes exact source edits.",
            expectation=AgentEvalExpectation(
                tool="author_publish",
                node_ids=tuple([private_node.node_id] if private_node is not None else []),
                citations=tuple([_author_target(private_node)] if private_node is not None else []),
            ),
        ),
        AgentEvalCase(
            id="author-validation-repair",
            category="author_workflows",
            prompt="Fix a lifecycle validation error with a previewed source edit, then validate clean.",
            description="Author validation repair creates a scoped lifecycle error, previews a repair, applies it, and restores clean validation.",
            expectation=AgentEvalExpectation(
                tool="author_validate",
                node_ids=tuple([private_node.node_id] if private_node is not None else []),
                citations=tuple([_author_target(private_node)] if private_node is not None else []),
            ),
        ),
        AgentEvalCase(
            id="author-publish-round-trip",
            category="author_workflows",
            prompt="Publish a reviewed draft, verify public retrieval, then unpublish it safely.",
            description="Confirmed author publish/unpublish keeps public retrieval boundaries current.",
            expectation=AgentEvalExpectation(
                tool="author_publish",
                node_ids=tuple([private_node.node_id] if private_node is not None else []),
                citations=tuple([_author_target(private_node)] if private_node is not None else []),
            ),
        ),
    )


def _eval_prose_retrieval(case: AgentEvalCase, client: Any) -> AgentEvalResult:
    expected_ids = set(case.expectation.node_ids)
    if not expected_ids:
        return _skip(case, "catalog has no public prose docs to evaluate")
    expected_id = next(iter(expected_ids))
    mount = expected_id.split(":", 1)[0] if ":" in expected_id else ""
    result = client.call(case.expectation.tool, query=case.prompt, limit=8, mount=mount)
    payload = result.structured
    observed_ids = [str(item.get("node_id")) for item in payload.get("results", [])]
    if expected_ids.intersection(observed_ids):
        return _pass(
            case,
            "expected prose node appeared in semantic search results",
            {"node_ids": observed_ids, "mount": mount},
        )
    return _fail(
        case,
        "expected prose node was not returned by semantic_search",
        {"node_ids": observed_ids, "mount": mount},
    )


def _eval_api_operations(case: AgentEvalCase, client: Any) -> AgentEvalResult:
    expected_ids = set(case.expectation.node_ids)
    if not expected_ids:
        return _skip(case, "catalog has no API-tagged or autodoc operation nodes")
    payload = _read_json_resource(client, "fura://catalog/api-operations")
    operations = payload.get("operations", []) if isinstance(payload, dict) else []
    observed_ids = [str(item.get("node_id")) for item in operations]
    if expected_ids.intersection(observed_ids):
        return _pass(case, "expected API operation node appeared in the API operations resource", {"node_ids": observed_ids})
    return _fail(case, "expected API operation node was missing from the API operations resource", {"node_ids": observed_ids})


def _eval_private_boundary(
    case: AgentEvalCase,
    public_client: Any,
    private_client: Any,
    include_private: bool,
) -> AgentEvalResult:
    expected_ids = set(case.expectation.node_ids)
    if not expected_ids:
        return _skip(case, "catalog has no private, draft, internal, or archived docs")
    if not include_private:
        return _skip(case, "rerun with --include-private to exercise the private author MCP path")
    node_id = next(iter(expected_ids))
    public_result = public_client.call("retrieve_node", node_id=node_id)
    private_result = private_client.call("retrieve_node", node_id=node_id)
    observed = {
        "public_is_error": public_result.is_error,
        "private_is_error": private_result.is_error,
        "private_node_id": private_result.structured.get("node_id") if private_result.structured else None,
    }
    if public_result.is_error and not private_result.is_error and observed["private_node_id"] == node_id:
        return _pass(case, "private node is blocked publicly and retrievable in include-private mode", observed)
    return _fail(case, "private content boundary did not behave as expected", observed)


def _eval_channels(case: AgentEvalCase, client: Any) -> AgentEvalResult:
    payload = _read_json_resource(client, "fura://catalog/channels")
    mounts = payload.get("mounts", []) if isinstance(payload, dict) else []
    channel_count = sum(len(item.get("channels") or []) for item in mounts)
    observed = {
        "active_channel": payload.get("active_channel") if isinstance(payload, dict) else None,
        "mount_count": len(mounts),
        "channel_count": channel_count,
    }
    if observed["active_channel"] and channel_count >= 1:
        return _pass(case, "channel resource exposed active version metadata", observed)
    return _fail(case, "channel resource did not expose active version metadata", observed)


def _eval_stale_report(case: AgentEvalCase, client: Any) -> AgentEvalResult:
    slug = case.expectation.citations[0] if case.expectation.citations else ""
    result = client.call("explain_stale_impact", slug=slug)
    payload = result.structured
    groups = payload.get("groups", {}) if isinstance(payload, dict) else {}
    observed = {
        "stale_count": payload.get("stale_count"),
        "entry_count": len(payload.get("entries", [])),
        "impact_count": len(payload.get("impact", [])),
        "grouped_dimensions": sorted(groups) if isinstance(groups, dict) else [],
    }
    if isinstance(observed["stale_count"], int):
        return _pass(case, "stale-impact tool returned a structured stale report", observed)
    return _fail(case, "stale-impact tool did not return a structured stale report", observed)


def _eval_multi_mounts(case: AgentEvalCase, client: Any) -> AgentEvalResult:
    result = client.call("inspect_source_health")
    payload = result.structured
    mounts = payload.get("mounts", [])
    observed = {
        "mount_count": payload.get("mount_count"),
        "mount_ids": [item.get("id") for item in mounts],
    }
    if int(payload.get("mount_count") or 0) >= 2:
        return _pass(case, "source-health tool exposed multiple mounted hubs", observed)
    return _skip(case, "catalog has fewer than two mounted hubs")


def _eval_tool_selection(case: AgentEvalCase, client: Any) -> AgentEvalResult:
    tools = client.list_tools()
    observed_names = [tool.name for tool in tools]
    expected = case.expectation.tool
    if expected in observed_names:
        return _pass(case, f"expected tool {expected} is available for selection", {"tool_names": observed_names})
    return _fail(case, f"expected tool {expected} was missing from MCP tool metadata", {"tool_names": observed_names})


def _eval_author_draft_dry_run(case: AgentEvalCase, client: Any, include_private: bool) -> AgentEvalResult:
    if not include_private:
        return _skip(case, "rerun with --include-private to exercise author workflow evals")
    slug = "__fura-agent-eval-draft-preview"
    result = client.call(
        "author_create_draft",
        slug=slug,
        title="Fura Agent Eval Draft Preview",
    )
    payload = _structured(result)
    observed = {
        "is_error": _milo_failed(result),
        "ok": payload.get("ok"),
        "dry_run": payload.get("dry_run"),
        "changed_files": payload.get("changed_files"),
        "resulting_visibility": payload.get("resulting_visibility"),
        "diagnostics": payload.get("diagnostics", []),
    }
    if _milo_failed(result) and _diagnostic_mentions(payload, "target already exists"):
        return _skip(case, "reserved eval draft slug already exists", observed)
    if (
        not _milo_failed(result)
        and payload.get("ok") is True
        and payload.get("dry_run") is True
        and payload.get("changed_files") == []
        and payload.get("resulting_visibility") == "draft"
    ):
        return _pass(case, "draft creation preview was structured and non-mutating", observed)
    return _fail(case, "draft dry-run did not return the expected non-mutating result", observed)


def _eval_author_publish_dry_run(case: AgentEvalCase, client: Any, include_private: bool) -> AgentEvalResult:
    if not include_private:
        return _skip(case, "rerun with --include-private to exercise author workflow evals")
    target = _expected_author_target(case)
    if not target:
        return _skip(case, "catalog has no private author target to publish-preview")
    result = client.call("author_publish", target=target)
    payload = _structured(result)
    impact = payload.get("publication_impact") if isinstance(payload, dict) else {}
    observed = {
        "target": target,
        "is_error": _milo_failed(result),
        "ok": payload.get("ok"),
        "dry_run": payload.get("dry_run"),
        "changed_files": payload.get("changed_files"),
        "resulting_visibility": payload.get("resulting_visibility"),
        "publication_change": impact.get("change") if isinstance(impact, dict) else None,
        "affected_surfaces": impact.get("affected_surfaces") if isinstance(impact, dict) else None,
    }
    if (
        not _milo_failed(result)
        and payload.get("ok") is True
        and payload.get("dry_run") is True
        and payload.get("changed_files") == []
        and payload.get("resulting_visibility") == "public"
        and isinstance(impact, dict)
        and impact.get("resulting_public") is True
    ):
        return _pass(case, "publish preview reported public impact without writing files", observed)
    return _fail(case, "publish dry-run did not return expected publication-impact metadata", observed)


def _eval_author_publish_remediation(case: AgentEvalCase, client: Any, include_private: bool) -> AgentEvalResult:
    if not include_private:
        return _skip(case, "rerun with --include-private to exercise author workflow evals")
    target = _expected_author_target(case)
    if not target:
        return _skip(case, "catalog has no private author target for publish remediation")

    source_before = client.call("author_read_source", target=target)
    failed_publish = client.call("author_publish", target=target, dry_run=False)
    source_after = client.call("author_read_source", target=target)
    validation = client.call("author_validate", target=target)
    if source_before.is_error or source_after.is_error:
        return _fail(
            case,
            "remediation eval could not read target source",
            {
                "target": target,
                "read_before_error": _structured(source_before),
                "read_after_error": _structured(source_after),
            },
        )
    before_source = str(_structured(source_before).get("source") or "")
    after_source = str(_structured(source_after).get("source") or "")
    span = _first_heading_span(before_source)
    if not span:
        return _skip(case, "target source has no heading span for a remediation preview")
    repair_preview = client.call(
        "author_propose_edit",
        target=target,
        old_text=span,
        new_text=f"{span}\nReviewed remediation note.\n",
    )
    observed = {
        "target": target,
        "failed_publish_is_error": _milo_failed(failed_publish),
        "failed_publish_diagnostics": _structured(failed_publish).get("diagnostics", []),
        "source_unchanged_after_failed_publish": before_source == after_source,
        "validation_is_error": _milo_failed(validation),
        "validation_has_audit": "audit" in _structured(validation),
        "repair_preview_is_error": _milo_failed(repair_preview),
        "repair_preview_dry_run": _structured(repair_preview).get("dry_run"),
        "repair_preview_changed_files": _structured(repair_preview).get("changed_files"),
    }
    if (
        _milo_failed(failed_publish)
        and before_source == after_source
        and "audit" in _structured(validation)
        and not _milo_failed(repair_preview)
        and _structured(repair_preview).get("dry_run") is True
        and _structured(repair_preview).get("changed_files") == []
    ):
        return _pass(case, "failed publish remediation stayed diagnostics-first and non-mutating", observed)
    return _fail(case, "failed publish remediation did not preserve the expected safety gates", observed)


def _eval_author_validation_repair(case: AgentEvalCase, client: Any, include_private: bool) -> AgentEvalResult:
    if not include_private:
        return _skip(case, "rerun with --include-private to exercise author workflow evals")
    target = _expected_author_target(case)
    if not target:
        return _skip(case, "catalog has no private author target for validation repair")

    source_before = client.call("author_read_source", target=target)
    if source_before.is_error:
        return _fail(
            case,
            "validation repair eval could not read target source",
            {"target": target, "read_error": _structured(source_before)},
        )
    original_source = str(_structured(source_before).get("source") or "")
    source_revision = str(_structured(source_before).get("source_revision") or "")
    source_target = str(_structured(source_before).get("target_path") or target)
    clean_span, invalid_span = _validation_repair_spans(original_source)
    if not clean_span or not invalid_span:
        return _skip(case, "target source has no lifecycle frontmatter span suitable for repair")

    introduce_error = client.call(
        "author_apply_edit",
        target=source_target,
        old_text=clean_span,
        new_text=invalid_span,
        source_revision=source_revision,
        dry_run=False,
        confirmed=True,
    )
    invalid_validation = client.call("author_validate", target=source_target)
    repair_preview = client.call(
        "author_propose_edit",
        target=source_target,
        old_text=invalid_span,
        new_text=clean_span,
    )
    repair_apply = client.call(
        "author_apply_edit",
        target=source_target,
        old_text=invalid_span,
        new_text=clean_span,
        source_revision=str(_structured(introduce_error).get("source_revision") or ""),
        dry_run=False,
        confirmed=True,
    )
    clean_validation = client.call("author_validate", target=source_target)
    source_after = client.call("author_read_source", target=source_target)

    restore_payload: dict[str, Any] = {}
    after_source = str(_structured(source_after).get("source") or "")
    if original_source and after_source and after_source != original_source:
        restore = client.call(
            "author_apply_edit",
            target=source_target,
            old_text=after_source,
            new_text=original_source,
            source_revision=str(_structured(source_after).get("source_revision") or ""),
            dry_run=False,
            confirmed=True,
        )
        restore_payload = {
            "restore_is_error": _milo_failed(restore),
            "restore_changed_files": _structured(restore).get("changed_files"),
        }

    invalid_payload = _structured(invalid_validation)
    clean_payload = _structured(clean_validation)
    observed = {
        "target": target,
        "source_target": source_target,
        "introduce_error_is_error": _milo_failed(introduce_error),
        "introduce_error_changed_files": _structured(introduce_error).get("changed_files"),
        "invalid_validation_is_error": _milo_failed(invalid_validation),
        "invalid_error_count": invalid_payload.get("error_count"),
        "invalid_errors": invalid_payload.get("errors", []),
        "invalid_diagnostics": invalid_payload.get("diagnostics", []),
        "repair_preview_is_error": _milo_failed(repair_preview),
        "repair_preview_dry_run": _structured(repair_preview).get("dry_run"),
        "repair_preview_changed_files": _structured(repair_preview).get("changed_files"),
        "repair_apply_is_error": _milo_failed(repair_apply),
        "repair_apply_changed_files": _structured(repair_apply).get("changed_files"),
        "clean_validation_is_error": _milo_failed(clean_validation),
        "clean_error_count": clean_payload.get("error_count"),
        "source_restored": after_source == original_source,
        **restore_payload,
    }
    if (
        not _milo_failed(introduce_error)
        and _milo_failed(invalid_validation)
        and int(invalid_payload.get("error_count") or 0) >= 1
        and not _milo_failed(repair_preview)
        and _structured(repair_preview).get("dry_run") is True
        and _structured(repair_preview).get("changed_files") == []
        and not _milo_failed(repair_apply)
        and not _milo_failed(clean_validation)
        and clean_payload.get("error_count") == 0
        and observed.get("restore_is_error") is not True
        and after_source == original_source
    ):
        return _pass(case, "validation repair failed, previewed, applied, and validated clean", observed)
    return _fail(case, "validation repair did not complete the expected safe repair loop", observed)


def _eval_author_publish_round_trip(
    case: AgentEvalCase,
    public_client: Any,
    private_client: Any,
    include_private: bool,
) -> AgentEvalResult:
    if not include_private:
        return _skip(case, "rerun with --include-private to exercise author workflow evals")
    target = _expected_author_target(case)
    node_id = next((item for item in case.expectation.node_ids if item), "")
    if not target or not node_id:
        return _skip(case, "catalog has no private author target for publish round-trip")

    source_before = private_client.call("author_read_source", target=target)
    if source_before.is_error:
        return _fail(
            case,
            "publish round-trip could not read target source",
            {"target": target, "read_error": _structured(source_before)},
        )
    original_source = str(_structured(source_before).get("source") or "")
    source_revision = str(_structured(source_before).get("source_revision") or "")
    public_before = public_client.call("retrieve_node", node_id=node_id)
    publish = private_client.call(
        "author_publish",
        target=target,
        source_revision=source_revision,
        dry_run=False,
        confirmed=True,
    )
    public_after_publish = public_client.call("retrieve_node", node_id=node_id)
    unpublish = private_client.call(
        "author_unpublish",
        target=target,
        source_revision=str(_structured(publish).get("source_revision") or ""),
        dry_run=False,
        confirmed=True,
    )
    public_after_unpublish = public_client.call("retrieve_node", node_id=node_id)

    restore_payload: dict[str, Any] = {}
    source_after_unpublish = private_client.call("author_read_source", target=target)
    if not source_after_unpublish.is_error:
        round_trip_source = str(_structured(source_after_unpublish).get("source") or "")
        if original_source and round_trip_source and round_trip_source != original_source:
            restore = private_client.call(
                "author_apply_edit",
                target=target,
                old_text=round_trip_source,
                new_text=original_source,
                source_revision=str(
                    _structured(source_after_unpublish).get("source_revision") or ""
                ),
                dry_run=False,
                confirmed=True,
            )
            restore_payload = {
                "restore_is_error": _milo_failed(restore),
                "restore_changed_files": _structured(restore).get("changed_files"),
            }

    observed = {
        "target": target,
        "node_id": node_id,
        "public_before_is_error": public_before.is_error,
        "publish_is_error": _milo_failed(publish),
        "publish_resulting_visibility": _structured(publish).get("resulting_visibility"),
        "public_after_publish_is_error": public_after_publish.is_error,
        "public_after_publish_node_id": _structured(public_after_publish).get("node_id"),
        "unpublish_is_error": _milo_failed(unpublish),
        "unpublish_resulting_visibility": _structured(unpublish).get("resulting_visibility"),
        "public_after_unpublish_is_error": public_after_unpublish.is_error,
        **restore_payload,
    }
    if (
        public_before.is_error
        and not _milo_failed(publish)
        and _structured(publish).get("resulting_visibility") == "public"
        and not public_after_publish.is_error
        and _structured(public_after_publish).get("node_id") == node_id
        and not _milo_failed(unpublish)
        and _structured(unpublish).get("resulting_visibility") == "draft"
        and public_after_unpublish.is_error
        and observed.get("restore_is_error") is not True
    ):
        return _pass(
            case,
            "publish/unpublish round-trip updated public retrieval boundaries and restored source",
            observed,
        )
    return _fail(case, "publish/unpublish round-trip did not preserve retrieval boundaries", observed)


def _read_json_resource(client: Any, uri: str) -> dict[str, Any]:
    payload = client.read_resource(uri)
    if not isinstance(payload, dict):
        return {}
    if "contents" not in payload:
        return payload
    contents = payload.get("contents") or []
    if not contents:
        return {}
    text = contents[0].get("text") if isinstance(contents[0], dict) else None
    if not isinstance(text, str):
        return {}
    try:
        decoded = json.loads(text)
    except json.JSONDecodeError:
        return {}
    return decoded if isinstance(decoded, dict) else {}


def _structured(result: Any) -> dict[str, Any]:
    payload = getattr(result, "structured", None)
    return payload if isinstance(payload, dict) else {}


def _milo_failed(result: Any) -> bool:
    payload = _structured(result)
    return bool(getattr(result, "is_error", False) or payload.get("ok") is False)


def _pick_node(nodes: list[Any], *, preferred_urls: tuple[str, ...]) -> Any | None:
    for url in preferred_urls:
        for node in nodes:
            if node.url == url:
                return node
    return None


def _node_query(node: Any | None, *, fallback: str) -> str:
    if node is None:
        return fallback
    parts = [str(node.title or "").strip(), str(node.description or "").strip(), str(node.url or "").strip()]
    query = " ".join(part for part in parts if part)
    return query or fallback


def _author_target(node: Any | None) -> str:
    if node is None:
        return ""
    return str(getattr(node, "slug", "") or getattr(node, "source_path", "") or "").strip()


def _expected_author_target(case: AgentEvalCase) -> str:
    return next((item for item in case.expectation.citations if item), "")


def _diagnostic_mentions(payload: dict[str, Any], needle: str) -> bool:
    return any(needle in str(item.get("message") or "") for item in payload.get("diagnostics", []))


def _first_heading_span(source: str) -> str:
    for line in source.splitlines(keepends=True):
        if line.startswith("# "):
            return line
    return ""


def _validation_repair_spans(source: str) -> tuple[str, str]:
    if "draft: true\n" in source:
        return "draft: true\n", "draft: true\npublished_at: 2026-01-01T00:00:00Z\n"
    if "visibility: private\n" in source:
        return "visibility: private\n", "visibility: private\npublished_at: 2026-01-01T00:00:00Z\n"
    if "visibility: draft\n" in source:
        return "visibility: draft\n", "visibility: draft\npublished_at: 2026-01-01T00:00:00Z\n"
    if "visibility: internal\n" in source:
        return "visibility: internal\n", "visibility: internal\npublished_at: 2026-01-01T00:00:00Z\n"
    return "", ""


def _pass(case: AgentEvalCase, message: str, observed: dict[str, Any]) -> AgentEvalResult:
    return AgentEvalResult(case.id, case.category, "pass", message, case.expectation, observed)


def _fail(case: AgentEvalCase, message: str, observed: dict[str, Any]) -> AgentEvalResult:
    return AgentEvalResult(case.id, case.category, "fail", message, case.expectation, observed)


def _skip(case: AgentEvalCase, message: str, observed: dict[str, Any] | None = None) -> AgentEvalResult:
    return AgentEvalResult(case.id, case.category, "skip", message, case.expectation, observed or {})
