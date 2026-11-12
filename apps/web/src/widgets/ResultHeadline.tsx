import type { Schemas } from "../api/types";
import { legText, measureLabel, measureText, modeText, scoreBand } from "../api/adapters/result";
import { ResultPrimary, RouteBreakdown } from "./ResultCardView";
const colors = { low: "#c72d44", medium: "#d49520", high: "#0b8a5c", unknown: "#8990a7" };
export function ResultHeadline({ score, journey, metric, destination }: { score?: Schemas["Score"] | null; journey?: Schemas["JourneyResult"] | null; metric?: Schemas["Metric"] | null; destination?: string }) {
  const timed = journey?.status === "available" && journey.time_kind === "complete_chain" && journey.total_seconds !== null ? journey : null;
  const allowed = metric && ["minimum_journey_seconds", "best_observed_time", "best_observed_journey_seconds"].includes(metric.name);
  const seconds = metric ? allowed && metric.status === "available" ? metric.value : null : timed?.total_seconds;
  if (!score && seconds == null && !timed) return null;
  const band = score ? scoreBand(score) : "unknown", found = metric?.name !== "minimum_journey_seconds";
  return <>
    {destination && <p className="fhint">До объекта: {destination}</p>}
    <ResultPrimary time={seconds == null ? null : (seconds / 60).toLocaleString("ru", { maximumFractionDigits: 1 })}
      score={score ? { value: measureText(score), color: colors[band], title: measureLabel(score.name), band } : undefined}
      caption={score?.subject.mode ? modeText[score.subject.mode] : "Полное время пути"}
      qualification={<>{score && <>{measureLabel(score.name)}. </>}{seconds != null && found && "Показано время по найденному маршруту, минимум не подтверждён."}</>} />
    {timed && timed.legs.length > 0 && <RouteBreakdown segments={timed.legs.map((leg) => ({
      kind: leg.kind === "walk" ? "walk" : leg.kind === "ride" ? "ride" : leg.kind === "wait_transfer" ? "hot" : leg.kind === "wait_initial" ? "wait" : "xfer",
      minutes: leg.duration_seconds / 60, valueText: (leg.duration_seconds / 60).toLocaleString("ru", { maximumFractionDigits: 1 }), label: legText[leg.kind],
      barText: `${(leg.duration_seconds / 60).toLocaleString("ru", { maximumFractionDigits: 1 })} мин`,
    }))} />}
  </>;
}
