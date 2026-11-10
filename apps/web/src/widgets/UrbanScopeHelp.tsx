import { useState } from "react";
import type { Schemas, Territory } from "../api/types";
import { categoryIds, categoryLabels, toRequest, type ApiDraft } from "../api/adapters/request";
import { modeText, timestampText, trafficText } from "../api/adapters/result";
import { scopePreset, type UrbanSelection, type UrbanScopeAvailability } from "../api/adapters/urbanScopes";
import { Select } from "../shared/Select";

export function UrbanScopeHelp({ state, selection, draft, caps, catalog, onApply }: {
  state: UrbanScopeAvailability;
  selection: UrbanSelection;
  draft: ApiDraft;
  caps: Schemas["Capabilities"];
  catalog: Territory[];
  onApply: (patch: Partial<ApiDraft>) => void;
}) {
  const [chosenId, setChosenId] = useState("");
  const scopes = caps.urban_passenger_scopes || [];
  if (!state.published) return null;
  const effectiveId = scopes.some((s) => s.scope_id === chosenId) ? chosenId
    : scopes.length === 1 ? scopes[0].scope_id : "";
  const chosen = scopes.find((s) => s.scope_id === effectiveId);
  const preset = chosen ? scopePreset(chosen, selection, scopes, catalog, caps.areas || []) : null;
  const patch: Partial<ApiDraft> | null = preset ? {
    cityId: preset.selection.cityId, snapshotId: preset.selection.snapshotId, areaId: preset.selection.areaId,
    date: preset.date, slot: preset.slot, traffic: preset.selection.traffic,
    travel: ({ walk: "walk", public_transport: "pt", private_car: "car" } as const)[preset.selection.mode],
    cats: Object.fromEntries(Object.entries(categoryIds)
      .filter(([, category]) => preset.selection.categories.includes(category))
      .map(([key]) => [key, draft.cats[key as keyof typeof categoryIds] || 2])),
  } : null;
  let presetError = "";
  if (patch) {
    try { toRequest({ ...draft, ...patch }, catalog, caps); }
    catch (error) { presetError = error instanceof Error ? error.message : "Этот набор параметров недоступен."; }
  }
  const cityName = (id: string) => catalog.find((t) => t.territory_id === id)?.name || "Город из справочника";
  return (
    <div className="api-scope-help" aria-label="Доступность городского расчёта">
      <p className="fhint" aria-live="polite">
        {state.problem?.message || "Для выбранных параметров зарегистрирован запуск расчёта."}
      </p>
      <p className="fhint">Доступный запуск не гарантирует полноту данных или наличие индекса.</p>
      {state.problem && scopes.length > 0 && (
        <>
          <Select
            label="Доступный набор параметров"
            value={effectiveId}
            options={[
              { value: "", label: "Выберите доступный набор" },
              ...scopes.map((scope) => ({ value: scope.scope_id, label: [
                cityName(scope.city_id), scope.passenger_modes.map((m) => modeText[m]).join(", "),
                scope.categories.map((c) => categoryLabels[c]).join(", "),
                scope.traffic_modes.map((t) => trafficText[t]).join(", "),
                `${timestampText(scope.valid_from, catalog.find((t) => t.territory_id === scope.city_id)?.timezone)} — ${timestampText(scope.valid_to, catalog.find((t) => t.territory_id === scope.city_id)?.timezone)}`,
                `набор данных · версия ${scope.snapshot_id.slice(-8)}`,
              ].join(" · ") })),
            ]}
            onChange={setChosenId}
          />
          {chosen && (
            <p className="fhint api-scope-preview">
              Период: с {timestampText(chosen.valid_from, catalog.find((t) => t.territory_id === chosen.city_id)?.timezone)} до{" "}
              {timestampText(chosen.valid_to, catalog.find((t) => t.territory_id === chosen.city_id)?.timezone)} (конец не включён).
            </p>
          )}
          {preset && (
            <p className="fhint api-scope-preview">
              Будет выбрано: {cityName(preset.selection.cityId)} · {preset.date} ·{" "}
              {String(Math.floor(preset.slot / 4)).padStart(2, "0")}:{String((preset.slot % 4) * 15).padStart(2, "0")} ·{" "}
              {modeText[preset.selection.mode]} · {trafficText[preset.selection.traffic]} ·{" "}
              {preset.selection.categories.map((c) => categoryLabels[c]).join(", ")} ·{" "}
              {preset.selection.areaId ? caps.areas?.find((a) => a.area_id === preset.selection.areaId)?.name || "Район из справочника" : "Весь город — область запроса"}.
            </p>
          )}
          {chosen && (!preset || presetError) && <p className="fhint">{presetError || "Не удалось подобрать однозначный доступный набор параметров. Измените условия или выберите другой набор."}</p>}
          <button className="iconbtn" disabled={!patch || !!presetError} onClick={() => patch && onApply(patch)}>
            Использовать доступные параметры
          </button>
        </>
      )}
    </div>
  );
}
