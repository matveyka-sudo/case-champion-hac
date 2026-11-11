import pytest
from a2_contracts.common import ContractModel, Identifier, Timestamp, Version, canonical_hash
from pydantic import ValidationError


class Sample(ContractModel):
    name: Identifier
    version: Version
    at: Timestamp
    value: float


def test_public_time_requires_offset_and_versions_do_not_coerce():
    data = dict(name="demo", version=1, at="2026-11-09T09:00:00+02:00", value=1)
    assert Sample.model_validate(data).at.utcoffset().total_seconds() == 7200
    for update in ({"at": "2026-11-09T09:00:00"}, {"version": "1"}, {"version": True}):
        with pytest.raises(ValidationError):
            Sample.model_validate(data | update)


@pytest.mark.parametrize("update", [{"name": " "}, {"value": float("nan")}, {"other": 1}])
def test_unsafe_values_are_rejected(update):
    with pytest.raises(ValidationError):
        Sample.model_validate(
            dict(name="demo", version=1, at="2026-11-09T09:00:00Z", value=1) | update
        )


def test_hash_ignores_mapping_order_but_preserves_operation_order():
    assert canonical_hash({"b": 2, "a": 1}) == canonical_hash({"a": 1, "b": 2})
    assert canonical_hash(["shift", "add"]) != canonical_hash(["add", "shift"])
    with pytest.raises(ValueError):
        canonical_hash({"score": float("nan")})
