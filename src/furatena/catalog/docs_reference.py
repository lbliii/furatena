"""Generate deterministic CLI, configuration, and environment reference docs."""

from __future__ import annotations

import argparse
import ast
from pathlib import Path
from typing import Any

from furatena.catalog.docs_inventory import _config_surfaces


def _display(value: object) -> str:
    if value is None or value == "":
        return "—"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, list):
        return ", ".join(_display(item) for item in value) or "—"
    text = str(value).replace("|", "\\|").replace("\n", " ")
    return f"`{text}`"


def _action_record(action: argparse.Action) -> dict[str, object]:
    label = ", ".join(action.option_strings) if action.option_strings else action.dest
    return {
        "label": label,
        "required": bool(action.required),
        "default": action.default,
        "choices": list(action.choices) if action.choices is not None else [],
        "nargs": action.nargs,
        "type": getattr(action.type, "__name__", str(action.type or "")),
        "help": action.help or "",
    }


def cli_reference_records() -> tuple[dict[str, object], ...]:
    """Return every parser command and option, including global options."""

    # Import lazily so catalog consumers do not initialize the CLI command graph.
    from furatena.cli.main import _build_parser

    parser = _build_parser()
    records: list[dict[str, object]] = []

    def record(current: argparse.ArgumentParser, command: str) -> None:
        options = [
            _action_record(action)
            for action in current._actions
            if not isinstance(action, argparse._SubParsersAction) and action.dest != "help"
        ]
        records.append(
            {
                "command": command,
                "description": current.description or "",
                "options": options,
            }
        )
        for action in current._actions:
            if not isinstance(action, argparse._SubParsersAction):
                continue
            seen: set[int] = set()
            for name, child in sorted(action.choices.items()):
                if id(child) in seen:
                    continue
                seen.add(id(child))
                record(child, f"{command} {name}")

    record(parser, "fura")
    return tuple(records)


def config_reference_records() -> tuple[dict[str, object], ...]:
    """Return flattened docs.yaml and mounts.yaml field contracts."""

    return tuple(
        {
            "field": surface.name,
            "type": surface.contract["type"],
            "default": surface.contract.get("default"),
            "required": bool(surface.contract.get("required", False)),
            "source": surface.implementation,
        }
        for surface in _config_surfaces()
    )


def _literal(node: ast.AST | None) -> object:
    if node is None:
        return None
    try:
        return ast.literal_eval(node)
    except (ValueError, TypeError):
        return ast.unparse(node)


def environment_reference_records(package_root: Path | None = None) -> tuple[dict[str, object], ...]:
    """Derive Furatena/Chirp environment controls and defaults from source usage."""

    root = package_root or Path(__file__).resolve().parents[1]
    found: dict[str, dict[str, Any]] = {}

    def add(name: str, *, default: object, source: str, mode: str) -> None:
        if not name.startswith(("FURA_", "CHIRP_")):
            return
        item = found.setdefault(name, {"name": name, "defaults": set(), "sources": set(), "modes": set()})
        item["defaults"].add(repr(default))
        item["sources"].add(source)
        item["modes"].add(mode)

    for path in root.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        relative = path.relative_to(root.parent).as_posix()
        for node in ast.walk(tree):
            source = f"{relative}:{getattr(node, 'lineno', 1)}"
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                owner = ast.unparse(node.func.value)
                if (
                    owner in {"os.environ", "os"}
                    and node.func.attr in {"get", "getenv", "setdefault"}
                    and node.args
                    and isinstance(node.args[0], ast.Constant)
                    and isinstance(node.args[0].value, str)
                ):
                    mode = "write" if node.func.attr == "setdefault" else "read"
                    add(
                        node.args[0].value,
                        default=_literal(node.args[1] if len(node.args) > 1 else None),
                        source=source,
                        mode=mode,
                    )
            if isinstance(node, ast.Subscript) and ast.unparse(node.value) == "os.environ":
                name = _literal(node.slice)
                if isinstance(name, str):
                    add(
                        name,
                        default=None,
                        source=source,
                        mode="write" if isinstance(node.ctx, ast.Store) else "read",
                    )

    return tuple(
        {
            "name": name,
            "defaults": sorted(found[name]["defaults"]),
            "modes": sorted(found[name]["modes"]),
            "sources": sorted(found[name]["sources"]),
        }
        for name in sorted(found)
    )


def render_docs_reference() -> str:
    """Render the committed generated reference."""

    lines = [
        "---",
        "title: Generated CLI and configuration reference",
        "description: Parser-derived commands, options, defaults, config fields, and environment controls.",
        "weight: 35",
        "lang: en",
        "type: doc",
        "category: reference",
        "---",
        "",
        "# Generated CLI and configuration reference",
        "",
        "This page is generated from the active CLI parser, configuration dataclasses, and",
        "environment lookups. Edit the implementation or generator, then run",
        "`fura docs-reference --output content/furatena/docs/reference/generated-cli-config.md`;",
        "do not hand-edit the tables.",
        "",
        "## CLI commands and options",
        "",
    ]
    for record in cli_reference_records():
        lines.extend(
            [
                f"### `{record['command']}`",
                "",
                str(record["description"] or "Command parser contract."),
                "",
                "| Argument or option | Required | Default | Choices | Type | Purpose |",
                "|---|---:|---|---|---|---|",
            ]
        )
        for option in record["options"]:
            lines.append(
                "| "
                + " | ".join(
                    (
                        _display(option["label"]),
                        "yes" if option["required"] else "no",
                        _display(option["default"]),
                        _display(option["choices"]),
                        _display(option["type"]),
                        str(option["help"] or "—").replace("|", "\\|").replace("\n", " "),
                    )
                )
                + " |"
            )
        lines.append("")

    lines.extend(
        [
            "## Configuration fields",
            "",
            "Paths use dotted docs.yaml notation; `mounts[]` identifies one mounts.yaml entry.",
            "",
            "| Field | Required | Type | Default | Implementation source |",
            "|---|---:|---|---|---|",
        ]
    )
    for record in config_reference_records():
        lines.append(
            f"| `{record['field']}` | {'yes' if record['required'] else 'no'} | "
            f"{_display(record['type'])} | {_display(record['default'])} | "
            f"`{record['source']}` |"
        )

    lines.extend(
        [
            "",
            "## Environment controls",
            "",
            "Read/write modes and defaults are derived from package source. `None` means the",
            "implementation treats absence as significant or supplies behavior elsewhere.",
            "",
            "| Variable | Modes | Observed defaults | Implementation sources |",
            "|---|---|---|---|",
        ]
    )
    for record in environment_reference_records():
        lines.append(
            f"| `{record['name']}` | {', '.join(record['modes'])} | "
            f"{', '.join(record['defaults'])} | "
            f"{', '.join(f'`{source}`' for source in record['sources'])} |"
        )

    lines.extend(
        [
            "",
            "## Error and remediation examples",
            "",
            "All JSON-capable commands return `ok`, `exit_code`, `summary`, `diagnostics`,",
            "and `data`. Validation failures use exit code 2; configuration failures use 3;",
            "source conflicts use 4. Each diagnostic includes a stable `rule_id` where available",
            "and a `next_action` remediation.",
            "",
            "```json",
            "{",
            '  "ok": false,',
            '  "exit_code": 2,',
            '  "summary": "check completed with 1 error(s)",',
            '  "diagnostics": [{',
            '    "severity": "error",',
            '    "rule_id": "fura.content",',
            '    "message": "broken internal link /missing/",',
            '    "next_action": "Fix the target or update the link, then rerun fura check."',
            "  }]",
            "}",
            "```",
            "",
            "For mutation conflicts, rerun `fura author status`, review the new source revision,",
            "and repeat the dry run. Do not retry with a stale revision or discard diagnostics.",
            "",
        ]
    )
    return "\n".join(lines)
