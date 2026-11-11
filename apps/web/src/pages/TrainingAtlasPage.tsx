import { useState } from "react";
import { categoryLabels, passengerModes } from "../api/adapters/request";
import { modeText } from "../api/adapters/result";
import type { Category } from "../api/types";
import { Wordmark } from "../shared/ui";
import training from "../mocks/training.json";

const cities: Record<string, string> = { moscow: "Москва", kaliningrad: "Калининград", novorossiysk: "Новороссийск" };
const directions = {
  urban: "В городе",
  passenger: "Пассажирские связи",
  "urban-cargo": "Грузовые связи в городе",
  delivery: "Грузовые связи между городами",
};
const kinds: Record<string, Direction> = { urban_passenger: "urban", intercity_passenger: "passenger", urban_cargo: "urban-cargo", intercity_cargo: "delivery" };
type Direction = keyof typeof directions;
type Example = typeof training.examples[number];
type Conditions = { direction: Direction; id: string; mode: string; category: Category };
type Reading = { total: number; index: number };
const title = (id: string) => id.split(":").slice(1).map((part) => cities[part]).join(" → ");
const number = new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 1 });
const minutes = (seconds: number) => number.format(seconds / 60) + " мин";
const steps: Record<Direction, string[]> = {
  urban: ["Отправление", "Пересадка", "Объект"],
  passenger: ["Узел отправления", "Пересадка", "Узел прибытия"],
  "urban-cargo": ["Склад", "Дорожная сеть", "Терминал"],
  delivery: ["Отправление", "Перегрузка", "Прибытие"],
};
const modes = ["public_transport", "walk", "private_car"];
const modelCategory = "adult_polyclinic";

function initialConditions(): Conditions {
  const params = new URLSearchParams(window.location.search);
  const kind = params.get("kind") || "";
  const direction = Object.hasOwn(kinds, kind) ? kinds[kind] : "urban";
  const city = params.get("city") || "kaliningrad";
  const destination = params.get("destination") || "";
  const requestedId = [direction, city, destination].filter(Boolean).join(":");
  const travel = params.get("travel") as keyof typeof passengerModes | null;
  const requestedCategory = params.get("category") as Category | null;
  return {
    direction, id: requestedId,
    mode: travel && Object.hasOwn(passengerModes, travel) ? passengerModes[travel] : "public_transport",
    category: requestedCategory && Object.hasOwn(categoryLabels, requestedCategory) ? requestedCategory : modelCategory,
  };
}

export function TrainingAtlasPage() {
  const [conditions, setConditions] = useState(initialConditions);
  const [result, setResult] = useState<{ row: Example; reading: Reading; after?: Reading } | null>(null);
  const [comparison, setComparison] = useState(false);
  const [explanation, setExplanation] = useState(false);
  const { direction, id, mode, category } = conditions;
  const options = [...new Set(training.examples.filter((example) => example.id.startsWith(direction + ":")).map((example) => example.id))];
  const selectedId = options.includes(id) ? id : options[0];
  const selected = training.examples.find((example) => example.id === selectedId && (direction !== "urban" || example.mode === mode));
  const change = (patch: Partial<Conditions>) => {
    setConditions((old) => ({ ...old, ...patch }));
    setResult(null);
    setComparison(false);
    setExplanation(false);
  };
  const submit = () => {
    if (!selected) return;
    const reading = direction === "urban" ? selected.categories?.[category] : selected;
    if (!reading) return;
    setResult({ row: selected, reading, after: selected.scenario?.[category] });
    setComparison(false);
    setExplanation(false);
  };
  const route = direction === "urban" && mode !== "public_transport"
    ? ["Отправление", modeText[mode], "Объект"] : steps[direction];

  return <main id="atlas" className="wrap api-workflow">
    <div className="api-preset-options"><Wordmark /><a className="iconbtn" href={"?data=api" + window.location.hash}>К реальным данным</a></div>
    <h1>Учебный пример</h1>
    <p>Синтетические данные. Здесь показаны результаты ядра {training.version}, seed {training.seed}. Срез модели — 9 ноября 2026 года, 08:00 UTC.</p>
    <form className="api-training-controls" onSubmit={(event) => { event.preventDefault(); submit(); }}>
      <label>Направление<select className="api-input" value={direction} onChange={(event) => change({ direction: event.target.value as Direction })}>
        {Object.entries(directions).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
      </select></label>
      <label>Город или пара<select className="api-input" value={selectedId} onChange={(event) => change({ id: event.target.value })}>
        {options.map((value) => <option key={value} value={value}>{title(value)}</option>)}
      </select></label>
      {direction === "urban" && <>
        <label>Передвижение<select className="api-input" value={mode} onChange={(event) => change({ mode: event.target.value })}>
          {modes.map((value) => <option key={value} value={value}>{modeText[value]}</option>)}
        </select></label>
        <label>Категория<select className="api-input" value={category} onChange={(event) => change({ category: event.target.value as Category })}>
          {Object.entries(categoryLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
        </select></label>
      </>}
      <button className="iconbtn primary" type="submit">Показать учебный расчёт</button>
    </form>
    {result && <section aria-label="Результат учебного примера" className="api-training-result">
      <h2>{title(result.row.id)}</h2>
      <p>Учебная схема маршрута{direction === "urban" ? ` · ${modeText[mode]} · ${categoryLabels[category]}` : ""}</p>
      <ol className="api-training-route" aria-label="Этапы учебного маршрута">
        {route.map((label) => <li key={label}>{label}</li>)}
      </ol>
      <dl className="api-training-values">
        <div><dt>Полное время</dt><dd>{minutes(result.reading.total)}</dd></div>
        <div><dt>Индекс учебного маршрута</dt><dd>{number.format(result.reading.index)} / 100</dd></div>
        {result.row.times.map((item) => <div key={item.label}><dt>{item.label}</dt><dd>{minutes(item.seconds)}</dd></div>)}
      </dl>
      <div className="api-preset-options">
        {result.after && <button className="iconbtn" type="button" aria-expanded={comparison} onClick={() => setComparison((value) => !value)}>
          {comparison ? "Вернуться к базе" : "Сравнить согласование пересадки"}
        </button>}
        <button className="iconbtn" type="button" aria-expanded={explanation} onClick={() => setExplanation((value) => !value)}>
          Объяснить учебный результат
        </button>
      </div>
      {comparison && result.after && <p role="status">Полное время {minutes(result.reading.total)} → {minutes(result.after.total)}. Индекс {number.format(result.reading.index)} → {number.format(result.after.index)}.</p>}
      {explanation && <p>Справка по синтетической модели. Для выбранных условий время составляет {minutes(result.reading.total)}. {result.after ? "Есть сохранённое сравнение согласования пересадки." : "Для выбранного примера сравнение сценария не сохранено."} Эти значения описывают учебный маршрут.</p>}
    </section>}
  </main>;
}
