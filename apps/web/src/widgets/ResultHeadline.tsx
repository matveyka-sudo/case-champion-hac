import type { CSSProperties } from "react";
import type { Schemas } from "../api/types";
import { legText, measureText, scoreBand } from "../api/adapters/result";

export function ResultHeadline({ score, journey, metric }: { score?: Schemas["Score"] | null; journey?: Schemas["JourneyResult"] | null; metric?: Schemas["Metric"] | null }) {
  const timed = journey?.status === "available" && journey.time_kind === "complete_chain" && journey.total_seconds !== null ? journey : null;
  const seconds = metric ? metric.name === "minimum_journey_seconds" && metric.status === "available" ? metric.value : null : timed?.total_seconds;
  if (!score && seconds == null && !timed) return null;
  return (
    <div className="api-result-headline">
      <div className="bigrow">
        {seconds != null && <div><div className="bignum">{(seconds / 60).toLocaleString("ru", { maximumFractionDigits: 1 })}<small>мин</small></div><div className="bigcap">Полное время пути</div></div>}
        {score && <span className="idxpill" data-score-band={scoreBand(score)}><i aria-hidden="true" />{measureText(score)}</span>}
      </div>
      {timed && timed.legs.length > 0 && <div className="mbar" aria-label="Время участков полученного маршрута">
        {timed.legs.map((leg, index) => <span key={index} className={`s ${leg.kind === "ride" ? "ride" : leg.kind === "walk" ? "walk" : "wait"}`}
          style={{ "--m": leg.duration_seconds } as CSSProperties} title={`${legText[leg.kind]}: ${(leg.duration_seconds / 60).toLocaleString("ru", { maximumFractionDigits: 1 })} мин`}>
          {(leg.duration_seconds / 60).toLocaleString("ru", { maximumFractionDigits: 1 })}
        </span>)}
      </div>}
    </div>
  );
}
