"""Public projection schema and fixture remain aligned with the serializer."""

from __future__ import annotations

import copy
import json
from importlib.resources import files
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

from furatena.catalog.public_projection import (
    PUBLIC_PROJECTION_SURFACES,
    PublicProjectionChange,
    PublicProjectionDeliveryIdentity,
    PublicProjectionInspection,
    PublicProjectionPlan,
    PublicProjectionSurface,
)

FIXTURE = Path(__file__).parent / "fixtures" / "public-projection" / "v1" / "publish.json"


def _schema() -> dict[str, object]:
    path = files("furatena.catalog").joinpath("schemas/public-projection/v1/inspection.schema.json")
    return json.loads(path.read_text(encoding="utf-8"))


def _fixture() -> dict[str, object]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _serialized_inspection() -> dict[str, object]:
    digest = f"sha256:{'a' * 64}"
    plan = PublicProjectionPlan.create(
        source_revision=digest,
        change="visibility -> public",
        node_id="chirp:latest:docs/planned",
        operation="publish",
        previous_visibility="draft",
        resulting_visibility="public",
    )
    surfaces = tuple(
        PublicProjectionSurface(
            surface_id=surface_id,
            route=f"/{surface_id.value}",
            previous_present=False,
            resulting_present=True,
            previous_digest=None,
            resulting_digest=digest,
            change=PublicProjectionChange.ADDED,
            preview={"node_id": plan.node_id},
        )
        for surface_id in PUBLIC_PROJECTION_SURFACES
    )
    return PublicProjectionInspection(
        ok=True,
        complete=True,
        read_only=True,
        plan=plan,
        source=PublicProjectionDeliveryIdentity("source", digest, "draft", "current"),
        frozen_artifact=PublicProjectionDeliveryIdentity(
            "frozen_artifact", None, "unavailable", "unknown"
        ),
        deployed_artifact=PublicProjectionDeliveryIdentity(
            "deployed_artifact", None, "unavailable", "unknown"
        ),
        surfaces=surfaces,
        privacy={
            "status": "pass",
            "protected_node_count": 1,
            "canary_count": 1,
            "scanned_artifacts": 1,
            "matches": [],
        },
    ).to_dict()


def test_v1_fixture_and_serializer_validate_against_shipped_schema() -> None:
    validator = Draft202012Validator(_schema())

    validator.validate(_fixture())
    serialized = _serialized_inspection()
    validator.validate(serialized)
    assert {item["id"] for item in serialized["surfaces"]} == {
        surface.value for surface in PUBLIC_PROJECTION_SURFACES
    }


@pytest.mark.parametrize(
    "mutate",
    [
        lambda payload: payload.update(schema_version=2),
        lambda payload: payload.update(unexpected=True),
        lambda payload: payload["surfaces"].pop(),
        lambda payload: payload["surfaces"][-1].update(id="anonymous_html"),
        lambda payload: payload["privacy"].update(status="fail"),
    ],
)
def test_v1_schema_rejects_version_shape_surface_and_privacy_drift(mutate) -> None:
    payload = copy.deepcopy(_fixture())
    mutate(payload)

    with pytest.raises(ValidationError):
        Draft202012Validator(_schema()).validate(payload)
