"""Reference existence is insufficient: its directed task scope must be proven."""

import pytest
from a2_contracts.v2.results import AnalysisResult
from pydantic import ValidationError

from apps.api.tests.refscope4_handlers import raw_case


@pytest.mark.parametrize(
    "mode",
    [
        "fixed_control",
        "journey_control",
        "city_ids_control",
        "fixed_broad_control",
        "singleton_journey_control",
        "partial_value",
    ],
)
def test_applicable_reference_and_honest_partial_controls(mode):
    assert AnalysisResult.model_validate(raw_case(mode)).metrics[0].value == 1800


@pytest.mark.parametrize(
    "mode",
    [
        "foreign",
        "reverse",
        "superset",
        "partial",
        "missing_binding",
        "foreign_unfixed",
        "fixed_foreign_journey",
        "narrower",
        "unlinked_multi_scope",
        "missing_path",
    ],
)
def test_full_index_rejects_unproven_reference_scope(mode):
    with pytest.raises(ValidationError):
        AnalysisResult.model_validate(raw_case(mode))
