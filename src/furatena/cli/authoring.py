"""Author lifecycle operations for ``fura author``."""

from __future__ import annotations

import difflib
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from furatena.catalog.lifecycle import is_public_meta, visibility_state
from furatena.catalog.sources.parse import parse_source_text


@dataclass(frozen=True, slots=True)
class AuthorDiagnostic:
    severity: str
    message: str
    rule_id: str = "fura.author"
    source_path: str | None = None
    next_action: str | None = None


@dataclass(frozen=True, slots=True)
class AuthorOperationResult:
    operation_id: str
    operation: str
    ok: bool
    target_path: Path | None
    mount: str | None
    previous_visibility: str | None
    resulting_visibility: str | None
    changed_files: tuple[Path, ...] = ()
    diagnostics: tuple[AuthorDiagnostic, ...] = ()
    dry_run: bool = False
    confirmed: bool = False
    diff: str | None = None
    next_actions: tuple[str, ...] = ()
    publication_impact: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "operation_id": self.operation_id,
            "operation": self.operation,
            "ok": self.ok,
            "target_path": self.target_path,
            "mount": self.mount,
            "previous_visibility": self.previous_visibility,
            "resulting_visibility": self.resulting_visibility,
            "changed_files": list(self.changed_files),
            "diagnostics": [
                {
                    "severity": diagnostic.severity,
                    "message": diagnostic.message,
                    "rule_id": diagnostic.rule_id,
                    "source_path": diagnostic.source_path,
                    "next_action": diagnostic.next_action,
                }
                for diagnostic in self.diagnostics
            ],
            "dry_run": self.dry_run,
            "confirmed": self.confirmed,
            "diff": self.diff,
            "next_actions": list(self.next_actions),
        }
        if self.publication_impact is not None:
            payload["publication_impact"] = self.publication_impact
        return payload


@dataclass(frozen=True, slots=True)
class ResolvedAuthorTarget:
    path: Path
    mount_id: str
    content_root: Path
    source_format: str


def author_status(target: str, *, mounts: tuple[Any, ...], mount_id: str | None = None) -> AuthorOperationResult:
    resolved = _resolve_existing_target(target, mounts=mounts, mount_id=mount_id)
    if isinstance(resolved, AuthorOperationResult):
        return resolved
    meta, _body = _read_source(resolved)
    return AuthorOperationResult(
        operation_id=_operation_id("status"),
        operation="status",
        ok=True,
        target_path=resolved.path,
        mount=resolved.mount_id,
        previous_visibility=visibility_state(meta),
        resulting_visibility=visibility_state(meta),
        next_actions=(
            "Run fura author draft|publish|unpublish|archive to change lifecycle state.",
        ),
    )


def author_validate(
    target: str,
    *,
    mounts: tuple[Any, ...],
    mount_id: str | None = None,
    validation_errors: tuple[str, ...] = (),
    validation_warnings: tuple[str, ...] = (),
) -> AuthorOperationResult:
    operation = "validate"
    resolved = _resolve_existing_target(target, mounts=mounts, mount_id=mount_id, operation=operation)
    if isinstance(resolved, AuthorOperationResult):
        return resolved

    source = resolved.path.read_text(encoding="utf-8")
    meta, _body = _validate_source_text(
        source,
        content_format=resolved.source_format,
        operation=operation,
        target_path=resolved.path,
        mount=resolved.mount_id,
    )
    if isinstance(meta, AuthorOperationResult):
        return meta

    diagnostics = tuple(
        _validation_diagnostic(message, severity, resolved.path)
        for severity, messages in (("error", validation_errors), ("warning", validation_warnings))
        for message in messages
        if _validation_message_applies(message, resolved.path)
    )
    ok = not any(diagnostic.severity == "error" for diagnostic in diagnostics)
    visibility = visibility_state(meta)
    return AuthorOperationResult(
        operation_id=_operation_id(operation),
        operation=operation,
        ok=ok,
        target_path=resolved.path,
        mount=resolved.mount_id,
        previous_visibility=visibility,
        resulting_visibility=visibility,
        diagnostics=diagnostics,
        next_actions=(
            ("Fix the reported validation errors and rerun fura author validate.",)
            if not ok
            else ("Run fura author publish --dry-run to inspect publication impact.",)
        ),
    )


def author_read_source(
    target: str,
    *,
    mounts: tuple[Any, ...],
    mount_id: str | None = None,
) -> tuple[AuthorOperationResult, str | None]:
    resolved = _resolve_existing_target(target, mounts=mounts, mount_id=mount_id, operation="read")
    if isinstance(resolved, AuthorOperationResult):
        return resolved, None
    meta, _body = _read_source(resolved)
    result = AuthorOperationResult(
        operation_id=_operation_id("read"),
        operation="read",
        ok=True,
        target_path=resolved.path,
        mount=resolved.mount_id,
        previous_visibility=visibility_state(meta),
        resulting_visibility=visibility_state(meta),
        next_actions=("Use author_propose_edit before applying a source edit.",),
    )
    return result, resolved.path.read_text(encoding="utf-8")


def author_new(
    slug: str,
    *,
    mounts: tuple[Any, ...],
    mount_id: str | None = None,
    title: str | None = None,
    dry_run: bool = False,
    confirmed: bool = False,
) -> AuthorOperationResult:
    operation = "new"
    selected = _select_mount(mounts, mount_id=mount_id)
    if isinstance(selected, AuthorOperationResult):
        return selected
    slug_path = _normalize_slug(slug)
    if not slug_path:
        return _failed(
            operation,
            "author new requires a non-empty slug",
            next_action="Pass a page slug such as docs/new-page.",
        )
    target = (selected.content_root / f"{slug_path}.md").resolve()
    if not _is_relative_to(target, selected.content_root):
        return _failed(
            operation,
            "target slug escapes the selected content root",
            target_path=target,
            mount=selected.id,
            next_action="Use a slug below the selected mount content root.",
        )
    if target.exists():
        return _failed(
            operation,
            "target already exists",
            target_path=target,
            mount=selected.id,
            next_action="Choose a new slug or run fura author status on the existing page.",
        )
    if not dry_run and not confirmed:
        return _confirmation_required(operation, target_path=target, mount=selected.id)

    page_title = title or slug_path.rsplit("/", 1)[-1].replace("-", " ").replace("_", " ").title()
    new_body = _compose_source(
        {
            "title": page_title,
            "draft": True,
            "visibility": "draft",
            "updated_at": _now_iso(),
        },
        f"# {page_title}\n",
    )
    diff = _diff("", new_body, fromfile="/dev/null", tofile=str(target))
    changed = () if dry_run else (target,)
    if not dry_run:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(new_body, encoding="utf-8")

    return AuthorOperationResult(
        operation_id=_operation_id(operation),
        operation=operation,
        ok=True,
        target_path=target,
        mount=selected.id,
        previous_visibility=None,
        resulting_visibility="draft",
        changed_files=changed,
        dry_run=dry_run,
        confirmed=confirmed,
        diff=diff,
        next_actions=(
            "Preview locally with fura serve --author.",
            "Run fura author publish when the page is ready for public output.",
        ),
    )


def author_apply_edit(
    target: str,
    *,
    mounts: tuple[Any, ...],
    old_text: str,
    new_text: str,
    mount_id: str | None = None,
    dry_run: bool = False,
    confirmed: bool = False,
) -> AuthorOperationResult:
    operation = "apply_edit"
    resolved = _resolve_existing_target(target, mounts=mounts, mount_id=mount_id, operation=operation)
    if isinstance(resolved, AuthorOperationResult):
        return resolved
    meta, _body = _read_source(resolved)
    old_source = resolved.path.read_text(encoding="utf-8")
    if not old_text:
        return _failed(
            operation,
            "author apply_edit requires non-empty old_text",
            target_path=resolved.path,
            mount=resolved.mount_id,
            next_action="Read the source first and pass the exact text span to replace.",
        )
    if old_text not in old_source:
        return _failed(
            operation,
            "old_text was not found in the source",
            target_path=resolved.path,
            mount=resolved.mount_id,
            next_action="Rerun author_read_source and retry with an exact source span.",
        )
    if old_text == new_text:
        return AuthorOperationResult(
            operation_id=_operation_id(operation),
            operation=operation,
            ok=True,
            target_path=resolved.path,
            mount=resolved.mount_id,
            previous_visibility=visibility_state(meta),
            resulting_visibility=visibility_state(meta),
            dry_run=dry_run,
            confirmed=confirmed,
            diff="",
            next_actions=("No source changes were needed.",),
        )
    if old_source.count(old_text) != 1:
        return _failed(
            operation,
            "old_text matches multiple source spans",
            target_path=resolved.path,
            mount=resolved.mount_id,
            next_action="Pass a larger exact source span that uniquely identifies the edit.",
        )
    new_source = old_source.replace(old_text, new_text, 1)
    diff = _diff(old_source, new_source, fromfile=str(resolved.path), tofile=str(resolved.path))
    if not dry_run and not confirmed:
        return _confirmation_required(operation, target_path=resolved.path, mount=resolved.mount_id)
    if not dry_run:
        resolved.path.write_text(new_source, encoding="utf-8")
    return AuthorOperationResult(
        operation_id=_operation_id(operation),
        operation=operation,
        ok=True,
        target_path=resolved.path,
        mount=resolved.mount_id,
        previous_visibility=visibility_state(meta),
        resulting_visibility=visibility_state(meta),
        changed_files=() if dry_run else (resolved.path,),
        dry_run=dry_run,
        confirmed=confirmed,
        diff=diff,
        next_actions=(
            "Run fura author status on the edited target.",
            "Run fura check --content-only --json before publishing.",
        ),
    )


def author_save_source(
    target: str,
    *,
    mounts: tuple[Any, ...],
    source_text: str,
    mount_id: str | None = None,
    dry_run: bool = False,
    confirmed: bool = False,
) -> AuthorOperationResult:
    operation = "save_source"
    resolved = _resolve_existing_target(target, mounts=mounts, mount_id=mount_id, operation=operation)
    if isinstance(resolved, AuthorOperationResult):
        return resolved
    old_source = resolved.path.read_text(encoding="utf-8")
    meta, _body = _validate_source_text(
        source_text,
        content_format=resolved.source_format,
        operation=operation,
        target_path=resolved.path,
        mount=resolved.mount_id,
    )
    if isinstance(meta, AuthorOperationResult):
        return meta
    old_meta, _old_body = _read_source(resolved)
    if old_source == source_text:
        return AuthorOperationResult(
            operation_id=_operation_id(operation),
            operation=operation,
            ok=True,
            target_path=resolved.path,
            mount=resolved.mount_id,
            previous_visibility=visibility_state(old_meta),
            resulting_visibility=visibility_state(meta),
            dry_run=dry_run,
            confirmed=confirmed,
            diff="",
            next_actions=("No source changes were needed.",),
        )
    diff = _diff(old_source, source_text, fromfile=str(resolved.path), tofile=str(resolved.path))
    if not dry_run and not confirmed:
        return _confirmation_required(operation, target_path=resolved.path, mount=resolved.mount_id)
    if not dry_run:
        resolved.path.write_text(source_text, encoding="utf-8")
    return AuthorOperationResult(
        operation_id=_operation_id(operation),
        operation=operation,
        ok=True,
        target_path=resolved.path,
        mount=resolved.mount_id,
        previous_visibility=visibility_state(old_meta),
        resulting_visibility=visibility_state(meta),
        changed_files=() if dry_run else (resolved.path,),
        dry_run=dry_run,
        confirmed=confirmed,
        diff=diff,
        next_actions=(
            "Preview locally with fura serve --author.",
            "Run fura check --content-only --json before publishing.",
        ),
    )


def author_transition(
    operation: str,
    target: str,
    *,
    mounts: tuple[Any, ...],
    mount_id: str | None = None,
    dry_run: bool = False,
    confirmed: bool = False,
) -> AuthorOperationResult:
    resolved = _resolve_existing_target(target, mounts=mounts, mount_id=mount_id, operation=operation)
    if isinstance(resolved, AuthorOperationResult):
        return resolved
    meta, body = _read_source(resolved)
    previous_visibility = visibility_state(meta)
    new_meta = dict(meta)

    if operation == "draft":
        new_meta["draft"] = True
        new_meta["visibility"] = "draft"
        new_meta.pop("published_at", None)
        new_meta.pop("archived_at", None)
        resulting_visibility = "draft"
    elif operation == "publish":
        new_meta.pop("draft", None)
        new_meta["visibility"] = "public"
        new_meta.pop("archived_at", None)
        new_meta.setdefault("published_at", _now_iso())
        resulting_visibility = "public"
    elif operation == "unpublish":
        new_meta["draft"] = True
        new_meta["visibility"] = "draft"
        new_meta.pop("published_at", None)
        new_meta.pop("archived_at", None)
        resulting_visibility = "draft"
    elif operation == "archive":
        new_meta.pop("draft", None)
        new_meta["visibility"] = "archived"
        new_meta.pop("published_at", None)
        new_meta.setdefault("archived_at", _now_iso())
        resulting_visibility = "archived"
    else:
        return _failed(operation, f"unknown author operation: {operation}")

    new_meta["updated_at"] = _now_iso()
    old_source = resolved.path.read_text(encoding="utf-8")
    new_source = _compose_source(new_meta, body)
    changed = old_source != new_source
    diff = _diff(old_source, new_source, fromfile=str(resolved.path), tofile=str(resolved.path))
    publication_impact = _publication_impact(
        previous_meta=meta,
        resulting_meta=new_meta,
        operation=operation,
    )

    if not changed:
        return AuthorOperationResult(
            operation_id=_operation_id(operation),
            operation=operation,
            ok=True,
            target_path=resolved.path,
            mount=resolved.mount_id,
            previous_visibility=previous_visibility,
            resulting_visibility=resulting_visibility,
            dry_run=dry_run,
            confirmed=confirmed,
            diff="",
            next_actions=("No source changes were needed.",),
            publication_impact=publication_impact,
        )
    if not dry_run and not confirmed:
        return _confirmation_required(operation, target_path=resolved.path, mount=resolved.mount_id)
    if not dry_run:
        resolved.path.write_text(new_source, encoding="utf-8")

    return AuthorOperationResult(
        operation_id=_operation_id(operation),
        operation=operation,
        ok=True,
        target_path=resolved.path,
        mount=resolved.mount_id,
        previous_visibility=previous_visibility,
        resulting_visibility=resulting_visibility,
        changed_files=() if dry_run else (resolved.path,),
        dry_run=dry_run,
        confirmed=confirmed,
        diff=diff,
        next_actions=_next_actions_for(operation),
        publication_impact=publication_impact,
    )


def _resolve_existing_target(
    target: str,
    *,
    mounts: tuple[Any, ...],
    mount_id: str | None = None,
    operation: str = "status",
) -> ResolvedAuthorTarget | AuthorOperationResult:
    explicit_path = Path(target).expanduser()
    candidates: list[ResolvedAuthorTarget] = []
    selected_mounts = [mount for mount in mounts if mount_id is None or mount.id == mount_id]
    if mount_id is not None and not selected_mounts:
        return _failed(
            operation,
            f"unknown mount: {mount_id}",
            next_action="Run fura query --json or inspect mounts.yaml for available mount ids.",
        )

    if explicit_path.is_absolute() or explicit_path.exists() or explicit_path.suffix:
        path = explicit_path.resolve()
        for mount in selected_mounts:
            if _is_relative_to(path, mount.content_root):
                candidates.append(
                    ResolvedAuthorTarget(
                        path=path,
                        mount_id=mount.id,
                        content_root=mount.content_root,
                        source_format=mount.source.content_format_for(path),
                    )
                )
    else:
        slug = _normalize_slug(target)
        for mount in selected_mounts:
            for candidate in _candidate_paths(mount.content_root, slug):
                if candidate.is_file():
                    candidates.append(
                        ResolvedAuthorTarget(
                            path=candidate,
                            mount_id=mount.id,
                            content_root=mount.content_root,
                            source_format=mount.source.content_format_for(candidate),
                        )
                    )

    if not candidates:
        return _failed(
            operation,
            f"author target not found: {target}",
            next_action="Pass a source path, page slug, or --mount for the intended content root.",
        )
    unique = {candidate.path: candidate for candidate in candidates}
    if len(unique) > 1:
        return _failed(
            operation,
            f"ambiguous author target: {target}",
            next_action="Pass --mount or an explicit source path.",
        )
    resolved = next(iter(unique.values()))
    if not resolved.path.is_file():
        return _failed(
            operation,
            f"author target is not a file: {resolved.path}",
            target_path=resolved.path,
            mount=resolved.mount_id,
        )
    return resolved


def _select_mount(mounts: tuple[Any, ...], *, mount_id: str | None = None) -> Any | AuthorOperationResult:
    if mount_id is not None:
        match = next((mount for mount in mounts if mount.id == mount_id), None)
        if match is None:
            return _failed(
                "new",
                f"unknown mount: {mount_id}",
                next_action="Inspect mounts.yaml for available mount ids.",
            )
        return match
    default = next((mount for mount in mounts if mount.default), None)
    if default is not None:
        return default
    if len(mounts) == 1:
        return mounts[0]
    return _failed(
        "new",
        "multiple mounts configured and no default mount was found",
        next_action="Pass --mount with the target mount id.",
    )


def _candidate_paths(content_root: Path, slug: str) -> list[Path]:
    normalized = _normalize_slug(slug)
    return [
        (content_root / f"{normalized}.md").resolve(),
        (content_root / normalized / "_index.md").resolve(),
    ]


def _read_source(target: ResolvedAuthorTarget) -> tuple[dict[str, Any], str]:
    source = target.path.read_text(encoding="utf-8")
    return parse_source_text(source, content_format=target.source_format)


def _validate_source_text(
    source_text: str,
    *,
    content_format: str,
    operation: str,
    target_path: Path,
    mount: str,
) -> tuple[dict[str, Any], str] | tuple[AuthorOperationResult, None]:
    if not source_text.strip():
        return (
            _failed(
                operation,
                "source text must not be empty",
                target_path=target_path,
                mount=mount,
                next_action="Restore the page source before saving.",
            ),
            None,
        )
    try:
        meta, body = parse_source_text(source_text, content_format=content_format)
    except Exception as exc:
        return (
            _failed(
                operation,
                f"source frontmatter could not be parsed: {exc}",
                target_path=target_path,
                mount=mount,
                next_action="Fix the frontmatter and retry the save.",
            ),
            None,
        )
    if not body.strip():
        return (
            _failed(
                operation,
                "source body must not be empty",
                target_path=target_path,
                mount=mount,
                next_action="Add page content below the frontmatter before saving.",
            ),
            None,
        )
    return meta, body


def _compose_source(meta: dict[str, Any], body: str) -> str:
    front = yaml.safe_dump(meta, sort_keys=False, allow_unicode=True).strip()
    clean_body = body.lstrip("\n")
    return f"---\n{front}\n---\n\n{clean_body}"


def _normalize_slug(slug: str) -> str:
    return slug.strip().strip("/").removesuffix(".md")


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _operation_id(operation: str) -> str:
    return f"author.{operation}.{uuid.uuid4().hex[:12]}"


def _diff(old: str, new: str, *, fromfile: str, tofile: str) -> str:
    return "".join(
        difflib.unified_diff(
            old.splitlines(keepends=True),
            new.splitlines(keepends=True),
            fromfile=fromfile,
            tofile=tofile,
        )
    )


def _publication_impact(
    *,
    previous_meta: dict[str, Any],
    resulting_meta: dict[str, Any],
    operation: str,
) -> dict[str, Any]:
    previous_public = is_public_meta(previous_meta)
    resulting_public = is_public_meta(resulting_meta)
    if previous_public and not resulting_public:
        change = "removed_from_public_output"
    elif not previous_public and resulting_public:
        change = "added_to_public_output"
    elif previous_public and resulting_public:
        change = "public_metadata_updated"
    else:
        change = "private_metadata_updated"

    surface_specs = (
        ("navigation", "Navigation and sidebar entries"),
        ("search", "Search indexes and suggestions"),
        ("export", "Static exports, sitemap, and DCP fixtures"),
        ("agent", "Agent retrieval, llms.txt, and MCP public resources"),
    )
    affected = previous_public or resulting_public
    surfaces = [
        {
            "id": surface_id,
            "label": label,
            "affected": affected,
            "reason": _publication_surface_reason(change, surface_id),
        }
        for surface_id, label in surface_specs
    ]
    return {
        "operation": operation,
        "previous_public": previous_public,
        "resulting_public": resulting_public,
        "change": change,
        "affected_surfaces": [surface["id"] for surface in surfaces if surface["affected"]],
        "surfaces": surfaces,
    }


def _publication_surface_reason(change: str, surface_id: str) -> str:
    if change == "added_to_public_output":
        return f"Page will be added to public {surface_id} output."
    if change == "removed_from_public_output":
        return f"Page will be removed from public {surface_id} output."
    if change == "public_metadata_updated":
        return f"Page remains public; {surface_id} metadata may refresh."
    return f"Page remains private; public {surface_id} output is unchanged."


def _failed(
    operation: str,
    message: str,
    *,
    target_path: Path | None = None,
    mount: str | None = None,
    next_action: str | None = None,
) -> AuthorOperationResult:
    diagnostic = AuthorDiagnostic(
        severity="error",
        message=message,
        source_path=str(target_path) if target_path is not None else None,
        next_action=next_action,
    )
    return AuthorOperationResult(
        operation_id=_operation_id(operation),
        operation=operation,
        ok=False,
        target_path=target_path,
        mount=mount,
        previous_visibility=None,
        resulting_visibility=None,
        diagnostics=(diagnostic,),
        next_actions=tuple(item for item in (next_action,) if item),
    )


def _confirmation_required(
    operation: str,
    *,
    target_path: Path,
    mount: str | None,
) -> AuthorOperationResult:
    return _failed(
        operation,
        "author lifecycle mutations require --yes or --dry-run",
        target_path=target_path,
        mount=mount,
        next_action="Rerun with --dry-run to preview or --yes to apply.",
    )


def _next_actions_for(operation: str) -> tuple[str, ...]:
    if operation == "publish":
        return (
            "Run fura check --content-only --json.",
            "Run fura export --fresh --json to verify public output surfaces.",
        )
    if operation == "archive":
        return ("Run fura check --content-only --json to verify archived visibility.",)
    return ("Preview locally with fura serve --author.",)


def _validation_message_applies(message: str, target_path: Path) -> bool:
    source = _validation_message_source(message)
    if source is None:
        return True
    source_path = Path(source)
    if not source_path.is_absolute():
        normalized_source = source_path.as_posix().strip("/")
        return target_path.resolve().as_posix().endswith(f"/{normalized_source}")
    try:
        return source_path.expanduser().resolve() == target_path.resolve()
    except OSError:
        return source == str(target_path)


def _validation_diagnostic(message: str, severity: str, target_path: Path) -> AuthorDiagnostic:
    source = _validation_message_source(message)
    source_path = str(target_path) if source is not None else None
    return AuthorDiagnostic(
        severity=severity,
        message=message,
        rule_id="fura.content",
        source_path=source_path,
        next_action=(
            "Fix the content validation error and rerun fura author validate."
            if severity == "error"
            else "Review the content validation warning."
        ),
    )


def _validation_message_source(message: str) -> str | None:
    if ": " not in message:
        return None
    source = message.split(": ", 1)[0]
    if "/" not in source and "\\" not in source:
        return None
    return source
