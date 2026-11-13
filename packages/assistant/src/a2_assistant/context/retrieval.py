"""One gated, scoped documentary lookup through the existing read-only backend.

Citation contract (shared with apps/web evidenceGroups.ts):

* a document is identified by evidence_id, source_id, url and retrieved_at;
* a chunk adds only its section, it never changes the document;
* the answer carries one public reference per document. For a document the
  saved result already cites, that reference is the saved leaf unchanged, so
  the browser's five-field comparison (plus section and observed_at) holds;
* documents are context found by search, never calculated metrics.
"""

import re
from collections.abc import Iterable, Mapping

from a2_contracts.v2.assistant import ExplanationRequest
from a2_contracts.v2.evidence import EvidenceResolver
from a2_contracts.v2.results import AnalysisResult, EvidenceRef
from a2_contracts.v2.retrieval import EvidenceHit, EvidenceSearchRequest, EvidenceSearchResult
from pydantic import JsonValue

from ..tools import AccessScope, ToolBackend, validate_tool

# Which questions get one bounded document search. Reproducible word rules, no
# extra model call. Each intent is covered by examples in test_retrieval_routing.
_INTENTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("sources", ("источник", "документ", "основан", "откуда", "на чём", "на чем", "source")),
    ("official", ("стратеги", "распоряжен", "постановлен", "программ", "закон", "нормативн")),
    ("methodology", ("методик", "формул", "как счита", "как рассчит", "норматив")),
    ("overview", ("обзор", "расскажи", "что известно", "в целом", "ситуаци", "район")),
    ("causes", ("почему", "причин", "из-за", "чем объясн", "влия")),
    ("context", ("контекст", "огранич", "учитыва", "полнот", "покрыт", "неизвест", "частич")),
)


def document_intent(context: ExplanationRequest | None) -> str | None:
    """The first matching intent, or None when the saved result alone answers.

    A plain numeric comparison ("сравни", "больше") matches no intent: the saved
    AnalysisResult answers it and the search would only add unrelated text.
    """
    question = (context.question or "").casefold() if context else ""
    for intent, words in _INTENTS:
        if any(word in question for word in words):
            return intent
    return None


def needs_documents(context: ExplanationRequest | None) -> bool:
    """One search only for a document-type question that names something to look for."""
    question = context.question if context else None
    return document_intent(context) is not None and bool(content_words(question or ""))


# Question and request words carry no document content; with any-word matching
# they only pull in unrelated fragments. Measured on 3363-r (scripts in the PR).
_QUERY_FILLER = frozenset(
    """
    а ли же бы ну вот это этот эта эти тот та те то там тут здесь сейчас
    что чем чего чему кто кого как какой какая какое какие каком какую каких каким
    какими почему зачем отчего где куда откуда когда сколько который которая которые
    ли разве можно нужно надо стоит будет будут есть был была были
    говорит говорят сказано указано написано известно расскажи расскажите объясни
    объясните покажи подскажи скажи опиши поясни планируется предусмотрено
    про о об обо по в во на для из от до у с со к ко при над под между и или но не
    мне нам вам тебе вы ты я мы этого этом этой этих
    """.split()
)


# Words about the saved result itself ("this index"), and the intent words of
# the question ("which sources", "what limits"): they name no document topic.
_RESULT_PREFIXES = (
    "индекс",
    "оценк",
    "значени",
    "показател",
    "результат",
    "расчёт",
    "расчет",
    "источник",
    "документ",
    "основан",
    "использ",
    "взят",
    "методик",
    "формул",
    "огранич",
    "контекст",
    "причин",
    "влия",
    "обзор",
    "сведени",
    "информаци",
    "данн",
)
_RESULT_WORDS = frozenset(
    """
    такой такая такое такие такого такую низкий низкая низкое низкие высокий высокая высокое
    высокие маленький большой хороший плохой слабый сильный
    """.split()
)


def content_words(question: str) -> list[str]:
    words = re.findall(r"[\wё-]+", question.casefold())
    return [
        word
        for word in words
        if len(word) > 1
        and word not in _QUERY_FILLER
        and word not in _RESULT_WORDS
        and not word.startswith(_RESULT_PREFIXES)
    ]


def search_query(question: str) -> str:
    """Content words of the question for the document index."""
    return " ".join(content_words(question)) or question


def document_ref(hit: Mapping[str, JsonValue]) -> EvidenceRef:
    return EvidenceRef.model_validate(
        {key: hit[key] for key in ("evidence_id", "source_id", "url", "retrieved_at", "section")}
        | {"observed_at": None}
    )


def _cut(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


class DocumentCitations:
    """Chunks admitted for one answer, grouped into one reference per document."""

    def __init__(self, saved: AnalysisResult) -> None:
        self._saved = {ref.evidence_id: ref for ref in saved.evidence_refs}
        self._documents: dict[str, EvidenceRef] = {}
        self._chunks: dict[str, dict[str, tuple[str, str]]] = {}

    def admit(self, hit: EvidenceHit | Mapping[str, JsonValue]) -> None:
        row = hit.model_dump(mode="json") if isinstance(hit, EvidenceHit) else hit
        ref = document_ref(row)
        # A group is a local alias of the saved result, never a retrieved document.
        EvidenceResolver((ref.evidence_id,))
        for prior in (self._saved.get(ref.evidence_id), self._documents.get(ref.evidence_id)):
            if prior is not None and (prior.source_id, prior.url, prior.retrieved_at) != (
                ref.source_id,
                ref.url,
                ref.retrieved_at,
            ):
                raise ValueError("Conflicting document provenance")
        if ref.evidence_id not in self._documents and len(self._documents) >= 50:
            raise ValueError("Too many retrieved evidence references")
        self._documents.setdefault(ref.evidence_id, ref)
        self._chunks.setdefault(ref.evidence_id, {})[str(row["chunk_id"])] = (
            str(row["title"]),
            ref.section,
        )

    @property
    def ids(self) -> frozenset[str]:
        return frozenset(self._documents)

    def sections(self, evidence_id: str) -> list[str]:
        """Selected sections in chunk order, whatever order the index returned them in."""
        chunks = self._chunks[evidence_id]
        return list(dict.fromkeys(chunks[chunk_id][1] for chunk_id in sorted(chunks)))

    def title(self, evidence_id: str) -> str:
        chunks = self._chunks[evidence_id]
        return chunks[min(chunks)][0]

    def references(self) -> list[EvidenceRef]:
        result = []
        for evidence_id in sorted(self._documents):
            known = self._saved.get(evidence_id)
            result.append(
                known
                if known is not None
                else self._documents[evidence_id].model_copy(
                    update={"section": _cut("; ".join(self.sections(evidence_id)), 300)}
                )
            )
        return result

    def sentence(self, evidence_ids: Iterable[str], limit: int) -> str:
        """Server-owned provenance wording: titles and sections, never document text."""
        parts = []
        for evidence_id in sorted(set(evidence_ids) & self.ids):
            sections = self.sections(evidence_id)
            parts.append(
                f"«{self.title(evidence_id)}», "
                + ("раздел: " if len(sections) == 1 else "разделы: ")
                + "; ".join(sections)
            )
        if not parts:
            return ""
        text = "Документальный контекст, не расчётный показатель: " + " | ".join(parts) + "."
        return _cut(text, limit) if limit >= 80 else ""


async def retrieve(
    backend: ToolBackend, saved: AnalysisResult, scope: AccessScope, context: ExplanationRequest
) -> EvidenceSearchResult:
    day = saved.request.departure_at.date()
    request = EvidenceSearchRequest(
        query=search_query(context.question or "")[:500],
        territory_ids=sorted(scope.territory_ids)[:20],
        source_ids=sorted(scope.source_ids)[:20],
        start_date=day,
        end_date=day,
    )
    name, args = validate_tool("search_evidence", request.model_dump_json(), scope)
    if saved.analysis_id not in scope.analysis_ids:
        raise ValueError("Unauthorized saved analysis")
    result = EvidenceSearchResult.model_validate(await backend.call(name, args, scope))
    citations = DocumentCitations(saved)
    for hit in result.hits:
        # The analysis snapshot or its own documents corpus, as scoped by the server.
        if (
            hit.snapshot_id not in {saved.snapshot_id, saved.snapshot_id + ":documents"}
            or hit.snapshot_id not in scope.snapshot_ids
            or hit.source_id not in (request.source_ids or [])
            or not set(hit.territory_ids) <= set(request.territory_ids)
            or not hit.valid_from <= day <= hit.valid_to
        ):
            raise ValueError("Document outside authorized scope or period")
        # Same provenance rule as the final citations: one identity per document.
        citations.admit(hit)
    return result
