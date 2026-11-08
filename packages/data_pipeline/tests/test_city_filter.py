import pytest

pytest.importorskip("shapely")
pytest.importorskip("pyproj")
pytest.importorskip("rasterio")

from a2_data_pipeline.build_city import _names_city  # noqa: E402


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
