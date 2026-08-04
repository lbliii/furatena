"""Versioned agent fixture and semantic compatibility diff contracts."""

from __future__ import annotations

import copy
import json
from pathlib import Path

from furatena.catalog.agent_contract_diff import (
    diff_agent_contract_fixtures,
    load_agent_contract_fixture,
)
from furatena.cli.main import run_command

FIXTURES = Path(__file__).parent / "fixtures" / "agent-contracts" / "v1"


def _write(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def test_versioned_public_and_trusted_agent_fixtures_are_valid() -> None:
    public = load_agent_contract_fixture(FIXTURES / "public.json")
    trusted = load_agent_contract_fixture(FIXTURES / "trusted-author.json")

    assert public["fixture_version"] == trusted["fixture_version"] == 1
    assert public["profile"] == "public"
    assert trusted["surfaces"]["tools"]["access"]["include_private"] is True
    assert public["surfaces"]["mcp_apps"]["contract_version"] == 1
    assert public["surfaces"]["mcp_apps"]["gateway"]["collision"] == "reject"
    assert trusted["surfaces"]["mcp_apps"]["resources"] == []


def test_semantic_diff_ignores_keyed_reordering_and_reports_compatible_additions(
    tmp_path: Path,
) -> None:
    old = load_agent_contract_fixture(FIXTURES / "public.json")
    new = copy.deepcopy(old)
    new["surfaces"]["mcp"]["tools"].reverse()
    new["surfaces"]["mcp"]["tools"].append({"name": "query_graph", "read_only": True})
    new["surfaces"]["tools"]["description"] = "Public agent tools."
    old_path = tmp_path / "old.json"
    new_path = tmp_path / "new.json"
    _write(old_path, old)
    _write(new_path, new)

    payload = diff_agent_contract_fixtures(old_path, new_path)

    assert payload["summary"] == {"added": 2, "removed": 0, "changed": 0, "breaking": 0}
    assert payload["compatibility"] == {
        "status": "compatible",
        "decision": None,
        "required": False,
    }


def test_breaking_diff_requires_and_records_explicit_compatibility_decision(
    tmp_path: Path,
) -> None:
    old = load_agent_contract_fixture(FIXTURES / "public.json")
    new = copy.deepcopy(old)
    new["surfaces"]["tools"]["urls"]["catalog_url"] = "/catalog-v4.json"
    new["surfaces"]["mcp"]["resources"] = [
        item
        for item in new["surfaces"]["mcp"]["resources"]
        if item["uri"] != "fura://catalog/graph"
    ]
    old_path = tmp_path / "old.json"
    new_path = tmp_path / "new.json"
    _write(old_path, old)
    _write(new_path, new)

    undecided = diff_agent_contract_fixtures(old_path, new_path)
    accepted = diff_agent_contract_fixtures(
        old_path,
        new_path,
        compatibility_decision="Ship in v2 with a catalog URL migration note.",
    )

    assert undecided["summary"]["breaking"] == 2
    assert undecided["compatibility"]["status"] == "requires-decision"
    assert accepted["compatibility"]["status"] == "accepted"
    assert accepted["compatibility"]["decision"].startswith("Ship in v2")


def test_agent_diff_cli_blocks_undecided_breaking_changes(tmp_path: Path) -> None:
    old = load_agent_contract_fixture(FIXTURES / "public.json")
    new = copy.deepcopy(old)
    del new["surfaces"]["tools"]["urls"]["search_url"]
    old_path = tmp_path / "old.json"
    new_path = tmp_path / "new.json"
    _write(old_path, old)
    _write(new_path, new)

    blocked = run_command(["agent-diff", str(old_path), str(new_path)])
    accepted = run_command(
        [
            "agent-diff",
            str(old_path),
            str(new_path),
            "--decision",
            "Accepted for the next major fixture version.",
        ]
    )

    assert blocked.ok is False
    assert blocked.exit_code == 2
    assert blocked.diagnostics[0].rule_id == "fura.agent.breaking_change"
    assert accepted.ok is True
    assert accepted.data["compatibility"]["status"] == "accepted"
