"""Novorossiysk founder's kindergarten list on synthetic cards (no real contacts)."""

from a2_data_pipeline import gorono

CARD = """<html><body><table>
<tr><td>Управление Образования г. Новороссийска</td></tr>
<tr><td>Муниципальное бюджетное дошкольное образовательное учреждение детский сад № 7</td></tr>
<tr><td>Заведующий: Петрова Пётр Петровна</td></tr>
<tr><td>Адрес: 353900 г.Новороссийск, ул. Тестовая, 5</td></tr>
<tr><td>Телефон: 00-00-00</td></tr><tr><td>E-mail: x@example.org</td></tr>
<tr><td>https://example.org/</td></tr>
</table></body></html>"""
NO_LABEL = CARD.replace("Адрес: 353900", "353900").replace("№ 7", "№ 8")


def test_card_keeps_name_and_address_only() -> None:
    record = gorono.parse_card("7", CARD)
    assert record is not None
    assert record.name.endswith("детский сад № 7")
    assert record.address == "353900 г.Новороссийск, ул. Тестовая, 5"
    assert record.ownership == "municipal"
    assert record.object_id == "kindergarten-gorono-7"
    text = repr(record)
    assert "Петров" not in text and "example.org" not in text and "00-00-00" not in text


def test_address_without_label_and_list_numbers() -> None:
    record = gorono.parse_card("8", NO_LABEL)
    assert record is not None and record.address.startswith("353900")
    page = (
        """<a href="javascript:open('dou/blank.php?nom=7')">ДОУ №7</a>"""
        """<a href="javascript:open('dou/blank.php?nom=д/д')">д/д</a>"""
    )
    assert gorono.list_numbers(page) == ["7"]  # the children's home is not a kindergarten
