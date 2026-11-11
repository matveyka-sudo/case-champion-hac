import { useEffect, useRef, useState } from "react";
import { pause } from "../api/client";
import { backend } from "../api/gateway";
import type { Schemas } from "../api/types";
import { categoryLabels } from "../api/adapters/request";
import { errorText, reasonText, statusText } from "../api/adapters/result";
import { savedPointerHash } from "../features/analysis/savedPointer";

type Group = Schemas["AnalysisGroupView"];
const active = (value: Group) => value.status === "queued" || value.status === "running";
const groupStatusText: Record<Group["items"][number]["status"], string> = { queued: statusText.queued, running: statusText.running, pending: "Ожидает расчёта", succeeded: "Расчёт сохранён", failed: "Расчёт завершён с ошибкой", cancelled: "Расчёт отменён" };

export function AnalysisGroupPanel({ id }: { id: string }) {
  const [group, setGroup] = useState<Group | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [revision, setRevision] = useState(0);
  const retryKey = useRef<string | null>(null);
  const poll = useRef<AbortController | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    poll.current = controller;
    const load = async () => {
      let value: Group;
      do {
        value = await backend.group(id, controller.signal);
        if (value.group_id !== id) throw new Error("Сервер вернул другую группу.");
        if (controller.signal.aborted) return;
        setGroup(value);
        if (active(value)) await pause((value.poll_after_seconds ?? 2) * 1000, controller.signal);
      } while (active(value));
    };
    void load().catch((failure: unknown) => {
      if (!controller.signal.aborted) setError(failure instanceof Error ? errorText(failure) : "Не удалось загрузить группу.");
    });
    return () => controller.abort();
  }, [id, revision]);

  const update = async (action: "cancel" | "retry") => {
    setBusy(true);
    setError("");
    poll.current?.abort();
    try {
      const key = action === "retry" ? retryKey.current ??= crypto.randomUUID() : undefined;
      const value = await backend.updateGroup(id, action, key);
      if (value.group_id !== id) throw new Error("Сервер вернул другую группу.");
      retryKey.current = null;
      setGroup(value);
      setRevision((count) => count + 1);
    } catch (failure) {
      setError(failure instanceof Error ? errorText(failure) : "Не удалось обновить группу.");
    } finally {
      setBusy(false);
    }
  };

  return <main id="atlas" className="wrap api-workflow">
    <a className="iconbtn" href="#atlas">Вернуться к фильтрам</a>
    <h1>Расчёт по категориям</h1>
    <p>Каждая категория сохраняется отдельно. Откройте готовый результат, чтобы увидеть карту.</p>
    {(error || group?.error) && <p role="alert">{error || (group?.error && errorText(group.error))}</p>}
    <p role="status">{group ? groupStatusText[group.status] : error ? "" : "Загружаем состояние группы…"}</p>
    {group && <ul className="api-workflow-items">
      {group.items.map((item) => <li key={item.category}>
        <strong>{categoryLabels[item.category]}</strong>
        <span>{groupStatusText[item.status]}</span>
        {item.error && <p>{reasonText(item.error.message) || item.error.message}</p>}
        {item.analysis_id && <a className="iconbtn" href={savedPointerHash({ kind: "analysis", id: item.analysis_id })}>Открыть результат</a>}
      </li>)}
    </ul>}
    <div className="api-preset-options">
      {group && active(group) && <button className="iconbtn" disabled={busy} onClick={() => void update("cancel")}>Отменить группу</button>}
      {group && !active(group) && group.items.some((item) => item.status === "failed" || item.status === "cancelled") &&
        <button className="iconbtn" disabled={busy} onClick={() => void update("retry")}>Повторить незавершённые категории</button>}
      {error && <button className="iconbtn" disabled={busy} onClick={() => { setError(""); setRevision((count) => count + 1); }}>Обновить состояние</button>}
    </div>
  </main>;
}
