"""Reviewed document evidence and FTS chunks; synthetic text only."""

import hashlib
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


def meta(tmp_path, text=None, acceptance=None, **change):
    reviewed = hashlib.sha256(text.read_bytes()).hexdigest() if text else "0" * 64
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
        "acceptance": {
            "status": "accepted",
            "accepted_by": "ML2",
            "accepted_at": "2026-11-12",
            "source_url_checked_at": "2026-11-12T00:00:00+00:00",
            "privacy_reviewed_at": "2026-11-12",
            "text_sha256": reviewed,
        }
        | (acceptance or {}),
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
        text,
        meta(tmp_path, text),
        source,
        "synthetic-snapshot-docs1",
        tmp_path,
        "I. Общие положения",
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
        documents.build(text, meta(tmp_path, text), source, "v2", tmp_path)


@pytest.mark.parametrize(
    "change,reason",
    [
        ({"status": "pending"}, "acceptance.status"),
        ({"accepted_by": None}, "accepted_by"),
        ({"accepted_at": None}, "accepted_at"),
        ({"source_url_checked_at": None}, "source URL was not re-checked"),
        ({"privacy_reviewed_at": None}, "not read by a person"),
        ({"text_sha256": None}, "text_sha256"),
    ],
)
def test_operator_input_without_owner_acceptance_never_becomes_verified(tmp_path, change, reason):
    text = tmp_path / "document.txt"
    text.write_text(TEXT)
    source = tmp_path / "accepted.json"
    source.write_text(snapshot().model_dump_json())
    path = meta(tmp_path, text, acceptance=change)
    info = documents.DocumentMeta.load(path)
    with pytest.raises(ValueError, match=reason):
        documents.build(text, path, source, "v2", tmp_path / "out", "I. Общие положения")
    # No partial output, and the lower-level builders refuse as well.
    assert not (tmp_path / "out").exists()
    with pytest.raises(ValueError, match="not accepted"):
        documents.attach(snapshot(), info, "v2")
    with pytest.raises(ValueError, match="not accepted"):
        documents.chunks(snapshot(), info, [("I. Раздел", "Текст.")])


def test_missing_acceptance_block_means_pending(tmp_path):
    path = meta(tmp_path)
    raw = json.loads(path.read_text())
    del raw["acceptance"]
    path.write_text(json.dumps(raw))
    info = documents.DocumentMeta.load(path)
    assert info.acceptance.status == "pending" and len(info.acceptance.missing()) == 6


def test_acceptance_is_bound_to_the_reviewed_text(tmp_path):
    text = tmp_path / "document.txt"
    text.write_text(TEXT)
    source = tmp_path / "accepted.json"
    source.write_text(snapshot().model_dump_json())
    path = meta(tmp_path, text)
    text.write_text(TEXT + "III. Новый раздел\nДобавлено после проверки.\n")
    with pytest.raises(ValueError, match="differs from the reviewed text"):
        documents.build(text, path, source, "v2", tmp_path / "out", "I. Общие положения")


def test_the_name_filter_is_a_safety_net_not_a_privacy_review():
    # A full name without initials passes the heuristic: only a person's review catches it.
    lines, dropped = documents.clean_lines("Ответственный Пётр Петрович Петров\nТранспорт")
    assert dropped == 0 and lines[0].startswith("Ответственный")


def test_source_must_be_accepted_in_the_registry(tmp_path):
    from a2_data_pipeline.manifest import load_registry

    text = tmp_path / "document.txt"
    text.write_text(TEXT)
    source = tmp_path / "accepted.json"
    source.write_text(snapshot().model_dump_json())
    registry = load_registry()
    pending = next(entry for entry in registry.entries if entry.status != "accepted")
    path = meta(tmp_path, text, source_id=pending.source_id)
    with pytest.raises(ValueError, match="not accepted in the source registry"):
        documents.build(text, path, source, "v2", tmp_path / "out", registry=registry)


def test_repository_document_is_not_accepted_yet():
    """3363-r stays unpublished until its owner records the URL and privacy checks."""
    path = ROOT / "data/documents/transport_strategy/3363-r-2021-11-27.meta.json"
    info = documents.DocumentMeta.load(path)
    text = ROOT / "data/documents/transport_strategy/3363-r-2021-11-27.txt"
    assert info.acceptance.text_sha256 == hashlib.sha256(text.read_bytes()).hexdigest()
    if info.acceptance.status != "accepted":
        with pytest.raises(ValueError, match="not accepted"):
            documents.require_accepted(info, text.read_bytes())
    else:
        documents.require_accepted(info, text.read_bytes())


@pytest.mark.parametrize(
    "acceptance,ok",
    [
        (
            {
                "source_url_checked_at": None,
                "waived": ["source_url_check"],
                "waived_reason": "owner",
            },
            True,
        ),
        (
            {"privacy_reviewed_at": None, "waived": ["privacy_review"], "waived_reason": "owner"},
            True,
        ),
        ({"privacy_reviewed_at": None, "waived": ["privacy_review"]}, False),
        (
            {
                "privacy_reviewed_at": None,
                "waived": ["privacy_review"],
                "waived_reason": "owner",
                "accepted_by": None,
            },
            False,
        ),
        ({"privacy_reviewed_at": None, "waived": ["everything"], "waived_reason": "owner"}, False),
        (
            {"privacy_reviewed_at": None, "waived": ["source_url_check"], "waived_reason": "owner"},
            False,
        ),
    ],
    ids=["url_waived", "privacy_waived", "no_reason", "no_owner", "unknown_waiver", "wrong_waiver"],
)
def test_owner_waiver_is_explicit_and_limited(tmp_path, acceptance, ok):
    text = tmp_path / "document.txt"
    text.write_text(TEXT)
    info = documents.DocumentMeta.load(meta(tmp_path, text, acceptance=acceptance))
    if ok:
        documents.require_accepted(info, text.read_bytes())
    else:
        with pytest.raises(ValueError, match="not accepted"):
            documents.require_accepted(info, text.read_bytes())
