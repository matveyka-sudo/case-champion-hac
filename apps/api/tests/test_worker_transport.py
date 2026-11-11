"""TEST_ONLY lossless bounded child transport; required nulls remain explicit."""

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from a2_api.workers import calculation
from a2_contracts.common import canonical_hash
from a2_contracts.v2.results import AnalysisResult
from test_product_runtime import claim_for, request_for
from test_product_runtime import transport_facts as transport_facts

ROOT = Path(__file__).resolve().parents[3]


class Capture:
    def send_bytes(self, value):
        self.value = value

    def close(self):
        pass


def test_real_child_transport_preserves_required_nulls_defaults_and_hash(
    transport_facts, monkeypatch
):
    raw = json.loads(
        (ROOT / "packages/contracts/fixtures/v2/urban_passenger-result.json").read_text()
    )
    expected = AnalysisResult.model_validate(raw["payload"])
    snapshot = transport_facts[0]
    monkeypatch.setenv("A2_CODE_VERSION", "TEST_ONLY")
    claim = claim_for(snapshot, request_for(snapshot))
    monkeypatch.setattr(
        calculation.importlib, "import_module", lambda _: SimpleNamespace(run=lambda _: expected)
    )
    capture = Capture()
    calculation._child(capture, "TEST_ONLY:run", claim.model_dump_json(), os.getppid())
    message = json.loads(capture.value)
    actual = AnalysisResult.model_validate(message["result"])
    assert actual == expected
    assert canonical_hash(actual.model_dump(mode="json")) == canonical_hash(
        expected.model_dump(mode="json")
    )
    assert all("reason_code" in metric for metric in message["result"]["metrics"])
    assert len(capture.value) <= calculation.MAX_RESULT_BYTES


def test_restored_full_moscow_demo_preserves_all_data_within_existing_wire_cap():
    from a2_api.adapters.demo_map import attach_demo_result
    from a2_contracts.v2.dataset import AcceptedData

    root = Path(
        os.environ.get(
            "A2_TEST_DEMO_RUNTIME", "/Users/kirito/waydex-full-runtime/demo-moscow-transport-v3"
        )
    )
    if not (root / "baseline-result.json").is_file():
        pytest.skip("Restore DEMO runtime and set A2_TEST_DEMO_RUNTIME for full2361-point replay")
    result = AnalysisResult.model_validate_json((root / "baseline-result.json").read_text())
    snapshot = AcceptedData.model_validate_json((root / "accepted-data.json").read_text())
    result = attach_demo_result(result, snapshot)
    wire = json.dumps(
        {
            "result_kind": "analysis_v2",
            "result": result.model_dump(mode="json", exclude_defaults=True),
        },
        allow_nan=False,
        separators=(",", ":"),
    ).encode()
    restored = AnalysisResult.model_validate(json.loads(wire)["result"])
    assert restored == result
    assert (
        canonical_hash(restored.model_dump(mode="json", exclude={"result_hash"}))
        == result.result_hash
    )
    assert len(wire) <= calculation.MAX_RESULT_BYTES
    points = [
        feature for feature in result.map_data.features if feature.entity_kind == "sample_point"
    ]
    excluded = [feature for feature in result.map_data.features if feature.entity_kind == "cell"]
    assert len(points) == len(snapshot.sample_points) == 2361
    assert len(excluded) == 2931
    excluded = [feature for feature in excluded if feature.population is not None]
    assert len(excluded) == 570
    assert len(snapshot.population_cells) == 2931
    assert (
        len(
            [
                score
                for score in restored.scores
                if score.name == "point_index" and score.value is not None
            ]
        )
        == 2361
    )
    cells = {cell.cell_id: cell for cell in snapshot.population_cells}
    assert all(
        feature.population == cells[feature.cell_id].population for feature in points + excluded
    )
