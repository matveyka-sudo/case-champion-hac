import { useEffect, useRef, useState, type CSSProperties } from "react";
import type { Schemas } from "../api/types";
import { accessAssessment, accessIcons, accessCategoryColors, accessCategoryStyle } from "../shared/accessibilityPresentation";
import { indexColor } from "../api/adapters/mapIndexColor";
import { Icon } from "../shared/ui";
import { CategoryChoices } from "./CategoryChoices";
import { urbanMovement } from "./urbanMovement";
import { Select } from "../shared/Select";
import { Seg } from "./SegmentedControl";
import { ResultCardFrame, ResultCardHeader } from "./ResultCardView";

import { api, categories, labels, errorMessage, type AddressMode, type Category, type useAddressAccess } from "../features/useAddressAccess";

const number = new Intl.NumberFormat("ru", { maximumFractionDigits: 1 });
type Access = ReturnType<typeof useAddressAccess>;
const priorities = ["low", "medium", "high"] as const;


export function AddressFilters({ access, onPick }: { access: Access; onPick: () => void }) {
  return <>
    <div className="fgroup">
      <p className="flabel">Москва и Московская область</p>
      <p className="fhint">Выберите место, чтобы оценить доступность нужных объектов.</p>
      <form className="api-address-search" onSubmit={(event) => { event.preventDefault(); void access.find(); }}>
        <label htmlFor="home-address">Введите адрес</label>
        <input id="home-address" className="api-input" value={access.query} maxLength={150}
          onChange={(event) => access.editQuery(event.target.value)} placeholder="Москва, Тверская улица, 12" />
        <button type="submit" className="iconbtn" disabled={access.searching || access.query.trim().length < 3}>Найти</button>
      </form>
      {access.searching && <p role="status">Ищем адреса…</p>}
      {access.searchError && <p role="alert" className="api-field-error">{access.searchError}</p>}
      {access.matches.length > 0 && <ul className="api-address-matches" aria-label="Подсказки адресов">
        {access.matches.map((match) => <li key={match.id}>
          <button type="button" className="iconbtn" onClick={() => access.choosePlace({ ...match, addressId: match.id })}>{match.label}</button>
        </li>)}
      </ul>}
      <button type="button" className="iconbtn" aria-pressed={access.picking} onClick={onPick}>
        <Icon name="map-pin" />{access.picking ? "Отменить выбор точки" : "Выбрать на карте"}
      </button>
      {access.place && <p className="fhint">Место расчёта: {access.place.label}</p>}
    </div>
    <div className="fgroup">
      <p className="flabel">Передвижение</p>
      <Seg name="Передвижение" items={urbanMovement} value={access.mode}
        onChange={(mode) => access.changeMode(mode as AddressMode)} />
      {access.mode === "pt" && <>
        <label htmlFor="address-departure">Отправление по московскому времени</label>
        <input id="address-departure" className="api-input" type="datetime-local" value={access.departure}
          min="2026-11-01T00:00" max="2026-11-30T23:59" onChange={(event) => access.changeDeparture(event.target.value)} />
        <p className="fhint">Пилот на отдельных автобусных маршрутах Москвы в ноябре 2026 года.</p>
      </>}
      {access.mode === "car" && <p className="fhint">Статическое время движения без пробок и поиска парковки.</p>}
    </div>
    <div className="fgroup">
      <p className="flabel">Категории</p>
      <CategoryChoices items={categories.map((id) => ({ id, name: labels[id] }))}
        selected={Object.fromEntries(categories.map((id) => [id, access.selected[id] ? priorities.indexOf(access.selected[id]) + 1 : undefined]))}
        onToggle={(value) => {
          const id = value as Category;
          access.changeCategory(id, access.selected[id] ? undefined : "medium");
        }}
        onPriority={(id, priority) => access.changeCategory(id as Category, priorities[priority - 1])} />
      <div className="prio-legend"><span>Н — низкий</span><span>С — средний</span><span>В — высокий</span></div>
    </div>
  </>;
}

export function AddressResultCard({ access, result, stale, onClose, onAsk }: {
  access: Access;
  result: Schemas["AddressResult"]; stale: boolean; onClose: () => void; onAsk: () => void;
}) {
  const driving = result.mode === "car";
  const transit = result.mode === "pt";
  const movement = transit ? "на общественном транспорте" : driving ? "на авто" : "пешком";
  return <ResultCardFrame label="Результат по адресу или точке">
    <ResultCardHeader label={transit ? "Общественный транспорт" : driving ? "Авто" : "Пешком"} title={result.address.label} onClose={onClose} />
    <div className="ctx-b life-result-content">
      {stale && <p className="fhint">Показан результат для предыдущих условий.</p>}
      <section className="api-result-summary" style={{ "--result-color": indexColor(result.index) } as CSSProperties}>
        <div className="api-summary-label">Доступность {movement}</div>
        {result.index != null ? <div className="api-summary-value tnum" style={{color: accessAssessment(result.index).color}}><strong data-address-index={result.index}>{number.format(result.index)}</strong><span>из 100</span></div>
          : <p>{result.address.h_seconds == null ? "Для индекса нужна подтверждённая площадь населённого пункта." : "Для общего индекса нужны маршруты по всем выбранным категориям."}</p>}
        <p className="district-assessment" style={{color: accessAssessment(result.index).color}}>{accessAssessment(result.index).text}</p>
        <button className="iconbtn result-ask-agent" disabled={stale} onClick={onAsk}><Icon name="sparkles" />Спросить агента<Icon name="arrow-up-right" /></button>
      </section>
      <ul className="api-address-results">{result.categories.map((item) => <li key={item.category}>
        <strong className="district-category-label"><span className="category-card-icon" style={{"--category-color": accessCategoryColors[item.category]} as CSSProperties}><Icon name={accessIcons[item.category]} /></span>{labels[item.category]}</strong>
        <span>{item.destination?.name}{item.destination?.line && ` · ${item.destination.line}`}</span>
        <span>{item.seconds == null
          ? item.unavailable_reason === "no_destination" ? "В зоне поиска нет объектов этой категории" : transit ? "Маршрут в режиме ОТ не найден" : "Маршрут по выбранной сети не найден"
          : `${number.format(item.seconds / 60)} мин ${transit && result.source.includes("ЦАО, Valhalla") && !item.transit_lines?.length ? "пешком" : movement}`}
          {!!item.transit_lines?.length && ` · ${item.transit_lines.join(" → ")}`}
          {item.index != null && ` · индекс ${number.format(item.index)}`}</span>
      </li>)}</ul>
      <button type="button" className="iconbtn" disabled={stale || access.compareAdded || !access.compareCompatible || access.comparison.length >= 3} onClick={access.compare}>
        {access.compareAdded ? "Место в сравнении" : "Добавить к сравнению"}
      </button>
      {!stale && !access.compareCompatible && <p className="fhint">Для сравнения нужны одинаковые транспорт, категории и приоритеты.</p>}
      {!!access.comparison.length && <details className="more">
        <summary>Сравнение мест ({access.comparison.length})</summary>
        <ul className="api-address-results">{access.comparison.map(({ data }) => <li key={data.address.id}>
          <strong>{data.address.label}</strong>
          <span>{data.index == null ? "Индекс недоступен" : `${number.format(data.index)} из 100`}</span>
          {data.categories.map((row) => <small key={row.category}>{labels[row.category]} · {row.seconds == null ? "Нет маршрута" : `${number.format(row.seconds / 60)} мин`}</small>)}
          <button className="iconbtn" type="button" onClick={() => access.removeCompared(data.address.id)}>Убрать</button>
        </li>)}</ul>
      </details>}
      <details className="more"><summary>Источники и условия</summary>
        <p>Каталог OpenStreetMap. {transit ? `Отправление: ${result.departure_local?.replace("T", " ")} МСК. Время включает подход, ожидание, поездку, пересадки и пеший участок до цели.` : driving ? "Статическое время движения без пробок и поиска парковки." : "Время суток не влияет на пеший расчёт."}</p><p>{result.source}</p>
      </details>
    </div>
  </ResultCardFrame>;
}
const municipalHospitalNotes: Record<string, string> = {
  "oblast-relation-1644065": "Путь до больницы через КПП № 2 рассчитан для жителей с допуском.",
  "oblast-relation-1703084": "Для категории «Больницы» учтён дневной стационар медсанчасти № 2.",
  "oblast-relation-184751": "Путь до больницы через КПП рассчитан для жителей с допуском.",
};
export function DistrictAccess({ onResult, results, onRemove, onSelect, metric, onMetric }: {
  onResult: (result: Schemas["DistrictResult"] | null) => void;
  results: Schemas["DistrictResult"][]; onRemove: (id: string) => void; onSelect: (result: Schemas["DistrictResult"]) => void;
  metric: string; onMetric: (metric: string) => void;
}) {
  const [kind, setKind] = useState<"moscow" | "oblast">("moscow");
  const [items, setItems] = useState<Schemas["District"][]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const request = useRef<AbortController | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    void api.list("/address-access/districts", "District", controller.signal).then(setItems).catch((failure) => {
      if (!controller.signal.aborted) setError(errorMessage(failure));
    });
    return () => { controller.abort(); request.current?.abort(); };
  }, []);
  const add = async (id: string) => {
    if (!id || busy) return;
    const existing = results.find((result) => result.district.id === id);
    if (existing) { onSelect(existing); return; }
    const controller = new AbortController();
    request.current = controller;
    setBusy(true); setError("");
    try {
      const report = await api.request("/address-access/districts/" + encodeURIComponent(id), "DistrictResult", { signal: controller.signal });
      if (!controller.signal.aborted) onResult(report);
    } catch (failure) {
      if (!controller.signal.aborted) setError(errorMessage(failure));
    } finally {
      if (!controller.signal.aborted) setBusy(false);
    }
  };
  return <div className="fgroup district-picker">
    <h3>Сравнить территории</h3>
    <p className="fhint">Добавьте несколько районов или муниципалитетов и сравните доступность пешком.</p>
    <Seg name="Тип территории" value={kind} items={[["moscow", "Москва"], ["oblast", "Область"]]} onChange={(value) => setKind(value as "moscow" | "oblast")} />
    <p className="flabel">Добавить территорию</p>
    <Select label="Территория" value="" options={[{value: "", label: busy ? "Загружаем…" : "Выбрать территорию"}, ...items.filter((item) => item.id.startsWith(kind + "-") && !results.some((result) => result.district.id === item.id)).map((item) => ({ value: item.id, label: item.name, disabled: busy }))]} onChange={(id) => void add(id)} />
    {error && <p role="alert" className="api-field-error">{error}</p>}
    {busy && <p role="status">Загружаем оценку территории…</p>}
    {!!results.length && <ul className="district-selection" aria-label="Выбранные территории">{results.map((result) => {
      const value = metric === "overall" ? result.index : result.categories[metric] ?? null;
      return <li key={result.district.id}><button className="district-select-name" onClick={() => onSelect(result)}>{result.district.name}<strong style={{color: accessAssessment(value).color}}>{value == null ? "Нет оценки" : number.format(value)}</strong></button><button className="iconbtn" aria-label={`Убрать ${result.district.name}`} onClick={() => onRemove(result.district.id)}><Icon name="x" /></button></li>;
    })}</ul>}
    <p className="flabel">Показатель на карте</p>
    <Select label="Показатель на карте" value={metric} options={[{value: "overall", label: "Общий индекс"}, ...categories.filter((category) => results.some((result) => category in result.categories)).map((value) => ({value, label: labels[value]}))]} onChange={onMetric} />
    <details className="more district-color-help"><summary>Что означают цвета</summary>
      <ul className="district-color-key">
        <li><i style={{background: "#e6adc1"}} /><span>Розовый · менее 40 — низкая доступность</span></li>
        <li><i style={{background: "#f0cdad"}} /><span>Персиковый · 40–59 — средняя</span></li>
        <li><i style={{background: "#cfdabc"}} /><span>Шалфейный · 60–79 — хорошая</span></li>
        <li><i style={{background: "#abcfc3"}} /><span>Мятный · 80–100 — высокая</span></li>
        <li><i style={{background: "#8990a7"}} /><span>Серый · недостаточно данных</span></li>
      </ul>
      <p className="fhint">Цвета плавно переходят друг в друга. Заливка показывает выбранный показатель, синий контур — открытый район.</p>
    </details>
    <div className="district-map-key"><span><i className="district-origin-key" />Места, из которых считали путь</span><span><Icon name="hospital" />Объекты назначения</span></div>
    <p className="fhint">Маленькие точки — исходные места расчёта. Иконки — больницы, учебные заведения и станции. Нажмите на объект, чтобы узнать его название.</p>
  </div>;
}

export function DistrictResultCard({ result, onClose, onAsk, metric = "overall" }: { result: Schemas["DistrictResult"]; onClose: () => void; onAsk: () => void; metric?: string }) {
  const value = metric === "overall" ? result.index : result.categories[metric] ?? null;
  const title = metric === "overall" ? "Индекс пешей доступности" : `${labels[metric as Category]} · доступность пешком`;
  return <ResultCardFrame label="Индекс пешей доступности территории">
    <ResultCardHeader label="Пешком · территория" title={result.district.name} onClose={onClose} />
    <div className="ctx-b life-result-content">
      <section className="api-result-summary" style={{ "--result-color": indexColor(value) } as CSSProperties}><div className="api-summary-label">{title}</div>
      {value == null ? <p>Для выбранного показателя недостаточно покрытия. Посмотрите доступные оценки по категориям.</p> :
        <div className="api-summary-value tnum" style={{color: accessAssessment(value).color}}><strong>{number.format(value)}</strong><span>из 100</span></div>}
      <p className="district-assessment" style={{color: accessAssessment(value).color}}>{accessAssessment(value).text}</p><button className="iconbtn result-ask-agent" onClick={onAsk}><Icon name="sparkles" />Спросить агента<Icon name="arrow-up-right" /></button></section>
      <ul className="api-address-results">{categories.filter((category) => category in result.categories).map((category) => <li key={category}>
        <strong className="district-category-label"><span className="category-card-icon" style={{"--category-color": accessCategoryColors[category]} as CSSProperties}><Icon name={accessIcons[category]} /></span>{labels[category]}</strong>
        <span style={{color: accessAssessment(result.categories[category]).color, fontWeight: 700}}>{result.categories[category] == null ? "Недостаточно данных" : `${number.format(result.categories[category])} из 100`}</span>
        {result.population_covered[category] != null && <small>Охват по оценке населения: {number.format(100 * result.population_covered[category])}%</small>}
      </li>)}</ul>
      <details className="more"><summary>Источники и условия</summary>
        <p>Оценка по населённым ячейкам. Оценённое население: {Math.round(result.population).toLocaleString("ru")}.</p>
        {result.sample_count != null && <p>Выборка: {result.sample_count} точек.</p>}
        {result.overall_population_covered != null && <p>Общий охват: {number.format(100 * result.overall_population_covered)}%.</p>}
        {municipalHospitalNotes[result.district.id] && <p>{municipalHospitalNotes[result.district.id]}</p>}
        <p>{result.source}</p>
      </details>
    </div>
  </ResultCardFrame>;
}

export function CategoryMapLegend({ categoryIds, names = labels, note = "Цвет линии совпадает с иконкой объекта. Синяя точка — выбранное место." }: {
  categoryIds: string[]; names?: Record<string, string>; note?: string;
}) {
  const shown = [...new Set(categoryIds)].filter((id) => id in names);
  if (!shown.length) return null;
  return <details className="panelc category-map-legend" open>
    <summary>Объекты и маршруты</summary>
    <ul>{shown.map((id) => {
      const { icon, color } = accessCategoryStyle(id);
      return <li key={id} style={{"--category-color": color} as CSSProperties}><span className="category-legend-line" /><span className="category-card-icon"><Icon name={icon} /></span><span>{names[id]}</span></li>;
    })}</ul>
    <p>{note}</p>
  </details>;
}
