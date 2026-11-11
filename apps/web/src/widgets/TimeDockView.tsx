import { DatePicker } from "../shared/DatePicker";
import { Icon } from "../shared/ui";
import { css, timeLabel } from "../shared/format";
export function TimeDockView({ run, mobile = false, date, slot, timezoneLabel, min, max, onDateChange, onSlotChange, disabled = false, highlighted = false, runLabel = "Рассчитать", dateInputId, timeInputId }: {
  run: () => void; mobile?: boolean; date: string; slot: number; timezoneLabel: string; min: string; max: string;
  onDateChange: (date: string) => void; onSlotChange: (slot: number) => void; disabled?: boolean; highlighted?: boolean;
  runLabel?: string; dateInputId?: string; timeInputId?: string;
}) {
  const validSlot = Number.isInteger(slot) && slot >= 0 && slot <= 95;
  const shownSlot = validSlot ? slot : 0;
  const shownTime = validSlot ? timeLabel(slot) : "Выберите время";
  const fill = (shownSlot / 95) * 100;
  const set = (t: number) =>
    onSlotChange(Math.max(0, Math.min(95, t)));
  const id = mobile ? "M" : "";
  return (
    <div
      className={mobile ? "timedock" : "timedock panelc"}
      id={mobile ? "mTime" : "timedock"}
    >
      <div className="td-date">
        <label htmlFor={dateInputId || `dateIn${id}`}>Дата</label>
        <DatePicker
          id={dateInputId || `dateIn${id}`}
          value={date}
          min={min}
          max={max}
          onChange={onDateChange}
        />

        <span className="tz">
          <Icon name="globe" /> {timezoneLabel}
        </span>
      </div>
      <div className="td-slider">
        <span
          className="td-now tnum"
          style={{ left: `calc(${fill}% + ${11 - (shownSlot / 95) * 22}px)` }}
        >
          {shownTime}
        </span>
        <span
          className="td-night"
          style={{ left: 0, width: `${(23 / 95) * 100}%` }}
        />
        <span
          className="td-night"
          style={{ left: `${(80 / 95) * 100}%`, right: 0 }}
        />
        <input
          type="range"
          min="0"
          max="95"
          step="1"
          value={shownSlot}
          id={timeInputId || `timeIn${id}`}
          aria-label="Время отправления, шаг 15 минут"
          aria-valuetext={`${shownTime}, ${timezoneLabel}`}
          style={css({ "--fill": `${fill}%` })}
          onChange={(e) => set(+e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "PageUp" || e.key === "PageDown") {
              e.preventDefault();
              set(slot + (e.key === "PageUp" ? 4 : -4));
            }
          }}
        />
        <div className="td-ticks tnum" aria-hidden="true">
          {[0, 24, 48, 72].map((t) => (
            <span key={t} style={{ left: `${(t / 95) * 100}%` }}>
              {String(t / 4).padStart(2, "0")}
            </span>
          ))}
          <span className="next" style={{ left: "100%" }}>
            00<sup>+1</sup>
          </span>
        </div>
      </div>
      <button
        className={`iconbtn ${highlighted ? "primary" : ""} td-run`}
        onClick={run}
        disabled={disabled}
      >
        <Icon name="play" />
        <span>
          {runLabel}
        </span>
      </button>
    </div>
  );
}
