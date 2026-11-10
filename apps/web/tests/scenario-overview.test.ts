import { describe, expect, it } from 'vitest';
import { scenarioOverview } from '../src/api/adapters/scenarioOverview';
import type { Schemas } from '../src/api/types';
import fixture from './fixtures/api/scenario.json';

describe('scenario selected-object comparison', () => {
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
