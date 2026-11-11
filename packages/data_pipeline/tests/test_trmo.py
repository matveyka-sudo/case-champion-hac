"""Polyclinic rules on synthetic register rows (no real organisations)."""

from dataclasses import replace

import pytest
from a2_data_pipeline.trmo import Unit, categories, polyclinics

BASE = Unit(
    org_code="390001",
    org_name='ГОСУДАРСТВЕННОЕ БЮДЖЕТНОЕ УЧРЕЖДЕНИЕ ЗДРАВООХРАНЕНИЯ "СИНТЕТИЧЕСКАЯ БОЛЬНИЦА"',
    org_short="ГБ СИНТ",
    inn="0",
    ogrn="0",
    okopf_code="75203",
    okopf_name="Государственные бюджетные учреждения субъектов Российской Федерации",
    okfs_code="13",
    okfs_name="Собственность субъектов Российской Федерации",
    address="236000, Калининградская область, г. Калининград, ул. Тестовая, д. 1",
    address_guid="guid-1",
    unit_code="u1",
    unit_name="Поликлиника",
    unit_oid="oid",
    care_types="первичная врачебная медико-санитарная помощь, первичная доврачебная медико-санитарная помощь",
    conditions="Амбулаторно",
    profiles="терапии, неврологии",
    included="01.01.2026",
    excluded="",
)


@pytest.mark.parametrize(
    ("changes", "expected", "reason"),
    [
        ({}, {"adult_polyclinic"}, None),
        ({"unit_name": "Детское поликлиническое отделение № 2"}, {"child_polyclinic"}, None),
        ({"profiles": "терапии, педиатрии"}, {"adult_polyclinic", "child_polyclinic"}, None),
        ({"excluded": "01.10.2026"}, set(), "EXCLUDED_FROM_REGISTER"),
        ({"unit_name": "Консультативная поликлиника"}, set(), "NOT_A_POLYCLINIC"),
        ({"unit_name": "Хирургическое отделение"}, set(), "NOT_A_POLYCLINIC"),
        ({"conditions": "Стационарно"}, set(), "NOT_OUTPATIENT"),
        (
            {"care_types": "первичная специализированная медико-санитарная помощь"},
            set(),
            "NO_PRIMARY_PHYSICIAN_CARE",
        ),
        ({"okfs_code": "16"}, set(), "NOT_REGIONAL_OR_MUNICIPAL"),
        ({"okfs_code": "12"}, set(), "NOT_REGIONAL_OR_MUNICIPAL"),
    ],
)
def test_categories(changes: dict[str, str], expected: set[str], reason: str | None) -> None:
    assert categories(replace(BASE, **changes)) == (expected, reason)


def test_subdivisions_at_one_address_are_one_object() -> None:
    units = [
        BASE,
        replace(BASE, unit_code="u2", unit_name="Поликлиническое отделение № 2"),
        replace(BASE, unit_code="u3", address_guid="guid-2"),
        replace(BASE, unit_code="u4", okfs_code="16"),
    ]
    objects, stats = polyclinics(units)
    assert [len(o.units) for o in objects] == [2, 1]
    assert stats["rejected"] == {"NOT_REGIONAL_OR_MUNICIPAL": 1}
    assert {o.ownership for o in objects} == {"regional"}
    assert objects[0].record_hash() == polyclinics(units)[0][0].record_hash()
