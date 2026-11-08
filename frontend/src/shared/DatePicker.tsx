import { useId, useState, type KeyboardEvent } from "react";
import { Icon } from "./ui";
import { usePopover } from "./usePopover";
const iso = (date: Date) => date.toISOString().slice(0, 10);
export function DatePicker({
  id,
  value,
  min,
  max,
  onChange,
}: {
  id: string;
  value: string;
  min: string;
  max: string;
  onChange: (value: string) => void;
}) {
  const { open, setOpen, trigger, popup } = usePopover(),
    dialogId = useId();
  const [month, setMonth] = useState(value.slice(0, 7));
  const first = new Date(`${month}-01T12:00:00Z`),
    year = first.getUTCFullYear(),
    m = first.getUTCMonth();
  const offset = (first.getUTCDay() + 6) % 7,
    days = new Date(Date.UTC(year, m + 1, 0)).getUTCDate();
  const shift = (delta: number) =>
    iso(new Date(Date.UTC(year, m + delta, 1))).slice(0, 7);
  const choose = (date: string) => {
    onChange(date);
    setOpen(false);
    trigger.current?.focus();
  };
  const key = (e: KeyboardEvent<HTMLButtonElement>, date: string) => {
    let delta = 0;
    if (e.key === "ArrowRight") delta = 1;
    if (e.key === "ArrowLeft") delta = -1;
    if (e.key === "ArrowDown") delta = 7;
    if (e.key === "ArrowUp") delta = -7;
    if (!delta) return;
    e.preventDefault();
    const next = new Date(`${date}T12:00:00Z`);
    next.setUTCDate(next.getUTCDate() + delta);
    const v = iso(next);
    if (v < min || v > max) return;
    setMonth(v.slice(0, 7));
    requestAnimationFrame(() =>
      popup.current?.querySelector<HTMLElement>(`[data-date="${v}"]`)?.focus(),
    );
  };
  return (
    <>
      <button
        id={id}
        ref={trigger}
        className="wx-date-trigger tnum"
        type="button"
        aria-label="Выбрать дату"
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-controls={dialogId}
        onClick={() => {
          setMonth(value.slice(0, 7));
          setOpen(!open);
        }}
      >
        {value.split("-").reverse().join(".")}
        <Icon name="calendar-check" />
      </button>
      <div
        id={dialogId}
        ref={popup}
        popover="manual"
        className="wx-popup wx-calendar"
        role="dialog"
        aria-label="Выбор даты"
      >
        <div className="wx-calendar-head">
          <button
            type="button"
            aria-label="Предыдущий месяц"
            disabled={shift(-1) < min.slice(0, 7)}
            onClick={() => setMonth(shift(-1))}
          >
            <Icon name="arrow-left" />
          </button>
          <b>
            {new Intl.DateTimeFormat("ru-RU", {
              month: "long",
              year: "numeric",
              timeZone: "UTC",
            }).format(first)}
          </b>
          <button
            type="button"
            aria-label="Следующий месяц"
            disabled={shift(1) > max.slice(0, 7)}
            onClick={() => setMonth(shift(1))}
          >
            <Icon name="arrow-right" />
          </button>
        </div>
        <div className="wx-calendar-week" aria-hidden="true">
          {["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"].map((d) => (
            <span key={d}>{d}</span>
          ))}
        </div>
        <div className="wx-calendar-days">
          {Array.from({ length: offset }, (_, i) => (
            <span key={`empty-${i}`} />
          ))}
          {Array.from({ length: days }, (_, i) => {
            const date = `${month}-${String(i + 1).padStart(2, "0")}`;
            return (
              <button
                key={date}
                type="button"
                data-date={date}
                aria-label={new Intl.DateTimeFormat("ru-RU", {
                  dateStyle: "full",
                  timeZone: "UTC",
                }).format(new Date(`${date}T12:00:00Z`))}
                aria-pressed={date === value}
                disabled={date < min || date > max}
                onKeyDown={(e) => key(e, date)}
                onClick={() => choose(date)}
              >
                {i + 1}
              </button>
            );
          })}
        </div>
        <p>
          Доступно: {min.split("-").reverse().join(".")} —{" "}
          {max.split("-").reverse().join(".")}
        </p>
      </div>
    </>
  );
}
