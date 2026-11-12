import { useMemo, useState } from "react";
import type { Schemas } from "../api/types";
import { safeSource } from "../api/adapters/result";

const pageSize = 20;
export function EvidenceSources({ refs, resolveSources, label = "Источники и условия" }: { refs: readonly Schemas["EvidenceRef"][]; resolveSources?: () => readonly Schemas["EvidenceRef"][]; label?: string }) {
  const [expanded, setExpanded] = useState(false), [page, setPage] = useState(0);
  const resolved = useMemo(() => {
    try { return { refs: expanded && resolveSources ? resolveSources() : refs, invalid: false }; }
    catch { return { refs: [], invalid: true }; }
  }, [expanded, refs, resolveSources]);
  const sources = resolved.refs;
  const pages = Math.max(1, Math.ceil(sources.length / pageSize));
  const current = Math.min(page, pages - 1);
  return <details className="src" onToggle={(event) => setExpanded(event.currentTarget.open)}>
    <summary>{label}</summary>
    {expanded && <>
      {resolved.invalid ? <p>Источники для выбранного контекста недоступны.</p> : sources.length ? sources.slice(current * pageSize, (current + 1) * pageSize).map((e, index) => {
        const url = safeSource(e.url);
        return <p key={`${e.evidence_id}-${current * pageSize + index}`}>
          {url ? <a href={url} target="_blank" rel="noopener noreferrer">{new URL(url).hostname} · {e.section}</a> : e.section || "Источник без публичной ссылки"}<br />
          <small>{e.observed_at ? `Данные на ${new Date(e.observed_at).toLocaleDateString("ru", { timeZone: "UTC" })}` : "Дата наблюдения неизвестна"}{" · получено "}
            {new Date(e.retrieved_at).toLocaleDateString("ru", { timeZone: "UTC" })}</small>
        </p>;
      }) : <p>Ссылки отсутствуют в ответе.</p>}
      {pages > 1 && <nav aria-label="Страницы источников"><p aria-live="polite">Источники {current * pageSize + 1}–{Math.min((current + 1) * pageSize, sources.length)} из {sources.length}</p>
        <button className="iconbtn" disabled={current === 0} onClick={() => setPage(current - 1)}>Назад</button>{" "}
        <button className="iconbtn" disabled={current === pages - 1} onClick={() => setPage(current + 1)}>Далее</button>
      </nav>}
    </>}
  </details>;
}
