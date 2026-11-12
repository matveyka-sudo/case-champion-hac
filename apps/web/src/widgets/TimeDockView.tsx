import { compactSlots } from "../api/adapters/compactScopes";
import { DatePicker } from "../shared/DatePicker";
import { Icon } from "../shared/ui";
import { css, timeLabel } from "../shared/format";
export function TimeDockView({ run, mobile = false, date, slot, timezoneLabel, min, max, onDateChange, onSlotChange, disabled = false, highlighted = false, runLabel = "Рассчитать", dateInputId, timeInputId, compact = false, dates }: {
  run: () => void; mobile?: boolean; date: string; slot: number; timezoneLabel: string; min: string; max: string;
  onDateChange: (date: string) => void; onSlotChange: (slot: number) => void; disabled?: boolean; highlighted?: boolean;
  runLabel?: string; dateInputId?: string; timeInputId?: string; compact?: boolean; dates?: string[];
}) {
  const validSlot = Number.isInteger(slot) && slot >= 0 && slot <= 95;
  const maximum = compact ? compactSlots.length - 1 : 95;
  const index = compact ? compactSlots.findIndex((item) => item.slot === slot) : validSlot ? slot : -1;
  const shownSlot = Math.max(0, index);
  const shownTime = index >= 0 ? timeLabel(slot) : "Выберите время";
  const fill = (shownSlot / maximum) * 100;
  const set = (value: number) => {
    const index = Math.max(0, Math.min(maximum, value));
    onSlotChange(compact ? compactSlots[index].slot : index);
  };
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
          dates={dates}
        />

        {!compact && <span className="tz">
          <Icon name="globe" /> {timezoneLabel}
        </span>}
      </div>
      <div className="td-slider">
        <span
          className="td-now tnum"
          style={{ left: `calc(${fill}% + ${11 - (shownSlot / maximum) * 22}px)` }}
        >
          {shownTime}
        </span>
        {!compact && <><span
          className="td-night"
          style={{ left: 0, width: `${(23 / 95) * 100}%` }}
        />
        <span
          className="td-night"
          style={{ left: `${(80 / 95) * 100}%`, right: 0 }}
        />
        </>}
        <input
          type="range"
          min="0"
          max={maximum}
          step="1"
          value={shownSlot}
          id={timeInputId || `timeIn${id}`}
          aria-label={compact ? "Время отправления" : "Время отправления, шаг 15 минут"}
          aria-valuetext={`${compact && index >= 0 ? compactSlots[index].label + ", " : ""}${shownTime}, ${timezoneLabel}`}
          style={css({ "--fill": `${fill}%` })}
          onChange={(e) => set(+e.target.value)}
          onKeyDown={(e) => {
            if (compact) return;
            if (e.key === "PageUp" || e.key === "PageDown") {
              e.preventDefault();
              set(slot + (e.key === "PageUp" ? 4 : -4));
            }
          }}
        />
        <div className="td-ticks tnum" aria-hidden="true">
          {compact ? compactSlots.map(({ label }, index) => (
            <span key={label} style={{ left: `${index / maximum * 100}%` }}>{label}</span>
          )) : <>{[0, 24, 48, 72].map((t) => (
            <span key={t} style={{ left: `${(t / 95) * 100}%` }}>
              {String(t / 4).padStart(2, "0")}
            </span>
          ))}
          <span className="next" style={{ left: "100%" }}>
            00<sup>+1</sup>
          </span></>}
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
