import { useState } from "react";
import type { Schemas } from "../api/types";
import { safeSource } from "../api/adapters/result";

const pageSize = 20;
export function EvidenceSources({ refs }: { refs: Schemas["EvidenceRef"][] }) {
  const [expanded, setExpanded] = useState(false), [page, setPage] = useState(0);
  const pages = Math.max(1, Math.ceil(refs.length / pageSize));
  const current = Math.min(page, pages - 1);
  return <details className="src" onToggle={(event) => setExpanded(event.currentTarget.open)}>
    <summary>Источники и условия</summary>
    {expanded && <>
      {refs.length ? refs.slice(current * pageSize, (current + 1) * pageSize).map((e, index) => {
        const url = safeSource(e.url);
        return <p key={`${e.evidence_id}-${current * pageSize + index}`}>
          {url ? <a href={url} target="_blank" rel="noopener noreferrer">{new URL(url).hostname} · {e.section}</a> : e.section || "Источник без публичной ссылки"}<br />
          <small>{e.observed_at ? `Данные на ${new Date(e.observed_at).toLocaleDateString("ru", { timeZone: "UTC" })}` : "Дата наблюдения неизвестна"}{" · получено "}
            {new Date(e.retrieved_at).toLocaleDateString("ru", { timeZone: "UTC" })}</small>
        </p>;
      }) : <p>Ссылки отсутствуют в ответе.</p>}
      {pages > 1 && <nav aria-label="Страницы источников"><p aria-live="polite">Источники {current * pageSize + 1}–{Math.min((current + 1) * pageSize, refs.length)} из {refs.length}</p>
        <button className="iconbtn" disabled={current === 0} onClick={() => setPage(current - 1)}>Назад</button>{" "}
        <button className="iconbtn" disabled={current === pages - 1} onClick={() => setPage(current + 1)}>Далее</button>
      </nav>}
    </>}
  </details>;
}
