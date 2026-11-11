import type { Schemas } from '../types';

type Result = Schemas['ScenarioResult'];
type Measure = Schemas['Metric'] | Schemas['Score'];
export function scenarioOverview(result: Result, metrics: Schemas['Metric'][], scores: Schemas['Score'][], selectionActive: boolean) {
  const lookup = (side: Result['before']) => new Map<string, Measure>([
    ...side.metrics.map((m): [string, Measure] => [`metric:${m.metric_id}`, m]),
    ...side.scores.map((s): [string, Measure] => [`score:${s.score_id}`, s]),
  ]);
  const before = lookup(result.before), after = lookup(result.after);
  const selected = new Set([
    ...metrics.map(m => `metric:${m.metric_id}`),
    ...scores.map(s => `score:${s.score_id}`),
  ]);
  const improved = new Set<string>(), worsened = new Set<string>();
  let unknown = 0, improvedEffects = 0, worsenedEffects = 0, hasPoints = false;
  const rows = result.deltas.map(delta => {
    const previous = before.get(`${delta.kind}:${delta.before_id}`);
    const next = after.get(`${delta.kind}:${delta.after_id}`);
    const subject = next?.subject || previous?.subject;
    if (subject?.kind === 'sample_point' && subject.point_id) {
      hasPoints = true;
      if (delta.outcome === 'improved') improved.add(subject.point_id);
      if (delta.outcome === 'worsened' || delta.outcome === 'lost_connection') worsened.add(subject.point_id);
    }
    if (delta.outcome === 'unknown') unknown++;
    if (delta.outcome === 'improved') improvedEffects++;
    if (delta.outcome === 'worsened' || delta.outcome === 'lost_connection') worsenedEffects++;
    return { delta, before: previous, after: next };
  }).filter(row => !selectionActive || selected.has(`${row.delta.kind}:${row.delta.before_id}`) || selected.has(`${row.delta.kind}:${row.delta.after_id}`));
  const rank = (row: typeof rows[number]) => {
    const name = (row.after || row.before)?.name;
    if (name === 'minimum_journey_seconds') return 0;
    if (name === 'point_index') return 1;
    if (name === 'category_index') return 2;
    return row.delta.outcome === 'worsened' || row.delta.outcome === 'lost_connection' ? 3 : 4;
  };
  const numeric = (measure: Measure | undefined): measure is Measure & { value: number } =>
    measure?.status === 'available' && measure.value !== null && Number.isFinite(measure.value);
  const numericRank = (row: typeof rows[number]) => {
    if (numeric(row.before) && numeric(row.after)) return row.before.value !== row.after.value ? 0 : 1;
    return numeric(row.before) || numeric(row.after) ? 2 : 3;
  };
  // Order supplied values for presentation only; preserve the server's proof/outcome/delta.
  rows.sort((a, b) => numericRank(a) - numericRank(b) || rank(a) - rank(b));
  return { rows, hasPoints, improvedPoints: improved.size, worsenedPoints: worsened.size, improvedEffects, worsenedEffects, unknownEffects: unknown, totalEffects: result.deltas.length };
}
