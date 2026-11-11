import { useId, useLayoutEffect, useRef, useState, type KeyboardEvent } from "react";
import { Icon } from "./ui";
import { usePopover } from "./usePopover";
export interface SelectOption {
  value: string;
  label: string;
  disabled?: boolean;
  reason?: string;
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
  const searchable = options.length > 20;
  const [query, setQuery] = useState("");
  const searchInput = useRef<HTMLInputElement>(null);
  const visibleOptions = searchable && query.trim()
    ? options.filter((option) => option.label.toLocaleLowerCase("ru").includes(query.trim().toLocaleLowerCase("ru"))) : options;
  const focusOption = (value: string) => {
    active.current = value;
    popup.current
      ?.querySelector<HTMLElement>(`[data-value="${CSS.escape(value)}"]`)
      ?.focus();
  };
  useLayoutEffect(() => {
    if (open && searchable) searchInput.current?.focus();
    else if (open) popup.current?.querySelector<HTMLElement>(`[data-value="${CSS.escape(active.current)}"]:not(:disabled)`)?.focus();
  }, [open, popup, searchable]);
  const select = (next: string) => {
    if (!options.some((option) => option.value === next && !option.disabled)) return;
    onChange(next);
    setOpen(false);
    trigger.current?.focus();
  };
  const keyboard = (e: KeyboardEvent) => {
    if (e.target instanceof HTMLInputElement) return;
    const enabled = visibleOptions.filter((o) => !o.disabled);
    if (!enabled.length) return;
    let i = enabled.findIndex((o) => o.value === active.current);
    if (["ArrowDown", "ArrowUp", "Home", "End"].includes(e.key)) {
      e.preventDefault();
      if (!open) {
        active.current = enabled.find((option) => option.value === value)?.value ?? enabled[0].value;
        setOpen(true);
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
        active.current = enabled.find((option) => option.value === value)?.value ?? enabled[0].value;
        setOpen(true);
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
        if (!open) {
          active.current = found.value;
          setOpen(true);
        } else focusOption(found.value);
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
          setQuery("");
          active.current = options.find((option) => option.value === value && !option.disabled)?.value ?? options.find((option) => !option.disabled)?.value ?? "";
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
        id={searchable ? undefined : id}
        popover="manual"
        className="wx-popup wx-select-popup"
        role={searchable ? undefined : "listbox"}
        aria-label={label}
        onKeyDown={keyboard}
      >
        {searchable && <div className="wx-select-search"><input ref={searchInput} className="api-input" type="search"
          aria-label={`${label}: поиск`} placeholder="Найти город или субъект" value={query}
          onChange={(event) => setQuery(event.target.value)} onKeyDown={(event) => {
            if (event.key === "ArrowDown") {
              event.preventDefault();
              const first = visibleOptions.find((option) => !option.disabled);
              if (first) focusOption(first.value);
            }
          }} /></div>}
        <div role={searchable ? "listbox" : "presentation"} id={searchable ? id : undefined} aria-label={searchable ? label : undefined}>
        {visibleOptions.map((o) => (
          <button
            key={o.value}
            type="button"
            role="option"
            tabIndex={-1}
            data-value={o.value}
            aria-selected={o.value === value}
            disabled={o.disabled}
            aria-label={o.label}
            aria-description={o.reason}
            title={o.reason}
            className={o.reason ? "wx-option-unavailable" : undefined}
            onFocus={() => {
              active.current = o.value;
            }}
            onClick={() => select(o.value)}
          >
            <span>{o.label}{o.reason && <small className="wx-option-reason">{o.reason}</small>}</span>
            {o.value === value && <Icon name="check" />}
          </button>
        ))}
        </div>
        {searchable && !visibleOptions.length && <p role="status">Территория не найдена</p>}
      </div>
    </div>
  );
}
