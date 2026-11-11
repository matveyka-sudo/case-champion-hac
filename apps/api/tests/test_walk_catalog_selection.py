import pytest
from a2_api.adapters.walk_batches import PhysicalPair, selected_pairs
from a2_api.core.errors import DomainError


def test_large_catalog_selects_only_the_requested_category():
    catalog = [PhysicalPair(point_id=f"p-{i}", destination_id=category)
               for i in range(3000) for category in ("school", "clinic")]
    selected = selected_pairs(catalog, {"school"})
    assert len(selected) == 3000
    assert {target for _, target in selected} == {"school"}
    with pytest.raises(DomainError, match="WALK_BATCH_PAIR_BUDGET_EXCEEDED"):
        selected_pairs(catalog, {"school", "clinic"})


def test_execution_boundary_is_not_raised_with_the_catalog_limit():
    catalog = [PhysicalPair(point_id=f"p-{i}", destination_id="school") for i in range(4096)]
    assert len(selected_pairs(catalog, {"school"})) == 4096
    catalog.append(PhysicalPair(point_id="extra", destination_id="school"))
    with pytest.raises(DomainError, match="WALK_BATCH_PAIR_BUDGET_EXCEEDED"):
        selected_pairs(catalog, {"school"})
