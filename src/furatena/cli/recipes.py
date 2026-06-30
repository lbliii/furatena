"""Agent-oriented command recipes for ``fura`` workflows."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class RecipeStep:
    """One command or approval gate in a recipe."""

    id: str
    title: str
    command: str
    purpose: str
    dry_run: bool = False
    requires_confirmation: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "command": self.command,
            "purpose": self.purpose,
            "dry_run": self.dry_run,
            "requires_confirmation": self.requires_confirmation,
        }


@dataclass(frozen=True, slots=True)
class Recipe:
    """A stable workflow agents can run without inferring hidden state."""

    id: str
    title: str
    summary: str
    applies_to: tuple[str, ...]
    steps: tuple[RecipeStep, ...]
    verifies: tuple[str, ...] = ()
    related_commands: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "summary": self.summary,
            "applies_to": list(self.applies_to),
            "steps": [step.to_dict() for step in self.steps],
            "verifies": list(self.verifies),
            "related_commands": list(self.related_commands),
        }


RECIPES: tuple[Recipe, ...] = (
    Recipe(
        id="init",
        title="Initialize a standalone docs app",
        summary="Create a fresh Furatena app, validate the scaffold, and start an author preview.",
        applies_to=("Codex", "Claude Code", "Cursor", "CI bootstrap", "local shell"),
        steps=(
            RecipeStep(
                id="scaffold",
                title="Scaffold the app",
                command='fura init <APP_ROOT> --name "<SITE_NAME>" --json',
                purpose="Create docs.yaml, mounts.yaml, starter content, and theme files.",
            ),
            RecipeStep(
                id="validate",
                title="Validate the scaffold",
                command="fura --app-root <APP_ROOT> check --content-only --warnings-as-errors --json",
                purpose="Confirm the generated content and view configuration are automation-safe.",
            ),
            RecipeStep(
                id="preview",
                title="Start author preview",
                command="fura --app-root <APP_ROOT> serve --author --json",
                purpose="Emit startup metadata, then run the live authoring server.",
            ),
        ),
        verifies=("scaffold files exist", "content check exits 0", "serve startup JSON includes URL"),
        related_commands=("init", "check", "serve"),
    ),
    Recipe(
        id="inspect",
        title="Inspect an existing catalog",
        summary="Read catalog shape, theme resolution, and representative graph nodes before editing.",
        applies_to=("Codex", "Claude Code", "Cursor", "local shell"),
        steps=(
            RecipeStep(
                id="check",
                title="Run non-mutating validation",
                command="fura --app-root <APP_ROOT> check --content-only --json",
                purpose="Collect structured diagnostics without writing files.",
            ),
            RecipeStep(
                id="theme",
                title="Inspect theme resolution",
                command="fura --app-root <APP_ROOT> theme inspect --json",
                purpose="Identify active templates and override targets.",
            ),
            RecipeStep(
                id="query",
                title="Sample catalog graph",
                command="fura --app-root <APP_ROOT> query --url-prefix / --json",
                purpose="Return graph nodes with stable node ids, mounts, editions, and URLs.",
            ),
        ),
        verifies=("check JSON is parseable", "theme files resolve", "query returns expected nodes"),
        related_commands=("check", "theme inspect", "query"),
    ),
    Recipe(
        id="validate",
        title="Validate content, views, API references, and deploy contracts",
        summary="Run strict checks suitable for CI and agent approval gates.",
        applies_to=("Codex", "Claude Code", "Cursor", "CI", "local shell"),
        steps=(
            RecipeStep(
                id="content",
                title="Run strict catalog checks",
                command="fura --app-root <APP_ROOT> check --deploy --agent --warnings-as-errors --json",
                purpose="Validate content links, views, theme lint, DCP export shape, deploy rules, and agent contracts.",
            ),
            RecipeStep(
                id="api-surface",
                title="Inspect API-tagged pages",
                command="fura --app-root <APP_ROOT> query --tag api --json",
                purpose="Report API reference pages when the catalog uses API tags or views.",
            ),
            RecipeStep(
                id="agent-evals",
                title="Run agent retrieval evals",
                command="fura --app-root <APP_ROOT> evals --json",
                purpose="Run deterministic Milo-backed retrieval, resource, and tool-selection evals.",
            ),
            RecipeStep(
                id="migration-dry-run",
                title="Preview MDX migration risk",
                command="fura --app-root <APP_ROOT> migrate --dry-run --keep-mdx --json",
                purpose="Surface unsupported constructs without modifying source files.",
                dry_run=True,
            ),
        ),
        verifies=("strict check exits 0", "agent evals pass", "diagnostics are structured", "migration preview is non-mutating"),
        related_commands=("check", "query", "evals", "migrate"),
    ),
    Recipe(
        id="query",
        title="Query the graph for agent retrieval",
        summary="Use stable Content IR and DCP graph filters without scraping rendered pages.",
        applies_to=("Codex", "Claude Code", "Cursor", "local shell"),
        steps=(
            RecipeStep(
                id="by-heading",
                title="Find pages by heading text",
                command='fura --app-root <APP_ROOT> query --heading "<TEXT>" --json',
                purpose="Locate docs whose Content IR headings match the requested text.",
            ),
            RecipeStep(
                id="by-directive",
                title="Find directive usage",
                command="fura --app-root <APP_ROOT> query --directive <DIRECTIVE> --json",
                purpose="Locate pages using a specific Patitas directive.",
            ),
            RecipeStep(
                id="by-namespace",
                title="Narrow by mount, edition, or URL",
                command="fura --app-root <APP_ROOT> query --mount <MOUNT> --edition <EDITION> --url-prefix <PATH> --json",
                purpose="Keep retrieval scoped to one catalog namespace.",
            ),
            RecipeStep(
                id="by-dcp-edge",
                title="Filter DCP graph edges",
                command=(
                    "curl '<BASE_URL>/catalog/query.json?mount=<MOUNT>&edge_kind=<EDGE_KIND>&"
                    "target=<TARGET>'"
                ),
                purpose="Return pages, edges, graph_nodes, and namespaces from the headless DCP graph API.",
            ),
            RecipeStep(
                id="by-mcp-graph",
                title="Query graph over MCP",
                command="MCP query_graph mount=<MOUNT> edge_kind=<EDGE_KIND> target=<TARGET>",
                purpose="Use the Milo-backed MCP tool when the agent is connected to fura mcp.",
            ),
        ),
        verifies=(
            "query filters are explicit",
            "DCP graph responses include pages, edges, and graph_nodes",
            "MCP query_graph returns structuredContent",
            "no source files are modified",
        ),
        related_commands=("query", "mcp", "catalog/query.json", "graph/query.json"),
    ),
    Recipe(
        id="publish",
        title="Build publishable static artifacts",
        summary="Freeze the graph, export static HTML, and verify the output before external deployment.",
        applies_to=("Codex", "Claude Code", "Cursor", "CI", "local shell"),
        steps=(
            RecipeStep(
                id="freeze",
                title="Freeze catalog IR",
                command="fura --app-root <APP_ROOT> freeze --json",
                purpose="Write catalog.json, search.json, tools.json, structure.json, semantic.json, and frozen pages.",
            ),
            RecipeStep(
                id="export",
                title="Export static site",
                command='fura --app-root <APP_ROOT> export --fresh --base-path "<BASE_PATH>" --base-url "<BASE_URL>" --json',
                purpose="Render frozen graph data into static HTML and sidecar files.",
            ),
            RecipeStep(
                id="verify-preview",
                title="Preview the frozen build",
                command="fura --app-root <APP_ROOT> serve --preview --json",
                purpose="Serve the frozen catalog locally for smoke verification before deployment.",
            ),
        ),
        verifies=("freeze writes catalog IR", "export writes public HTML", "preview serves frozen output"),
        related_commands=("freeze", "export", "serve"),
    ),
    Recipe(
        id="repair",
        title="Repair content and theme drift",
        summary="Collect diagnostics, preview risky rewrites, and require approval before mutating sources.",
        applies_to=("Codex", "Claude Code", "Cursor", "local shell"),
        steps=(
            RecipeStep(
                id="diagnose",
                title="Collect diagnostics",
                command="fura --app-root <APP_ROOT> check --json",
                purpose="Get structured errors and warnings with source paths and suggested next actions.",
            ),
            RecipeStep(
                id="migration-preview",
                title="Preview MDX repairs",
                command="fura --app-root <APP_ROOT> migrate --dry-run --keep-mdx --json",
                purpose="Identify files that can be lowered safely without changing source yet.",
                dry_run=True,
            ),
            RecipeStep(
                id="migration-write",
                title="Apply approved MDX repairs",
                command="fura --app-root <APP_ROOT> migrate --keep-mdx --json",
                purpose="Write approved markdown siblings while retaining original MDX sources.",
                requires_confirmation=True,
            ),
            RecipeStep(
                id="theme-diff",
                title="Review theme override drift",
                command="fura --app-root <APP_ROOT> theme diff <THEME_PATH> --json",
                purpose="Compare local overrides against upstream templates.",
            ),
        ),
        verifies=("diagnostics identify files", "dry-run precedes writes", "mutating steps are approval-gated"),
        related_commands=("check", "migrate", "theme diff"),
    ),
    Recipe(
        id="author-draft",
        title="Create and inspect a draft page",
        summary="Create a private draft with a dry-run preview before writing source files.",
        applies_to=("Codex", "Claude Code", "Cursor", "local MCP agent", "local shell"),
        steps=(
            RecipeStep(
                id="preview-draft",
                title="Preview draft creation",
                command=(
                    'fura --app-root <APP_ROOT> author new <SLUG> --title "<TITLE>" '
                    "--dry-run --json"
                ),
                purpose="Return the source diff and draft visibility without creating a file.",
                dry_run=True,
            ),
            RecipeStep(
                id="create-draft",
                title="Create approved draft",
                command='fura --app-root <APP_ROOT> author new <SLUG> --title "<TITLE>" --yes --json',
                purpose="Write the draft after review approval.",
                requires_confirmation=True,
            ),
            RecipeStep(
                id="inspect-draft",
                title="Inspect draft state",
                command="fura --app-root <APP_ROOT> author status <SLUG> --json",
                purpose="Confirm the source remains draft/private by default.",
            ),
        ),
        verifies=("dry-run diff reviewed", "draft source written only after approval", "status is draft"),
        related_commands=("author new", "author status", "mcp --author --include-private"),
    ),
    Recipe(
        id="author-edit-publish",
        title="Edit, validate, and publish safely",
        summary="Read a source, preview exact edits, validate, and publish only after explicit approval.",
        applies_to=("Codex", "Claude Code", "Cursor", "local MCP agent", "local shell"),
        steps=(
            RecipeStep(
                id="read-source",
                title="Read author source",
                command="MCP author_read_source target=<SLUG>",
                purpose="Fetch source only from an include-private local author MCP session.",
            ),
            RecipeStep(
                id="preview-edit",
                title="Preview exact edit",
                command="MCP author_propose_edit target=<SLUG> old_text=<OLD> new_text=<NEW>",
                purpose="Return a unified diff without writing files.",
                dry_run=True,
            ),
            RecipeStep(
                id="apply-edit",
                title="Apply approved edit",
                command=(
                    "MCP author_apply_edit target=<SLUG> old_text=<OLD> new_text=<NEW> "
                    "confirmed=true dry_run=false"
                ),
                purpose="Write only the exact reviewed replacement.",
                requires_confirmation=True,
            ),
            RecipeStep(
                id="validate",
                title="Validate edited source",
                command="MCP author_validate target=<SLUG>",
                purpose="Collect structured validation diagnostics before publication.",
            ),
            RecipeStep(
                id="publish-dry-run",
                title="Preview publication",
                command="MCP author_publish target=<SLUG> dry_run=true",
                purpose="Review lifecycle metadata diff before public output changes.",
                dry_run=True,
            ),
            RecipeStep(
                id="publish",
                title="Publish approved source",
                command="MCP author_publish target=<SLUG> confirmed=true dry_run=false",
                purpose="Move the page to public visibility after validation.",
                requires_confirmation=True,
            ),
        ),
        verifies=("private source read requires include-private MCP", "edit diff reviewed", "publish is approval-gated"),
        related_commands=("author status", "author publish", "mcp --author --include-private"),
    ),
    Recipe(
        id="author-stale-repair",
        title="Repair stale author content",
        summary="Inspect stale impact, apply a reviewed source fix, and rebuild public outputs.",
        applies_to=("Codex", "Claude Code", "Cursor", "local MCP agent", "local shell"),
        steps=(
            RecipeStep(
                id="inspect-impact",
                title="Inspect stale impact",
                command="MCP author_inspect_publication_impact target=<SLUG>",
                purpose="Return lifecycle state, validation diagnostics, and stale refresh targets.",
            ),
            RecipeStep(
                id="preview-fix",
                title="Preview source fix",
                command="MCP author_propose_edit target=<SLUG> old_text=<OLD> new_text=<NEW>",
                purpose="Review the exact source change before writing.",
                dry_run=True,
            ),
            RecipeStep(
                id="apply-fix",
                title="Apply approved fix",
                command=(
                    "MCP author_apply_edit target=<SLUG> old_text=<OLD> new_text=<NEW> "
                    "confirmed=true dry_run=false"
                ),
                purpose="Write the reviewed repair.",
                requires_confirmation=True,
            ),
            RecipeStep(
                id="validate",
                title="Validate repaired source",
                command="fura --app-root <APP_ROOT> check --content-only --json",
                purpose="Confirm stale repair did not introduce catalog errors.",
            ),
        ),
        verifies=("impact is structured", "repair is diff-first", "validation passes after the edit"),
        related_commands=("check", "mcp --author --include-private"),
    ),
    Recipe(
        id="author-publish-remediation",
        title="Remediate failed publication",
        summary="Handle failed publish attempts by reading diagnostics, repairing source, and retrying dry-run first.",
        applies_to=("Codex", "Claude Code", "Cursor", "local MCP agent", "local shell"),
        steps=(
            RecipeStep(
                id="publish-preview",
                title="Preview publication",
                command="MCP author_publish target=<SLUG> dry_run=true",
                purpose="Collect publication diff and lifecycle diagnostics before a write.",
                dry_run=True,
            ),
            RecipeStep(
                id="validate-target",
                title="Validate target",
                command="MCP author_validate target=<SLUG>",
                purpose="Return structured errors and warnings for remediation.",
            ),
            RecipeStep(
                id="repair-preview",
                title="Preview remediation edit",
                command="MCP author_propose_edit target=<SLUG> old_text=<OLD> new_text=<NEW>",
                purpose="Prepare a focused fix without modifying source.",
                dry_run=True,
            ),
            RecipeStep(
                id="repair-write",
                title="Apply approved remediation",
                command=(
                    "MCP author_apply_edit target=<SLUG> old_text=<OLD> new_text=<NEW> "
                    "confirmed=true dry_run=false"
                ),
                purpose="Apply the reviewed remediation.",
                requires_confirmation=True,
            ),
            RecipeStep(
                id="retry-publish",
                title="Retry publication with approval",
                command="MCP author_publish target=<SLUG> confirmed=true dry_run=false",
                purpose="Publish only after remediation and validation are clean.",
                requires_confirmation=True,
            ),
        ),
        verifies=("failed publish remains non-mutating", "diagnostics drive repair", "retry is approval-gated"),
        related_commands=("author publish", "check", "mcp --author --include-private"),
    ),
    Recipe(
        id="author-archive",
        title="Archive obsolete public content safely",
        summary="Inspect publication impact, preview archive metadata, and remove obsolete pages from public output after approval.",
        applies_to=("Codex", "Claude Code", "Cursor", "local MCP agent", "local shell"),
        steps=(
            RecipeStep(
                id="inspect-impact",
                title="Inspect publication impact",
                command="MCP author_inspect_publication_impact target=<SLUG>",
                purpose="Return lifecycle state, validation diagnostics, and public-output impact before archiving.",
            ),
            RecipeStep(
                id="archive-dry-run",
                title="Preview archive transition",
                command="MCP author_archive target=<SLUG> dry_run=true",
                purpose="Review the archive diff and publication impact without changing source.",
                dry_run=True,
            ),
            RecipeStep(
                id="archive",
                title="Archive approved source",
                command="MCP author_archive target=<SLUG> confirmed=true dry_run=false",
                purpose="Move the page to archived visibility after approval.",
                requires_confirmation=True,
            ),
            RecipeStep(
                id="validate",
                title="Validate archived visibility",
                command="fura --app-root <APP_ROOT> check --content-only --json",
                purpose="Confirm archived content is excluded from public catalog output.",
            ),
        ),
        verifies=(
            "archive impact is reviewed",
            "archive dry-run precedes writes",
            "archived content stays out of public retrieval",
        ),
        related_commands=("author archive", "mcp --author --include-private", "check"),
    ),
    Recipe(
        id="source-sync",
        title="Refresh sources and rebuild derived outputs",
        summary="Synchronize externally managed content, then rebuild and validate generated graph outputs.",
        applies_to=("Codex", "Claude Code", "Cursor", "CI", "local shell"),
        steps=(
            RecipeStep(
                id="sync-external",
                title="Synchronize external source checkout",
                command="git -C <SOURCE_REPO> pull --ff-only",
                purpose="Refresh a git-backed source mount before Furatena indexes it.",
                requires_confirmation=True,
            ),
            RecipeStep(
                id="validate",
                title="Validate refreshed sources",
                command="fura --app-root <APP_ROOT> check --content-only --json",
                purpose="Catch broken links, view issues, and source diagnostics before freezing.",
            ),
            RecipeStep(
                id="freeze",
                title="Rebuild frozen graph outputs",
                command="fura --app-root <APP_ROOT> freeze --json",
                purpose="Persist refreshed registry, mount shards, search, semantic, and structure outputs.",
            ),
        ),
        verifies=("source sync is explicit", "validation runs after sync", "freeze records refreshed outputs"),
        related_commands=("check", "freeze"),
    ),
)


def all_recipes() -> tuple[Recipe, ...]:
    return RECIPES


def get_recipe(recipe_id: str) -> Recipe | None:
    for recipe in RECIPES:
        if recipe.id == recipe_id:
            return recipe
    return None


def recipe_ids() -> tuple[str, ...]:
    return tuple(recipe.id for recipe in RECIPES)
