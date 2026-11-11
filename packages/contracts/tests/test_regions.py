import pytest
from a2_contracts.regions import StudyArea
from pydantic import ValidationError


def test_demo_scope_is_explicit_subset_not_the_full_region_limit():
    area = StudyArea(
        area_id="demo-area",
        area_version=1,
        region_ids=["RU-47"],
        city_ids=["demo:a", "demo:b", "demo:c", "demo:d"],
        demo_city_ids=["demo:a", "demo:b", "demo:c"],
        timezone="Europe/Moscow",
        basis="scenario",
        evidence_ids=[],
    )
    assert len(area.city_ids) == 4
    assert len(area.demo_city_ids) == 3
    with pytest.raises(ValidationError):
        StudyArea.model_validate(area.model_dump() | {"demo_city_ids": ["unknown:city"]})


def test_new_region_requires_configuration_without_engine_special_case():
    area = StudyArea(
        area_id="demo-other",
        area_version=1,
        region_ids=["demo-region-2"],
        city_ids=["demo:x"],
        demo_city_ids=[],
        timezone="Asia/Yekaterinburg",
        basis="scenario",
        evidence_ids=[],
    )
    assert area.timezone == "Asia/Yekaterinburg"


def test_area_cannot_silently_deduplicate_cities():
    with pytest.raises(ValidationError):
        StudyArea(
            area_id="demo",
            area_version=1,
            region_ids=["RU-47"],
            city_ids=["demo:a", "demo:a"],
            demo_city_ids=[],
            timezone="Europe/Moscow",
            basis="scenario",
            evidence_ids=[],
        )
