"""Accepted public documents as snapshot evidence and FTS chunks (ML2).

Input is a plain-text extract of one official document and its provenance. The
output is a new AcceptedData version that carries the document as verified
evidence, plus EvidenceChunk JSON lines for the backend publication CLI.
Nothing is fetched here.

Nothing becomes verified by being built. The metadata must carry an explicit
acceptance record of the data owner: the official URL was opened and returned
this very file, and a person read this very text for personal data. The name
filter below is only a safety net (signatures, initials); it is a heuristic and
never counts as that review.
"""

import argparse
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from a2_contracts.common import canonical_hash
from a2_contracts.v2.dataset import AcceptedData, Evidence, Period
from a2_contracts.v2.retrieval import EvidenceChunk

from .manifest import Registry, load_registry

CHUNK_CHARACTERS = 700
# "IV. Цели" or "2. Проблемы": a numbered heading starts with a capital letter;
# "12 процентов на ..." is a table or list line, not a heading.
_HEADING = re.compile(r"^(?:[IVXLC]+|\d{1,2}(?:\.\d{1,2})*)\.\s+[А-ЯЁA-Z].{0,180}$")
_ROMAN = re.compile(r"^[IVXLC]+\.\s")
_PERSON = re.compile(
    r"\b[А-ЯЁ]\.\s?(?:[А-ЯЁ]\.\s?)?[А-ЯЁ][а-яё]+|\b[А-ЯЁ][а-яё]+\s[А-ЯЁ]\.\s?[А-ЯЁ]\.(?!\w)"
)


_WAIVABLE = frozenset({"source_url_check", "privacy_review"})


@dataclass(frozen=True)
class Acceptance:
    """The data owner's explicit decision; no field is inferred from the file."""

    status: str
    accepted_by: str | None
    accepted_at: date | None
    source_url_checked_at: datetime | None
    privacy_reviewed_at: date | None
    text_sha256: str | None
    # The owner may waive the URL comparison or the privacy reading, by name and
    # with a reason. A waiver is recorded as such; it never pretends the check ran.
    waived: frozenset[str] = frozenset()
    waived_reason: str | None = None

    @classmethod
    def load(cls, raw: object) -> "Acceptance":
        row = raw if isinstance(raw, dict) else {}

        def day(key: str) -> date | None:
            return date.fromisoformat(row[key]) if row.get(key) else None

        return cls(
            status=str(row.get("status", "pending")),
            accepted_by=row.get("accepted_by") or None,
            accepted_at=day("accepted_at"),
            source_url_checked_at=datetime.fromisoformat(row["source_url_checked_at"])
            if row.get("source_url_checked_at")
            else None,
            privacy_reviewed_at=day("privacy_reviewed_at"),
            text_sha256=row.get("text_sha256") or None,
            waived=frozenset(row.get("waived") or ()),
            waived_reason=row.get("waived_reason") or None,
        )

    def missing(self) -> list[str]:
        """What still separates this document from verified evidence."""
        unknown = sorted(self.waived - _WAIVABLE)
        if unknown:
            return [f"unknown waiver: {', '.join(unknown)}"]
        if self.waived and (not self.waived_reason or not self.accepted_by):
            return ["a waiver needs acceptance.waived_reason and acceptance.accepted_by"]
        return [
            reason
            for absent, reason in (
                (self.status != "accepted", "acceptance.status is not 'accepted'"),
                (not self.accepted_by, "acceptance.accepted_by (owner role) is empty"),
                (self.accepted_at is None, "acceptance.accepted_at is empty"),
                (
                    self.source_url_checked_at is None and "source_url_check" not in self.waived,
                    "source URL was not re-checked against the file hash",
                ),
                (
                    self.privacy_reviewed_at is None and "privacy_review" not in self.waived,
                    "the text was not read by a person for personal data",
                ),
                (not self.text_sha256, "acceptance.text_sha256 of the reviewed text is empty"),
            )
            if absent
        ]


def require_accepted(meta: "DocumentMeta", text: bytes | None = None) -> None:
    """Fail closed: operator input alone never becomes verified evidence."""
    missing = meta.acceptance.missing()
    if (
        not missing
        and text is not None
        and hashlib.sha256(text).hexdigest() != meta.acceptance.text_sha256
    ):
        missing = ["the text differs from the reviewed text (acceptance.text_sha256)"]
    if missing:
        raise ValueError("Document is not accepted: " + "; ".join(missing))


@dataclass(frozen=True)
class DocumentMeta:
    evidence_id: str
    source_id: str
    source_version: str
    url: str
    retrieved_at: datetime
    title: str
    document_date: date
    valid_from: date
    valid_to: date
    license: str
    file_sha256: str
    acceptance: Acceptance

    @classmethod
    def load(cls, path: Path) -> "DocumentMeta":
        raw = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            evidence_id=raw["evidence_id"],
            source_id=raw["source_id"],
            source_version=raw["source_version"],
            url=raw["url"],
            retrieved_at=datetime.fromisoformat(raw["retrieved_at"]),
            title=raw["title"],
            document_date=date.fromisoformat(raw["document_date"]),
            valid_from=date.fromisoformat(raw["valid_from"]),
            valid_to=date.fromisoformat(raw["valid_to"]),
            license=raw["license"],
            file_sha256=raw["file_sha256"],
            acceptance=Acceptance.load(raw.get("acceptance")),
        )


def clean_lines(text: str) -> tuple[list[str], int]:
    """Normalized non-empty lines without personal names; returns the dropped count."""
    kept: list[str] = []
    dropped = 0
    for line in text.replace(" ", " ").splitlines():
        line = " ".join(line.split())
        if not line or line.isdigit():
            continue
        if _PERSON.search(line):
            dropped += 1
            continue
        kept.append(line)
    return kept, dropped


def sections(lines: list[str], start: str | None = None) -> list[tuple[str, str]]:
    """Split on numbered or Roman headings; text before ``start`` (the document body,
    after an order's preamble) and before the first heading is skipped."""
    if start is not None:
        if start not in lines:
            raise ValueError("Start line not found in the document text")
        lines = lines[lines.index(start) :]
    result: list[tuple[str, list[str]]] = []
    parents: list[str] = []
    continued = False
    for line in lines:
        if _HEADING.match(line) and not line.endswith((",", ";")):
            roman = _ROMAN.match(line) is not None
            parents.append(line if roman else (parents[-1] if parents else ""))
            result.append((line, []))
            continued = not line.endswith(".")
        elif result and continued and not result[-1][1] and line[:1].islower():
            # A wrapped heading line: "VII. Цифровая трансформация, а также" / "развитие".
            result[-1] = (_join([result[-1][0], line]), [])
            if _ROMAN.match(result[-1][0]):
                parents[-1] = result[-1][0]
            continued = not line.endswith(".")
        elif result:
            result[-1][1].append(line)
            continued = False
    # "V. Принципы ... — 1. Общие принципы": the parent part names the context.
    return [
        ((title if parent in ("", title) else f"{parent} — {title}")[:300], _join(body))
        for (title, body), parent in zip(result, parents, strict=True)
        if body
    ]


def _join(lines: list[str]) -> str:
    """Join PDF lines; a line ending in a hyphen continues the same word."""
    text = ""
    for line in lines:
        if text.endswith("-") and line[:1].islower():
            text += line
        else:
            text = f"{text} {line}" if text else line
    return text


def _pieces(body: str, limit: int) -> list[str]:
    sentences = re.split(r"(?<=[.;:!?])\s+", body)
    pieces: list[str] = []
    current = ""
    for sentence in sentences:
        while len(sentence) > limit:
            pieces += [current] if current else []
            current = ""
            pieces.append(sentence[:limit])
            sentence = sentence[limit:]
        if current and len(current) + 1 + len(sentence) > limit:
            pieces.append(current)
            current = sentence
        else:
            current = f"{current} {sentence}".strip()
    if current:
        pieces.append(current)
    return [piece for piece in pieces if piece.strip()]


def reviewed_pieces(text: str, start: str | None = None) -> list[tuple[str, str]]:
    """(section, chunk text) pairs exactly as build() cuts the reviewed text."""
    lines, _ = clean_lines(text)
    return [
        (title, piece)
        for title, body in sections(lines, start)
        for piece in _pieces(body, CHUNK_CHARACTERS)
    ]


def document_evidence(meta: DocumentMeta) -> Evidence:
    return Evidence(
        evidence_id=meta.evidence_id,
        source_id=meta.source_id,
        source_version=meta.source_version,
        url=meta.url,  # type: ignore[arg-type]
        retrieved_at=meta.retrieved_at,
        valid_period=Period(start=meta.valid_from, end=meta.valid_to),
        license=meta.license,
        content_hash=meta.file_sha256,
        verified=True,
    )


def attach(snapshot: AcceptedData, meta: DocumentMeta, snapshot_id: str) -> AcceptedData:
    """A new snapshot version that also cites the document; existing rows are untouched."""
    require_accepted(meta)
    if snapshot_id == snapshot.snapshot_id:
        raise ValueError("A changed snapshot needs a new snapshot_id")
    known = snapshot.source_versions.get(meta.source_id)
    if known is not None and known != meta.source_version:
        raise ValueError("Snapshot already pins another version of this source")
    raw = snapshot.model_dump(mode="json")
    raw["snapshot_id"] = snapshot_id
    raw["source_versions"] = raw["source_versions"] | {meta.source_id: meta.source_version}
    raw["evidence"] = [row for row in raw["evidence"] if row["evidence_id"] != meta.evidence_id] + [
        document_evidence(meta).model_dump(mode="json")
    ]
    return AcceptedData.model_validate(raw)


def chunks(
    snapshot: AcceptedData, meta: DocumentMeta, parts: list[tuple[str, str]]
) -> list[EvidenceChunk]:
    require_accepted(meta)
    rows: list[EvidenceChunk] = []
    for title, body in parts:
        for piece in _pieces(body, CHUNK_CHARACTERS):
            index = len(rows) + 1
            rows.append(
                EvidenceChunk(
                    schema_version="2",
                    chunk_id=f"{meta.evidence_id}:{index:04d}",
                    evidence_id=meta.evidence_id,
                    snapshot_id=snapshot.snapshot_id,
                    source_id=meta.source_id,
                    source_version=meta.source_version,
                    url=meta.url,  # type: ignore[arg-type]
                    retrieved_at=meta.retrieved_at,
                    title=meta.title[:300],
                    section=title,
                    document_date=meta.document_date,
                    valid_from=meta.valid_from,
                    valid_to=meta.valid_to,
                    territory_ids=list(snapshot.geographic_scope)[:20],
                    text=piece,
                    content_hash=canonical_hash(piece),
                    public_nonpersonal_reviewed=True,
                )
            )
    return rows


def build(
    text_path: Path,
    meta_path: Path,
    snapshot_path: Path,
    snapshot_id: str,
    out: Path,
    start: str | None = None,
    registry: Registry | None = None,
) -> dict[str, object]:
    meta = DocumentMeta.load(meta_path)
    require_accepted(meta, text_path.read_bytes())
    if registry is not None and registry.get(meta.source_id).status != "accepted":
        raise ValueError("Document source is not accepted in the source registry")
    lines, dropped = clean_lines(text_path.read_text(encoding="utf-8"))
    parts = sections(lines, start)
    if not parts:
        raise ValueError("No numbered sections found in the document text")
    source = AcceptedData.model_validate_json(snapshot_path.read_text(encoding="utf-8"))
    accepted = attach(source, meta, snapshot_id)
    rows = chunks(accepted, meta, parts)
    out.mkdir(parents=True, exist_ok=True)
    snapshot_text = json.dumps(accepted.model_dump(mode="json"), ensure_ascii=False, indent=1)
    (out / "snapshot.json").write_text(snapshot_text + "\n", encoding="utf-8")
    (out / "chunks.jsonl").write_text(
        "".join(row.model_dump_json() + "\n" for row in rows), encoding="utf-8"
    )
    return {
        "snapshot_id": accepted.snapshot_id,
        "content_hash": canonical_hash(accepted.model_dump(mode="json")),
        "snapshot_sha256": hashlib.sha256((snapshot_text + "\n").encode()).hexdigest(),
        "sections": len(parts),
        "chunks": len(rows),
        "dropped_name_lines": dropped,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    clean = sub.add_parser("clean", help="write the reviewed text without personal names")
    clean.add_argument("--text", type=Path, required=True)
    clean.add_argument("--out", type=Path, required=True)
    make = sub.add_parser("build")
    make.add_argument("--text", type=Path, required=True)
    make.add_argument("--meta", type=Path, required=True)
    make.add_argument("--snapshot", type=Path, required=True)
    make.add_argument("--snapshot-id", required=True)
    make.add_argument("--out", type=Path, required=True)
    make.add_argument("--start", help="exact first line of the document body")
    args = parser.parse_args()
    if args.command == "clean":
        lines, dropped = clean_lines(args.text.read_text(encoding="utf-8"))
        args.out.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(json.dumps({"lines": len(lines), "dropped_name_lines": dropped}))
        return
    report = build(
        args.text,
        args.meta,
        args.snapshot,
        args.snapshot_id,
        args.out,
        args.start,
        registry=load_registry(),
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
