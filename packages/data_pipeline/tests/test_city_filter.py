import pytest

pytest.importorskip("shapely")
pytest.importorskip("pyproj")
pytest.importorskip("rasterio")
pytest.importorskip("osmium")

from a2_data_pipeline.build_city import _names_city, _school_number  # noqa: E402


@pytest.mark.parametrize(
    ("address", "city", "expected"),
    [
        ("353440, г. Анапа, ул.Новороссийская, 1", "Новороссийск", False),
        ("353900, г. Новороссийск, ул. Советов, 10", "Новороссийск", True),
        ("238630, Калининградская обл., г. Полесск", "Калининград", False),
        ("236006, РФ, г. Калининград, ул. Фрунзе,4", "Калининград", True),
    ],
)
def test_city_is_a_whole_word(address: str, city: str, expected: bool) -> None:
    assert _names_city(address, city) is expected


@pytest.mark.parametrize(
    ("names", "expected"),
    [
        (("ГБОУ Школа № 1514",), "1514"),
        (("Школа №1514, корпус 3",), "1514"),
        (("ГБОУ Школа № 1514, дошкольное отделение",), "1514"),
        (("Лицей № 1535",), "1535"),
        (("Детский сад № 1234",), None),  # old kindergarten numbers repeat school numbers
        (("Школа № 5", "Школа № 7"), None),  # two numbers: ambiguous
        (("Школа «Покровский квартал»",), None),
    ],
)
def test_school_number_by_type(names: tuple[str, ...], expected: str | None) -> None:
    assert _school_number(*names) == expected
