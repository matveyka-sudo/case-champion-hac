import pytest
from a2_contracts.v2.requests import AnalysisRequest, DeliveryCargoProfile, parse_analysis_request
from pydantic import TypeAdapter, ValidationError


def base(kind="urban_passenger"):
    return dict(
        schema_version="2",
        analysis_kind=kind,
        departure_at="2026-11-09T08:15:00+03:00",
        traffic_mode="static",
    )


def cases():
    return [
        base()
        | dict(
            city_id="demo-city",
            passenger_mode="walk",
            categories=["school"],
            priorities={"school": "high"},
        ),
        base("intercity_passenger")
        | dict(territory_level="city", origin_id="demo-a", destination_id="demo-b"),
        base("urban_cargo")
        | dict(city_id="demo-city", cargo_profile_id="demo-general", direction="outbound"),
        base("intercity_cargo")
        | dict(
            territory_level="region",
            origin_id="demo-a",
            destination_id="demo-b",
            cargo_profile_id="demo-general",
            cargo_transport="multimodal",
        ),
    ]


def test_all_four_kinds_are_distinct_without_legacy_profile_ids():
    assert [parse_analysis_request(case).analysis_kind for case in cases()] == [
        "urban_passenger",
        "intercity_passenger",
        "urban_cargo",
        "intercity_cargo",
    ]
    assert (
        TypeAdapter(AnalysisRequest).json_schema()["discriminator"]["propertyName"]
        == "analysis_kind"
    )


@pytest.mark.parametrize(
    "update",
    [
        {"schema_version": 2},
        {"departure_at": "2026-11-09T08:15:00"},
        {"departure_at": "2026-11-09T08:16:00+03:00"},
        {"categories": []},
        {"categories": ["school", "school"]},
        {"priorities": {}},
        {"priorities": {"school": 3}},
        {"priorities": {"school": "high", "airport": "low"}},
        {"budget": 100},
        {"H": 30},
        {"weights": [1]},
        {"cargo_profile_id": "demo"},
    ],
)
def test_passenger_request_rejects_ambiguous_or_unapproved_inputs(update):
    with pytest.raises(ValidationError):
        parse_analysis_request(cases()[0] | update)


def test_same_territory_and_inapplicable_fields_are_rejected():
    for data in [
        cases()[1] | {"destination_id": "demo-a"},
        cases()[1] | {"categories": ["school"]},
        cases()[2] | {"direction": None},
        cases()[2] | {"passenger_mode": "walk"},
    ]:
        with pytest.raises(ValidationError):
            parse_analysis_request(data)


def test_pipeline_is_not_food_class_or_a_generic_delivery_mode():
    data = cases()[3] | dict(cargo_transport="pipeline", pipeline_product="oil_products")
    assert parse_analysis_request(data).pipeline_product == "oil_products"
    for update in (
        {"pipeline_product": None},
        {"pipeline_product": "oilprod"},
        {"cargo_transport": "multimodal"},
        {"food_class": "food"},
    ):
        with pytest.raises(ValidationError):
            parse_analysis_request(data | update)


def test_independent_cargo_requirements_do_not_collapse_into_one_boolean():
    profile = DeliveryCargoProfile(
        profile_id="demo-food",
        kind="delivery",
        food_class="food",
        urgency="ordinary",
        temperature_requirements={"controlled": True, "min_c": 2, "max_c": 8},
        handling_requirements=["demo-refrigerated-handling"],
        vehicle_constraints={"max_height_m": 4, "max_weight_tonnes": 20, "restrictions": []},
    )
    assert profile.urgency == "ordinary"
    assert profile.temperature_requirements.controlled
    assert profile.perishable is False

    raw = DeliveryCargoProfile.model_validate(
        profile.model_dump() | {"food_class": "raw", "perishable": True}
    )
    assert raw.food_class == "raw"
    assert raw.perishable is True
    assert raw.urgency == "ordinary"
    assert raw.temperature_requirements == profile.temperature_requirements


@pytest.mark.parametrize(
    "kind,field,values",
    [
        ("intercity_passenger", "passenger_modes", []),
        ("intercity_passenger", "passenger_modes", ["rail", "rail"]),
        ("urban_cargo", "freight_node_types", []),
        ("urban_cargo", "freight_node_types", ["port", "port"]),
        ("intercity_cargo", "cargo_modes", []),
        ("intercity_cargo", "cargo_modes", ["road", "road"]),
    ],
)
def test_explicit_mode_and_node_filters_are_nonempty_unique(kind, field, values):
    import json
    from pathlib import Path

    from a2_contracts.v2.requests import parse_analysis_request

    fixture = Path(__file__).resolve().parents[1] / "fixtures/v2" / f"{kind}-request.json"
    payload = json.loads(fixture.read_text())["payload"]
    with pytest.raises(ValidationError):
        parse_analysis_request(payload | {field: values})
