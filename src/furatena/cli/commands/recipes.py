"""recipes command parser and execution."""

from __future__ import annotations

import argparse
from typing import Any

from furatena.cli.commands._shared import CommandModule
from furatena.cli.contracts import CommandResult, Diagnostic, ExitCode, command_name


def _run_recipes(args: argparse.Namespace) -> CommandResult:
    from furatena.cli.recipes import all_recipes, get_recipe, recipe_ids

    selected = (get_recipe(args.recipe),) if args.recipe else all_recipes()
    if args.recipe and selected[0] is None:
        diagnostics = (
            Diagnostic(
                severity="error",
                message=f"unknown recipe: {args.recipe}",
                rule_id="fura.recipes",
                next_action=f"Choose one of: {', '.join(recipe_ids())}.",
            ),
        )
        return CommandResult(
            command=command_name(args),
            ok=False,
            exit_code=ExitCode.CONFIG_ERROR,
            summary="unknown recipe",
            diagnostics=diagnostics,
            data={"available": recipe_ids()},
            terminal_lines=tuple(f"error: {item.message}" for item in diagnostics),
        )
    recipes = tuple(recipe for recipe in selected if recipe is not None)
    terminal_lines: list[str] = []
    for index, recipe in enumerate(recipes):
        if index:
            terminal_lines.append("")
        terminal_lines.append(f"{recipe.id}: {recipe.title}")
        terminal_lines.append(f"  {recipe.summary}")
        terminal_lines.append(f"  applies to: {', '.join(recipe.applies_to)}")
        terminal_lines.append("  steps:")
        for step in recipe.steps:
            flags = []
            if step.dry_run:
                flags.append("dry-run")
            if step.requires_confirmation:
                flags.append("requires confirmation")
            suffix = f" ({', '.join(flags)})" if flags else ""
            terminal_lines.append(f"    - {step.id}: {step.command}{suffix}")
            terminal_lines.append(f"      {step.purpose}")
        if recipe.verifies:
            terminal_lines.append(f"  verifies: {', '.join(recipe.verifies)}")
    return CommandResult(
        command=command_name(args),
        ok=True,
        summary=f"returned {len(recipes)} recipe(s)",
        data={
            "recipes": [recipe.to_dict() for recipe in recipes],
            "count": len(recipes),
            "available": recipe_ids(),
        },
        terminal_lines=tuple(terminal_lines),
    )


def configure(sub: Any) -> None:
    recipes = sub.add_parser("recipes", help="Show stable agent workflow recipes")
    recipes.add_argument(
        "recipe",
        nargs="?",
        default=None,
        help="Optional recipe id, e.g. init, inspect, validate, query, publish, repair, source-sync",
    )
    recipes.add_argument(
        "--json", action="store_true", help="Emit the standard command result JSON"
    )
    recipes.set_defaults(handler=_run_recipes)


COMMAND = CommandModule("recipes", configure)
