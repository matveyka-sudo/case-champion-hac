import { useAtlas } from "../features/state";
import { timezone } from "../shared/timezone";
import { Icon } from "../shared/ui";
import { css, timeLabel } from "../shared/format";
export function TimeDock({
  run,
  mobile = false,
}: {
  run: () => void;
  mobile?: boolean;
}) {
  const { state: s, dispatch } = useAtlas(),
    p = s.p;
  const fill = (p.t / 95) * 100;
  const set = (t: number) =>
    dispatch({ type: "params", patch: { t: Math.max(0, Math.min(95, t)) } });
  const id = mobile ? "M" : "";
  return (
    <div
      className={mobile ? "timedock" : "timedock panelc"}
      id={mobile ? "mTime" : "timedock"}
    >
      <div className="td-date">
        <label htmlFor={`dateIn${id}`}>Дата</label>
        <input
          type="date"
          id={`dateIn${id}`}
          className="tnum"
          value={p.date}
          min="2026-11-01"
          max="2026-11-30"
          onChange={(e) =>
            dispatch({
              type: "params",
              patch: { date: e.target.value || "2026-11-10" },
            })
          }
        />
        <span className="tz">
          <Icon name="globe" /> {timezone(p)}
        </span>
      </div>
      <div className="td-slider">
        <span
          className="td-now tnum"
          style={{ left: `calc(${fill}% + ${11 - (p.t / 95) * 22}px)` }}
        >
          {timeLabel(p.t)}
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
          value={p.t}
          id={`timeIn${id}`}
          aria-label="Время отправления, шаг 15 минут"
          aria-valuetext={`${timeLabel(p.t)}, ${timezone(p)}`}
          style={css({ "--fill": `${fill}%` })}
          onChange={(e) => set(+e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "PageUp" || e.key === "PageDown") {
              e.preventDefault();
              set(p.t + (e.key === "PageUp" ? 4 : -4));
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
        className={`iconbtn ${s.status === "stale" || s.status === "demoonly" ? "primary" : ""} td-run`}
        onClick={run}
        disabled={s.status === "run"}
      >
        <Icon name="play" />
        <span>
          {p.mode === "pax" || (p.mode === "cargo" && p.cargoScale === "inter")
            ? "Проанализировать"
            : "Рассчитать"}
        </span>
      </button>
    </div>
  );
}
