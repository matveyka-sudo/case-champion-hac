import { useId, useRef, type KeyboardEvent } from "react";
import { Icon } from "./ui";
import { usePopover } from "./usePopover";
export interface SelectOption {
  value: string;
  label: string;
  disabled?: boolean;
}
export function Select({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: string;
  options: SelectOption[];
  onChange: (value: string) => void;
}) {
  const { open, setOpen, trigger, popup } = usePopover(),
    id = useId();
  const active = useRef(value),
    search = useRef({ text: "", time: 0 });
  const focusOption = (value: string) => {
    active.current = value;
    popup.current
      ?.querySelector<HTMLElement>(`[data-value="${CSS.escape(value)}"]`)
      ?.focus();
  };
  const select = (next: string) => {
    onChange(next);
    setOpen(false);
    trigger.current?.focus();
  };
  const keyboard = (e: KeyboardEvent) => {
    const enabled = options.filter((o) => !o.disabled);
    if (!enabled.length) return;
    let i = enabled.findIndex((o) => o.value === active.current);
    if (["ArrowDown", "ArrowUp", "Home", "End"].includes(e.key)) {
      e.preventDefault();
      if (!open) {
        active.current = value;
        setOpen(true);
        requestAnimationFrame(() => focusOption(value));
        return;
      }
      i =
        e.key === "Home"
          ? 0
          : e.key === "End"
            ? enabled.length - 1
            : (i + (e.key === "ArrowDown" ? 1 : -1) + enabled.length) %
              enabled.length;
      focusOption(enabled[i].value);
    } else if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      if (open) select(active.current);
      else {
        setOpen(true);
        requestAnimationFrame(() => focusOption(value));
      }
    } else if (e.key === "Tab") setOpen(false);
    else if (e.key.length === 1) {
      const time = Date.now();
      search.current = {
        text:
          (time - search.current.time < 700 ? search.current.text : "") +
          e.key.toLocaleLowerCase("ru"),
        time,
      };
      const found = enabled.find((o) =>
        o.label.toLocaleLowerCase("ru").startsWith(search.current.text),
      );
      if (found) {
        e.preventDefault();
        if (!open) setOpen(true);
        requestAnimationFrame(() => focusOption(found.value));
      }
    }
  };
  return (
    <div className="wx-select">
      <button
        ref={trigger}
        type="button"
        className="wx-select-trigger"
        role="combobox"
        aria-label={label}
        aria-controls={id}
        aria-expanded={open}
        aria-haspopup="listbox"
        onClick={() => {
          active.current = value;
          setOpen(!open);
        }}
        onKeyDown={keyboard}
      >
        <span>
          {options.find((o) => o.value === value)?.label || "Выберите"}
        </span>
        <Icon name="chevron-down" />
      </button>
      <div
        ref={popup}
        id={id}
        popover="manual"
        className="wx-popup wx-select-popup"
        role="listbox"
        aria-label={label}
        onKeyDown={keyboard}
      >
        {options.map((o) => (
          <button
            key={o.value}
            type="button"
            role="option"
            tabIndex={-1}
            data-value={o.value}
            aria-selected={o.value === value}
            disabled={o.disabled}
            onFocus={() => {
              active.current = o.value;
            }}
            onClick={() => select(o.value)}
          >
            <span>{o.label}</span>
            {o.value === value && <Icon name="check" />}
          </button>
        ))}
      </div>
    </div>
  );
}
