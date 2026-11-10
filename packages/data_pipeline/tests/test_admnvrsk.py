"""Novorossiysk healthcare page rules on a synthetic table (no real contacts)."""

from a2_data_pipeline import admnvrsk

PAGE = """
<table>
<tr><td>№</td><td>Наименование</td><td>Адрес</td><td>Профиль</td><td>Часы</td></tr>
<tr><td> 6 </td><td> ГБУЗ «Городская поликлиника №1 г. Новороссийска» МЗ КК </td>
<td> 353900, Краснодарский край, г. Новороссийск,
<br> ул. Тестовая, 6. Поликлиническое отделение N 2 ГБУЗ "Городская поликлиника N1 "МЗ КК, Анапское шоссе, 1/3
<br> Электронный адрес: <a href="mailto:x@example.org">x@example.org</a>
<br> Приемная главного врача: +7 (000) 00-00-00 </td>
<td> амбулаторная помощь </td><td> ежедневно </td></tr>
<tr><td> 12 </td><td> ГБУЗ «Детская городская поликлиника г. Новороссийска» МЗ КК </td>
<td> 353900, Краснодарский край, г. Новороссийск,<br> ул. Советов, 10<br> Регистратура: +7 (000) 00-00-01 </td>
<td> амбулаторная помощь </td><td> ежедневно </td></tr>
<tr><td> 15 </td><td> ГАУЗ «Стоматологическая поликлиника г. Новороссийска» МЗ КК </td>
<td> 353900, г. Новороссийск, ул. Советов, 23 </td><td> стоматология </td><td></td></tr>
<tr><td> 20 </td><td> ЧУЗ «РЖД-Медицина» поликлиника 7 на станции Новороссийск </td>
<td> 353900, г. Новороссийск, ул. Вокзальная, 1 </td><td> амбулаторная помощь </td><td></td></tr>
</table>
"""


def test_parse_keeps_public_polyclinics_and_their_units() -> None:
    rows, stats = admnvrsk.parse(PAGE)
    assert [(r.category, r.unit is not None) for r in rows] == [
        ("adult_polyclinic", False),
        ("adult_polyclinic", True),
        ("child_polyclinic", False),
    ]
    assert rows[0].address == "353900, Краснодарский край, г. Новороссийск, ул. Тестовая, 6"
    assert rows[1].address.endswith("Анапское шоссе, 1/3")
    assert all(r.ownership == "regional" for r in rows)
    assert stats["skipped_rows"] == {"not_a_city_polyclinic": 2, "not_public": 1}
    assert "example.org" not in repr(rows) and "+7" not in repr(rows)
    assert len({r.object_id for r in rows}) == 3
