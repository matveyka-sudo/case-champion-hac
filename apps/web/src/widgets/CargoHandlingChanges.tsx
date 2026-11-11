import type { Schemas } from "../api/types";
import { timestampText } from "../api/adapters/result";

const duration = (seconds: number) => `${(seconds / 60).toLocaleString("ru", { maximumFractionDigits: 2 })} мин`;

export function CargoHandlingChanges({ changes }: { changes: Schemas["CargoHandlingChange"][] }) {
  return <div aria-label="Изменения обработки груза">
    {changes.map((change, index) => <div className="api-comparison-row" key={`${change.action_id}:${change.resource_id}`}>
      <b>Обработка груза · операция {index + 1}</b>
      <div className="api-comparison-values">
        <span><small>Было</small><strong>{duration(change.before_seconds)}</strong></span>
        <span><small>Стало</small><strong>{duration(change.after_seconds)}</strong></span>
      </div>
      <p className="fhint">Ресурс обработки: {change.resource_units_before.toLocaleString("ru")} → {change.resource_units_after.toLocaleString("ru")} ед.</p>
      <p className="fhint">Период действия (UTC): {timestampText(change.valid_from, "UTC")} — {timestampText(change.valid_to, "UTC")}</p>
    </div>)}
    {!changes.length && <p>Изменения обработки не предоставлены.</p>}
  </div>;
}
