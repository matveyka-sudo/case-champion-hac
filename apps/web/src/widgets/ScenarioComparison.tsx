import { useState } from 'react';
import type { Schemas } from '../api/types';
import { scenarioOverview } from '../api/adapters/scenarioOverview';
import { deltaText, measureLabel, measureText, noticeText } from '../api/adapters/result';

export function ScenarioComparison({ scenario, metrics, scores, selectionActive, scenarioView, onViewChange }: {
  scenario: Schemas['ScenarioResult']; metrics: Schemas['Metric'][]; scores: Schemas['Score'][];
  selectionActive: boolean; scenarioView: boolean; onViewChange: (value: boolean) => void;
}) {
  const [visible, setVisible] = useState(6);
  const overview = scenarioOverview(scenario, metrics, scores, selectionActive);
  return <section className="sect api-scenario-comparison" aria-label="Сравнение до / после">
    <h4>Сравнение до / после</h4>
    <div className="segc">
      <button aria-pressed={!scenarioView} onClick={() => onViewChange(false)}>База</button>
      <button aria-pressed={scenarioView} onClick={() => onViewChange(true)}>Сценарий</button>
    </div>
    <p className="fhint">{overview.hasPoints ? 'Точек с улучшениями' : 'Показателей с улучшениями'}: <b>{(overview.hasPoints ? overview.improvedPoints : overview.improvedEffects).toLocaleString('ru')}</b> · с ухудшениями: <b>{(overview.hasPoints ? overview.worsenedPoints : overview.worsenedEffects).toLocaleString('ru')}</b></p>
    <p className="fhint">{selectionActive ? 'Показатели выбранного объекта' : 'Показатели рассчитанного сценария'}</p>
    {overview.rows.slice(0, visible).map(row => <div className="api-comparison-row" key={row.delta.effect_id} data-outcome={row.delta.outcome}>
      <b>{measureLabel((row.after || row.before)?.name || 'Изменение показателя')}</b>
      <div className="api-comparison-values">
        <span><small>Было</small><strong>{row.before ? measureText(row.before) : 'Не предоставлено'}</strong></span>
        <span><small>Стало</small><strong>{row.after ? measureText(row.after) : 'Не предоставлено'}</strong></span>
      </div>
      <p className="fhint">{deltaText(row.delta)}</p>
    </div>)}
    {!overview.rows.length && <p>Изменения для выбранного объекта не предоставлены.</p>}
    {overview.rows.length > visible && <button className="iconbtn" onClick={() => setVisible(value => value + 12)}>Ещё показатели ({overview.rows.length - visible})</button>}
    <p className="fhint">Неизвестных изменений показателей во всём сценарии: {overview.unknownEffects.toLocaleString('ru')}. Они не считаются нулевыми.</p>
    <p className="fhint">{scenario.affected_population !== null ? `${scenario.affected_population.toLocaleString('ru')} жителей затронуто` : scenario.affected_sites !== null ? `${scenario.affected_sites.toLocaleString('ru')} площадок затронуто` : 'Охват населения не подтверждён'}</p>
    <p className="fhint">{scenario.feasibility === 'unverified' ? 'Модельный эксперимент, реализуемость не подтверждена' : 'Реализуемость подтверждена'}</p>
    <details><summary>Ограничения сценария</summary>{[...new Set(scenario.limitations.map(noticeText))].map(text => <p className="fhint" key={text}>{text}</p>)}</details>
  </section>;
}
