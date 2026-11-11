import json
from pathlib import Path

import pytest
from a2_api.exports.schemas import export_schemas
from a2_contracts.results import ProfileResult
from pydantic import ValidationError

FIXTURES = Path(__file__).resolve().parents[3] / "packages/contracts/fixtures"


def test_fixtures_are_synthetic_and_invalid_is_rejected():
    for name in ("success", "partial", "invalid"):
        fixture = json.loads((FIXTURES / f"{name}.json").read_text())
        assert fixture["synthetic"] is True
        assert fixture["payload"]["run_id"].startswith("demo-")
        if name == "invalid":
            with pytest.raises(ValidationError):
                ProfileResult.model_validate(fixture["payload"])
        else:
            result = ProfileResult.model_validate(fixture["payload"])
            assert result.status == ("ok" if name == "success" else "partial")


def test_schema_export_is_repeatable_with_complete_local_references(tmp_path):
    left, right = tmp_path / "left", tmp_path / "right"
    export_schemas(left)
    export_schemas(right)
    first = {p.name: p.read_bytes() for p in left.glob("*.json")}
    second = {p.name: p.read_bytes() for p in right.glob("*.json")}
    assert first == second
    assert "JourneyRequest.validation.schema.json" in first
    assert "StudyArea.serialization.schema.json" in first
    spec = json.loads(first["openapi.json"])
    assert spec["paths"]["/api/v1/analyses"]["post"]["responses"]["202"]
    assert spec["paths"]["/api/v1/analyses"]["post"]["responses"]["422"]["content"][
        "application/json"
    ]["schema"]["$ref"].endswith("ErrorEnvelope")
    assert "v2.AnalysisResult.validation.schema.json" in first
    assert "v2.AnalysisRequest.validation.schema.json" in first
    assert (
        len(
            spec["paths"]["/api/v1/analyses"]["post"]["requestBody"]["content"]["application/json"][
                "schema"
            ]["oneOf"]
        )
        == 4
    )
    assert "/api/v1/scenario-analyses" not in spec["paths"]
    assert "/api/v1/scenario-analyses" in json.loads(first["legacy-openapi.json"])["paths"]
    for content in first.values():
        document = json.loads(content)

        def visit(value):
            if isinstance(value, dict):
                if "$ref" in value:
                    ref = value["$ref"]
                    assert ref.startswith("#/")
                    target = document
                    for part in ref[2:].split("/"):
                        target = target[part.replace("~1", "/").replace("~0", "~")]
                for child in value.values():
                    visit(child)
            elif isinstance(value, list):
                for child in value:
                    visit(child)

        visit(document)
