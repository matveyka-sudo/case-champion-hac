"""Reviewed document evidence and FTS chunks; synthetic text only."""

import json
from pathlib import Path

import pytest
from a2_contracts.common import canonical_hash
from a2_contracts.v2.dataset import AcceptedData
from a2_contracts.v2.retrieval import EvidenceChunk
from a2_data_pipeline import documents

ROOT = Path(__file__).resolve().parents[3]
TEXT = """РАСПОРЯЖЕНИЕ
от 1 ноября 2026 г.
1. Утвердить прилагаемый синтетический документ.
Председатель Правительства
Российской Федерации М.Иванов
УТВЕРЖДЕН
I. Общие положения
Синтетический документ описывает транспортную доступность городов.
Пересадка между видами транспорта сокращает полное время поездки.
II. Цели
Повышение связности территорий; развитие пересадочных узлов.
Ответственный исполнитель Петров П.П.
"""


def meta(tmp_path, **change):
    raw = {
        "evidence_id": "synthetic-document:2026",
        "source_id": "synthetic_document",
        "source_version": "2026-11-01",
        "url": "https://example.org/document.pdf",
        "retrieved_at": "2026-11-12T00:00:00+00:00",
        "title": "Синтетический документ",
        "document_date": "2026-11-01",
        "valid_from": "2026-11-01",
        "valid_to": "2026-11-30",
        "license": "public-domain-official-act",
        "file_sha256": "0" * 64,
    } | change
    path = tmp_path / "meta.json"
    path.write_text(json.dumps(raw))
    return path


def snapshot():
    raw = json.loads((ROOT / "packages/contracts/fixtures/v2/accepted-data.json").read_text())
    return AcceptedData.model_validate(raw.get("payload", raw))


def test_names_and_preamble_never_reach_sections():
    lines, dropped = documents.clean_lines(TEXT)
    assert dropped == 2
    assert not any("Иванов" in line or "Петров" in line for line in lines)
    parts = documents.sections(lines, start="I. Общие положения")
    assert [title for title, _ in parts] == ["I. Общие положения", "II. Цели"]
    assert "пересадочных узлов" in parts[-1][1] and "Утвердить" not in str(parts)
    # Without a start line the order's preamble item is a section of its own.
    assert documents.sections(lines)[0][0].startswith("1. Утвердить")
    with pytest.raises(ValueError):
        documents.sections(lines, start="IV. Нет такого")


@pytest.mark.parametrize(
    "line", ["М.Иванов", "М. В. Иванов", "Иванов М.В.", "подписал И.И.Сидоров"]
)
def test_name_patterns_are_dropped(line):
    assert documents.clean_lines(line + "\nТранспорт")[0] == ["Транспорт"]


def test_attach_adds_verified_document_evidence_as_a_new_version(tmp_path):
    source = snapshot()
    info = documents.DocumentMeta.load(meta(tmp_path))
    changed = documents.attach(source, info, "synthetic-snapshot-docs1")
    added = next(e for e in changed.evidence if e.evidence_id == info.evidence_id)
    assert added.verified and added.source_version == info.source_version
    assert changed.source_versions[info.source_id] == info.source_version
    assert len(changed.evidence) == len(source.evidence) + 1
    assert canonical_hash(changed.model_dump(mode="json")) != canonical_hash(
        source.model_dump(mode="json")
    )
    with pytest.raises(ValueError):
        documents.attach(source, info, source.snapshot_id)
    pinned = documents.attach(source, info, "v2")
    other = documents.DocumentMeta.load(meta(tmp_path, source_version="other"))
    with pytest.raises(ValueError):
        documents.attach(pinned, other, "v3")


def test_build_writes_publishable_chunks(tmp_path):
    text = tmp_path / "document.txt"
    text.write_text(TEXT + "III. Длинный раздел\n" + "Связность территорий растёт. " * 80)
    source = tmp_path / "accepted.json"
    source.write_text(snapshot().model_dump_json())
    report = documents.build(
        text, meta(tmp_path), source, "synthetic-snapshot-docs1", tmp_path, "I. Общие положения"
    )
    accepted = AcceptedData.model_validate_json((tmp_path / "snapshot.json").read_text())
    rows = [
        EvidenceChunk.model_validate_json(line)
        for line in (tmp_path / "chunks.jsonl").read_text().splitlines()
    ]
    assert report["chunks"] == len(rows) > 3 and report["dropped_name_lines"] == 2
    assert report["content_hash"] == canonical_hash(accepted.model_dump(mode="json"))
    assert len({row.chunk_id for row in rows}) == len(rows)
    evidence = {e.evidence_id: e for e in accepted.evidence}
    for row in rows:
        source_row = evidence[row.evidence_id]
        assert row.snapshot_id == accepted.snapshot_id and len(row.text) <= 700
        assert (row.source_version, row.url, row.retrieved_at) == (
            source_row.source_version,
            source_row.url,
            source_row.retrieved_at,
        )
        assert set(row.territory_ids) <= set(accepted.geographic_scope)
        assert "Иванов" not in row.text and "Петров" not in row.text


def test_table_lines_wraps_and_long_headings():
    lines, _ = documents.clean_lines(
        "I. Общие положения\n"
        "12 процентов на железнодорожном транспорте\n"
        "транспортно-\n"
        "экономический баланс\n"
        "VII. Цифровая трансформация, а также\n"
        "технологическое развитие\n"
        "Текст раздела.\n"
        "1. Реализация стратегии\n"
        "Пункт.\n"
    )
    parts = documents.sections(lines)
    assert [title for title, _ in parts] == [
        "I. Общие положения",
        "VII. Цифровая трансформация, а также технологическое развитие",
        "VII. Цифровая трансформация, а также технологическое развитие — 1. Реализация стратегии",
    ]
    assert (
        parts[0][1] == "12 процентов на железнодорожном транспорте транспортно-экономический баланс"
    )


def test_text_without_sections_is_rejected(tmp_path):
    text = tmp_path / "document.txt"
    text.write_text("Без нумерованных разделов.")
    source = tmp_path / "accepted.json"
    source.write_text(snapshot().model_dump_json())
    with pytest.raises(ValueError):
        documents.build(text, meta(tmp_path), source, "v2", tmp_path)
