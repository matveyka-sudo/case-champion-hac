import { Icon } from "../shared/ui";
import { Select } from "../shared/Select";
import { Seg } from "./SegmentedControl";
import { cargoHandling, type compactCargoOptions } from "../api/adapters/cargoBinding";

export function ChoiceChips({ items, values, onChange, disabled = [], descriptionId }: {
  items: string[][];
  values: Record<string, boolean>;
  onChange: (value: string) => void;
  disabled?: string[];
  descriptionId?: string;
}) {
  return <div className="chipset">{items.map(([value, label, icon]) => {
    const unavailable = disabled.includes(value);
    return <button key={value} type="button" className="fchip" disabled={unavailable}
      aria-pressed={!unavailable && !!values[value]} aria-describedby={unavailable ? descriptionId : undefined}
      onClick={() => onChange(value)}>{icon && <Icon name={icon} />}{label}</button>;
  })}</div>;
}
export function TerritoryPair({ origin, destination, options, onChange, onSwap }: {
  origin: string; destination: string; options: { value: string; label: string }[];
  onChange: (side: "A" | "B", value: string) => void; onSwap: () => void;
}) {
  return <div className="pair">{(["A", "B"] as const).map((side, index) => <div className="fsel" key={side}><span className="k">{index ? "Б" : "А"}</span>
    <Select label={`Территория ${index ? "Б" : "А"}`} value={index ? destination : origin} options={options} onChange={(value) => onChange(side, value)} /></div>)}
    <button className="swap" aria-label="Поменять направление" onClick={onSwap}><Icon name="arrow-up-down" /></button></div>;
}
export interface CargoProfileChoice { group: string; handling: string; limits: Record<string, boolean> }
export function CargoProfileChoices({ value, onChange, demo = false, showNonurgent = false, available = null }: { value: CargoProfileChoice; onChange: (profile: CargoProfileChoice, field: "group" | "handling" | "limits") => void; demo?: boolean; showNonurgent?: boolean; available?: ReturnType<typeof compactCargoOptions> }) {
  return <div className="fgroup"><p className="flabel">Профиль груза{demo && <small>демо</small>}</p>
    <p className="fhint" style={{ margin: "0 0 6px" }}>Товарная группа</p>
    <Seg name="Товарная группа" items={[["food", "Продовольствие"], ["nonfood", "Непрод."], ["raw", "Сырьё"]]} value={value.group} onChange={(group) => onChange({ ...value, group }, "group")} />
    <p className="fhint" style={{ margin: "12px 0 6px" }}>Обработка</p>
    <Seg stack name="Обработка" items={cargoHandling.filter(([handling]) => handling === "container" || handling === "general").map(([handling, label]) => [handling, label, undefined, !!available && !available.some((option) => option.handling === handling)])}
      value={value.handling} onChange={(handling) => onChange({ ...value, handling }, "handling")} />
    {available && <div aria-live="polite">
      {available.length ? <p className="fhint">Доступная обработка и местное время: {available.map(({ label, times }) =>
        `${label} — ${times.map(({ time, onSelectedDate }) => onSelectedDate ? time : `${time} (в другие даты)`).join(", ")}`).join(" · ")}.</p>
        : <p className="fhint">Для этой товарной группы с выбранными условиями нет доступной обработки.</p>}
    </div>}
    <p className="fhint" style={{ margin: "12px 0 6px" }}>Срочность</p>
    <Seg name="Срочность" items={[["urgent", "Срочный"], ["ordinary", "Обычный"], ...(showNonurgent ? [["nonurgent", "Несрочный"] as const] : [])]}
      value={value.limits.urgent ? "urgent" : value.limits.nonurgent ? "nonurgent" : "ordinary"}
      onChange={(urgency) => onChange({ ...value, limits: { ...value.limits, urgent: urgency === "urgent", nonurgent: urgency === "nonurgent" } }, "limits")} />
  </div>;
}
