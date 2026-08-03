"""Public URL helpers for request-scoped documentation editions."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class EditionRoute:
    """One resolved public edition route."""

    requested: str
    edition: str
    content_path: str
    canonical_path: str
    alias: bool = False


def edition_segment(edition: str) -> str:
    """Return the public path segment for a stored edition id."""
    normalized = edition.strip()
    if normalized == "latest":
        return ""
    return normalized if normalized.startswith("v") else f"v{normalized}"


def edition_path(path: str, edition: str) -> str:
    """Prefix an unscoped catalog path with its canonical edition segment."""
    normalized = f"/{path.lstrip('/')}"
    segment = edition_segment(edition)
    if not segment:
        return normalized
    if normalized == "/":
        return f"/{segment}/"
    return f"/{segment}{normalized}"


def strip_edition_path(path: str, segment: str) -> str:
    """Remove one already-validated edition segment from a request path."""
    prefix = f"/{segment}"
    if path == prefix or path == f"{prefix}/":
        return "/"
    if path.startswith(f"{prefix}/"):
        return path[len(prefix) :]
    return path


def route_for_segment(
    path: str,
    segment: str,
    *,
    edition_ids: tuple[str, ...],
    aliases: dict[str, str],
) -> EditionRoute | None:
    """Resolve a configured release segment or alias for one mount."""
    content_path = strip_edition_path(path, segment)
    alias_target = aliases.get(segment)
    if alias_target is not None:
        if alias_target != "latest" and alias_target not in edition_ids:
            return None
        return EditionRoute(
            requested=segment,
            edition=alias_target,
            content_path=content_path,
            canonical_path=edition_path(content_path, alias_target),
            alias=True,
        )
    for edition in edition_ids:
        if edition != "latest" and edition_segment(edition) == segment:
            return EditionRoute(
                requested=segment,
                edition=edition,
                content_path=content_path,
                canonical_path=edition_path(content_path, edition),
            )
    return None
