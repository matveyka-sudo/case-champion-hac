"""TEST_ONLY: the disabled HTTP service keeps selected facts beyond the first page."""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from a2_api.assistant.explanations import explain
from a2_contracts.v2.assistant import ExplanationRequest, ExplanationSelection
from a2_contracts.v2.results import AnalysisResult


@pytest.mark.asyncio
async def test_disabled_assistant_receives_selected_metric_after_first_twenty():
    root = Path(__file__).resolve().parents[3]
    saved = AnalysisResult.model_validate(
        json.loads(
            (root / "packages/contracts/fixtures/v2/urban_passenger-result.json").read_text()
        )["payload"]
    )
    source = saved.metrics[0]
    metrics = [source]
    for index in range(1, 21):
        metrics.append(
            source.model_copy(
                update={
                    "metric_id": "TEST_ONLY-metric-" + str(index),
                    "subject": source.subject.model_copy(
                        update={"kind": "sample_point", "point_id": "TEST_ONLY-point-" + str(index)}
                    ),
                }
            )
        )
    saved = saved.model_copy(update={"metrics": metrics})
    service = SimpleNamespace(analysis=AsyncMock(return_value=saved), assistant=None)
    result = await explain(
        service,
        saved.analysis_id,
        "TEST_ONLY-scope",
        "TEST_ONLY-request",
        ExplanationRequest(
            selection=ExplanationSelection(
                entity_kind="sample_point", entity_id="TEST_ONLY-point-20"
            )
        ),
    )
    assert result.status == "template" and result.reason_code == "DISABLED"
    assert any(
        claim.metric_id == "TEST_ONLY-metric-20"
        for finding in result.findings
        for claim in finding.claims
    )
