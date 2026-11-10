import { useAtlas } from "../features/state";
import { timezone } from "../shared/timezone";
import { TimeDockView } from "./TimeDockView";
export function TimeDock({ run, mobile = false }: { run: () => void; mobile?: boolean }) {
  const { state: s, dispatch } = useAtlas(), p = s.p;
  return <TimeDockView run={run} mobile={mobile} date={p.date} slot={p.t} timezoneLabel={timezone(p)} min="2026-11-01" max="2026-11-30"
    onDateChange={(date) => dispatch({ type: "params", patch: { date } })}
    onSlotChange={(t) => dispatch({ type: "params", patch: { t } })} disabled={s.status === "run"}
    highlighted={s.status === "stale" || s.status === "demoonly"}
    runLabel={p.mode === "pax" || (p.mode === "cargo" && p.cargoScale === "inter") ? "Проанализировать" : "Рассчитать"} />;
}
