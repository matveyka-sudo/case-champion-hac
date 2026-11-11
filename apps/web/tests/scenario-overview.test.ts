import { describe, expect, it } from 'vitest';
import { scenarioOverview } from '../src/api/adapters/scenarioOverview';
import type { Schemas } from '../src/api/types';
import fixture from './fixtures/api/scenario.json';

describe('scenario selected-object comparison', () => {
  it('reveals supplied changed numeric times before unknown minima without inventing a proven delta', () => {
    const scenario = structuredClone(fixture) as Schemas['ScenarioResult'];
    const template = scenario.before.metrics[0], delta = scenario.deltas[0];
    const unknown = Array.from({length: 8}, (_, index) => ({...template, metric_id: `unknown-${index}`, name: 'minimum_journey_seconds', status: 'unknown' as const, value: null}));
    const known = {...template, metric_id: 'known-route', name: 'best_observed_time', status: 'available' as const, value: 5595.143};
    const unchanged = {...known, metric_id: 'unchanged-route'};
    scenario.before.metrics = [...unknown, unchanged, known];
    scenario.after.metrics = [...unknown, unchanged, {...known, value: 5235.143}];
    scenario.deltas = scenario.before.metrics.map(metric => ({...delta, effect_id: metric.metric_id, kind: 'metric' as const, before_id: metric.metric_id, after_id: metric.metric_id, outcome: 'unknown' as const, delta: null}));
    const before = JSON.stringify(scenario);
    const overview = scenarioOverview(scenario, [], [], false);
    expect(overview.rows.slice(0, 2).map(row => row.delta.effect_id)).toEqual(['known-route', 'unchanged-route']);
    expect(overview.rows).toHaveLength(10);
    expect(overview.unknownEffects).toBe(10);
    expect(overview.rows[0].delta).toMatchObject({outcome: 'unknown', delta: null});
    expect(JSON.stringify(scenario)).toBe(before);
  });
  it('keeps the original delta and nulls while selecting by namespaced identity', () => {
    const scenario = structuredClone(fixture) as Schemas['ScenarioResult'];
    const metric = scenario.before.metrics[0];
    const score = scenario.before.scores[0];
    const overview = scenarioOverview(scenario, [metric], score ? [score] : [], true);
    expect(overview.rows.every(row => row.delta.before_id === metric.metric_id || row.delta.after_id === metric.metric_id || row.delta.before_id === score?.score_id || row.delta.after_id === score?.score_id)).toBe(true);
    for(const row of overview.rows) expect(scenario.deltas).toContainEqual(row.delta);
    expect(scenario).toEqual(fixture);
  });
  it('does not infer changes for a selected object absent from the result', () => {
    const scenario = structuredClone(fixture) as Schemas['ScenarioResult'];
    expect(scenarioOverview(scenario, [], [], true).rows).toEqual([]);
    expect(scenarioOverview(scenario, [], [], false).rows).toHaveLength(scenario.deltas.length);
  });
  it('counts improved points once and keeps unknown and worsening effects visible', () => {
    const scenario = structuredClone(fixture) as Schemas['ScenarioResult'];
    const metric = scenario.before.metrics[0];
    metric.subject = {...metric.subject, kind:'sample_point', point_id:'point-1'};
    scenario.after.metrics = [structuredClone(metric)];
    scenario.deltas = ['improved','improved','worsened','unknown'].map((outcome,i)=>({...scenario.deltas[0],effect_id:String(i),kind:'metric',before_id:metric.metric_id,after_id:metric.metric_id,outcome:outcome as Schemas['ScenarioDelta']['outcome'],delta:outcome==='unknown'?null:1}));
    const view=scenarioOverview(scenario,[metric],[],true);
    expect(view.improvedPoints).toBe(1);expect(view.worsenedPoints).toBe(1);expect(view.unknownEffects).toBe(1);
    expect(view.rows.find(row=>row.delta.outcome==='unknown')?.delta.delta).toBeNull();
  });
});
