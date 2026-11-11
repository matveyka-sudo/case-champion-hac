from copy import deepcopy

from apps.api.scripts.plan_observed_categories import plan


def endpoint(identity, kind, lat, categories=()):
    return {
        "entity_id": identity, "entity_kind": kind, "categories": list(categories),
        "connector": {"traversable": True, "seconds": 1,
                      "physical_location": {"lon": 20, "lat": lat}},
    }


def test_unresolved_nearest_target_is_not_a_route_or_completeness_proof():
    rows = [endpoint("point", "point", 54), endpoint("unknown-point", "point", 54),
            endpoint("nearest-unresolved", "destination", 54, ["school"]),
            endpoint("known", "destination", 54.1, ["school"])]
    before = deepcopy(rows)
    audit = [{"entity_id": row["entity_id"], "state": "matched" if row["entity_id"]
              in {"point", "known"} else "unknown"} for row in rows]
    result = plan(rows, audit, ["school", "airport"])
    assert result["pairs"] == [{"point_id": "point", "destination_id": "known"}]
    assert result["categories"]["airport"]["planned_origins"] == 0
    assert result["minimum_proven"] is False
    assert result["complete_catalog_search"] is False
    assert result["unknown_points_must_remain_in_result"] is True
    assert rows == before


def test_shared_target_is_deduplicated_and_order_does_not_change_selection():
    rows = [endpoint("point", "point", 54),
            endpoint("b", "destination", 54.1, ["school", "kindergarten"]),
            endpoint("a", "destination", 54.1, ["school", "kindergarten"])]
    audit = [{"entity_id": row["entity_id"], "state": "matched"} for row in rows]
    result = plan(rows, audit, ["school", "kindergarten"])
    assert result == plan(list(reversed(rows)), audit, ["kindergarten", "school"])
    assert result["pairs"] == [{"point_id": "point", "destination_id": "a"}]
