import { Icon } from "../shared/ui";
import { Select } from "../shared/Select";
import { Seg } from "./SegmentedControl";

export function ChoiceChips({ items, values, onChange }: { items: string[][]; values: Record<string, boolean>; onChange: (value: string) => void }) {
  return <div className="chipset">{items.map(([value, label, icon]) => <button key={value} type="button" className="fchip" aria-pressed={!!values[value]} onClick={() => onChange(value)}>
    {icon && <Icon name={icon} />}{label}</button>)}</div>;
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
export function CargoProfileChoices({ value, onChange, demo = false }: { value: CargoProfileChoice; onChange: (profile: CargoProfileChoice) => void; demo?: boolean }) {
  return <div className="fgroup"><p className="flabel">Профиль груза{demo && <small>демо</small>}</p>
    <p className="fhint" style={{ margin: "0 0 6px" }}>Товарная группа</p>
    <Seg name="Товарная группа" items={[["food", "Продовольствие"], ["nonfood", "Непрод."], ["raw", "Сырьё"]]} value={value.group} onChange={(group) => onChange({ ...value, group })} />
    <p className="fhint" style={{ margin: "12px 0 6px" }}>Обработка</p>
    <Seg stack name="Обработка" items={[["container", "Контейнер"], ["general", "Генеральный"], ["bulk", "Навалочный"], ["liquid", "Наливной"]]}
      value={value.handling} onChange={(handling) => onChange({ ...value, handling })} />
    <p className="fhint" style={{ margin: "12px 0 6px" }}>Ограничения</p>
    <ChoiceChips items={[["temp", "Температурный режим"], ["perish", "Скоропортящийся"], ["urgent", "Срочный"]]} values={value.limits}
      onChange={(key) => onChange({ ...value, limits: { ...value.limits, [key]: !value.limits[key] } })} />
  </div>;
}
