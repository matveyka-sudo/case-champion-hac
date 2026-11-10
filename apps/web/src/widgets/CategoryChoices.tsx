import { Icon } from "../shared/ui";
export interface CategoryChoice { id: string; name: string; disabled?: boolean; reason?: string }
export function CategoryChoices({ items, selected, onToggle, onPriority }: {
  items: CategoryChoice[]; selected: Partial<Record<string, number>>;
  onToggle: (id: string) => void; onPriority: (id: string, priority: number) => void;
}) {
  return <div role="group" aria-label="Категории объектов и приоритеты">{items.map((item) =>
    <div className={`cat ${selected[item.id] ? "on" : ""}`} key={item.id}>
      <button className="cat-tog" aria-label={item.name} aria-pressed={!!selected[item.id]} disabled={item.disabled}
        aria-description={item.reason} title={item.reason} onClick={() => onToggle(item.id)}>
        <span className="box"><Icon name="check" /></span><span>{item.name}</span>
      </button>
      <div className="prio" role="group" aria-label={`Приоритет: ${item.name}`}>{[1, 2, 3].map((priority, index) =>
        <button key={priority} disabled={item.disabled || !selected[item.id]} aria-pressed={selected[item.id] === priority} aria-label={`${["Низкий", "Средний", "Высокий"][index]} приоритет`}
          onClick={() => onPriority(item.id, priority)}>{["Н", "С", "В"][index]}</button>)}</div>
    </div>)}</div>;
}
