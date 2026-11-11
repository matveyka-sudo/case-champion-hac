import { Icon } from "../shared/ui";
export function Seg({ name, items, value, onChange, stack = false }: {
  name: string; items: readonly (readonly [string, string, string?, boolean?, string?])[];
  value: string | null; onChange: (value: string) => void; stack?: boolean;
}) {
  return <div className={`segc ${stack ? "stack" : ""}`} role="group" aria-label={name}>
    {items.map(([id, label, icon, disabled, reason]) => <button key={id} type="button" disabled={disabled} aria-pressed={id === value}
      aria-description={reason} title={reason} onClick={() => onChange(id)}>{icon && <Icon name={icon} />}<span>{label}</span></button>)}
  </div>;
}
