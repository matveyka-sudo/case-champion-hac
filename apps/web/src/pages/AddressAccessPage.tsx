import { useEffect, useRef, useState, type FormEvent } from "react";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import { ApiClient } from "../api/client";
import type { Schemas } from "../api/types";
import "./address-access.css";

const api = new ApiClient();
type Category = Schemas["AddressRequest"]["categories"][number];
type Priority = "low" | "medium" | "high";
const labels: Record<Category, string> = {
  hospital: "Больницы", school: "Школы", kindergarten: "Детские сады",
  college: "Колледжи", university: "Вузы", metro: "Метро", mcd: "МЦД", mck: "МЦК",
};
const categories = Object.keys(labels) as Category[];
const municipalHospitalNotes: Record<string, string> = {
  "oblast-relation-1644065": "Путь до больницы через КПП № 2 рассчитан для жителей с допуском.",
  "oblast-relation-1703084": "Для категории «Больницы» учтён дневной стационар медсанчасти № 2.",
  "oblast-relation-184751": "Путь до больницы через КПП рассчитан для жителей с допуском.",
};
const number = new Intl.NumberFormat("ru", { maximumFractionDigits: 1 });
const people = new Intl.NumberFormat("ru", { maximumFractionDigits: 0 });

function AccessMap({ address, result, boundary }: {
  address: Schemas["Address"] | null;
  result: Schemas["AddressResult"] | null;
  boundary?: Schemas["DistrictResult"]["boundary"];
}) {
  const container = useRef<HTMLDivElement>(null);
  const map = useRef<L.Map | null>(null);
  useEffect(() => {
    if (!container.current) return;
    const instance = L.map(container.current, { scrollWheelZoom: false }).setView([55.75, 37.62], 10);
    map.current = instance;
    L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
    }).addTo(instance);
    return () => { instance.remove(); map.current = null; };
  }, []);
  useEffect(() => {
    if (!map.current) return;
    const instance = map.current;
    const layers = L.featureGroup().addTo(instance);
    if (boundary) {
      L.geoJSON(boundary, { style: { color: "#2856e4", fillOpacity: 0.14 } }).addTo(layers);
    }
    if (address) {
      const caption = document.createElement("span");
      caption.textContent = address.label;
      L.circleMarker([address.lat, address.lon], { radius: 7 }).bindPopup(caption).addTo(layers);
      result?.categories.forEach((item, index) => {
        if ((item.path?.length ?? 0) < 2) return;
        const caption = document.createElement("span");
        caption.textContent = [labels[item.category], item.destination?.name].filter(Boolean).join(" · ");
        L.polyline((item.path ?? []).map((point) => [point.lat, point.lon]), {
          color: ["#2856e4", "#a42965", "#007854", "#864208"][index % 4],
        }).bindPopup(caption).addTo(layers);
      });
    }
    const bounds = layers.getBounds();
    if (bounds.isValid()) {
      instance.stop();
      instance.invalidateSize({ pan: false });
      instance.fitBounds(bounds, { padding: [32, 32], maxZoom: 16, animate: false });
    }
    return () => { layers.remove(); };
  }, [address, result, boundary]);
  return <div className="address-access__map"><div ref={container} aria-label={boundary ? "Карта границ территории" : "Карта пеших маршрутов"} /></div>;
}

function errorText(error: unknown) {
  return error instanceof Error ? error.message : "Запрос не выполнен. Попробуйте ещё раз.";
}

export default function AddressAccessPage() {
  const [mode, setMode] = useState<"address" | "district" | "municipality">("address");
  const [query, setQuery] = useState("");
  const [matches, setMatches] = useState<Schemas["Address"][]>([]);
  const [address, setAddress] = useState<Schemas["Address"] | null>(null);
  const [enabled, setEnabled] = useState<Category[]>(["hospital", "school", "kindergarten"]);
  const [priorities, setPriorities] = useState<Partial<Record<Category, Priority>>>({});
  const [result, setResult] = useState<Schemas["AddressResult"] | null>(null);
  const [comparison, setComparison] = useState<{ result: Schemas["AddressResult"]; filters: string }[]>([]);
  const [districts, setDistricts] = useState<Schemas["District"][]>([]);
  const [district, setDistrict] = useState("");
  const [districtResult, setDistrictResult] = useState<Schemas["DistrictResult"] | null>(null);
  const [busy, setBusy] = useState("");
  const [notice, setNotice] = useState("");

  const filters = [...enabled].sort().map((category) => category + ":" + (priorities[category] ?? "medium")).join("|");
  const compareMatches = !comparison.length || (comparison[0].filters === filters && comparison[0].result.source === result?.source);
  const compareAdded = comparison.some((item) => item.result.address.id === result?.address.id);

  async function search(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy("Ищем дома…");
    setNotice("");
    setAddress(null);
    setResult(null);
    try {
      const found = await api.list("/address-access/addresses", "Address", undefined, { query: query.trim() });
      setMatches(found);
      if (!found.length) setNotice("Дом не найден в подключённом каталоге. Уточните населённый пункт, улицу и номер.");
    } catch (error) {
      setNotice(errorText(error));
    } finally {
      setBusy("");
    }
  }

  async function calculate() {
    if (!address || !enabled.length) return;
    setBusy("Считаем пешие маршруты…");
    setNotice("");
    setResult(null);
    try {
      setResult(await api.request("/address-access/calculate", "AddressResult", {
        body: { address_id: address.id, categories: enabled, priorities: Object.fromEntries(enabled.map((category) => [category, priorities[category] ?? "medium"])) },
        timeoutMs: 90000,
      }));
    } catch (error) {
      setNotice(errorText(error));
    } finally {
      setBusy("");
    }
  }

  async function openDistricts(next: "district" | "municipality") {
    setMode(next);
    setDistrict("");
    setDistrictResult(null);
    setNotice("");
    if (districts.length) return;
    setBusy("Загружаем территории…");
    try {
      setDistricts(await api.list("/address-access/districts", "District"));
    } catch (error) {
      setNotice(errorText(error));
    } finally {
      setBusy("");
    }
  }

  async function loadDistrict(id: string) {
    setDistrict(id);
    setDistrictResult(null);
    setNotice("");
    if (!id) return;
    setBusy("Загружаем оценку территории…");
    try {
      setDistrictResult(await api.request("/address-access/districts/" + encodeURIComponent(id), "DistrictResult"));
    } catch (error) {
      setNotice(errorText(error));
    } finally {
      setBusy("");
    }
  }

  return <main id="address-access" className="address-access">
    <header className="address-access__header">
      <a href="#atlas" className="wbtn wbtn-light">← К атласу</a>
      <h1>Инфраструктура рядом с домом</h1>
      <p>Москва и Московская область · пешком</p>
    </header>
    <div className="address-access__switch" role="group" aria-label="Масштаб расчёта">
      <button className="iconbtn" disabled={!!busy} aria-pressed={mode === "address"} onClick={() => { setMode("address"); setNotice(""); }}>Адрес</button>
      <button className="iconbtn" disabled={!!busy} aria-pressed={mode === "district"} onClick={() => void openDistricts("district")}>Районы Москвы</button>
      <button className="iconbtn" disabled={!!busy} aria-pressed={mode === "municipality"} onClick={() => void openDistricts("municipality")}>Муниципалитеты области</button>
    </div>
    <div className="address-access__body">
      <section className="address-access__panel" aria-label="Условия и результаты">
        {mode === "address" ? <>
          <form onSubmit={(event) => void search(event)}>
            <label htmlFor="home-address">Город, улица и номер дома</label>
            <div className="address-access__search">
              <input id="home-address" value={query} maxLength={150} disabled={!!busy}
                onChange={(event) => { setQuery(event.target.value); setAddress(null); setMatches([]); setResult(null); setNotice(""); }}
                placeholder="Москва, Тверская улица, 12" />
              <button type="submit" className="iconbtn" disabled={!!busy || query.trim().length < 3}>Найти</button>
            </div>
          </form>
          {matches.length > 0 && <ul className="address-access__matches">
            {matches.map((match) => <li key={match.id}><button className="iconbtn" type="button" disabled={!!busy}
              aria-pressed={match.id === address?.id} onClick={() => { setAddress(match); setResult(null); setNotice(""); }}>
              {match.label}
            </button></li>)}
          </ul>}
          <fieldset disabled={!!busy}>
            <legend>Что важно рядом с домом</legend>
            <div className="address-access__choices">
              {categories.map((category) => <div className="address-access__choice" key={category}><label>
                <input type="checkbox" checked={enabled.includes(category)} onChange={(event) => {
                  setEnabled(event.target.checked ? [...enabled, category] : enabled.filter((item) => item !== category));
                  setResult(null);
                }} />
                {labels[category]}
              </label>{enabled.includes(category) && <select aria-label={`Приоритет: ${labels[category]}`}
                value={priorities[category] ?? "medium"} onChange={(event) => {
                  const priority = event.target.value as Priority;
                  setPriorities((current) => ({ ...current, [category]: priority }));
                  setResult(null);
                }}>
                <option value="low">Низкий</option>
                <option value="medium">Средний</option>
                <option value="high">Высокий</option>
              </select>}</div>)}
            </div>
          </fieldset>
          <button className="iconbtn primary" disabled={!address || !enabled.length || !!busy} onClick={() => void calculate()}>Рассчитать пешком</button>
          {result && <div className="address-access__result">
            <h2>{result.address.label}</h2>
            {result.index == null ? <p>{result.address.h_seconds == null ? "Для этого адреса нет подтверждённой площади населённого пункта для расчёта индекса." : "Для общей оценки нужны маршруты по всем выбранным категориям."}</p>
              : <p className="address-access__index">{number.format(result.index)} <small>из 100 · индекс доступности</small></p>}
            <ul>{result.categories.map((item) => <li key={item.category}>
              <strong>{labels[item.category]}</strong>
              {item.destination && item.seconds != null ? <>
                <span>{item.destination.name}{item.destination.line && " · " + item.destination.line} — {number.format(item.seconds / 60)} мин</span>
                <small>{item.index != null && `${number.format(item.index)} из 100 · `}{item.destination.location === "mapped_entrance" ? "ориентир у входа" : item.destination.location === "station_approach" ? "подход к станции" : "подход к объекту"}</small>
              </> : <span>Пеший маршрут по доступным данным не найден.</span>}
            </li>)}</ul>
            <p className="address-access__scope">Поиск среди ближайших объектов каталога OpenStreetMap. Время суток не влияет на пеший расчёт.</p>
            <button className="iconbtn" disabled={comparison.length >= 3 || compareAdded || !compareMatches}
              onClick={() => setComparison((items) => [...items, { result, filters }])}>
              {compareAdded ? "Адрес в сравнении" : "Добавить к сравнению"}
            </button>
            {!compareMatches && <p>Для сравнения выберите те же категории и приоритеты и пересчитайте адрес.</p>}
          </div>}
        </> : <>
          <label htmlFor="access-district">{mode === "municipality" ? "Муниципалитет Московской области" : "Район или округ Москвы"}</label>
          <select id="access-district" value={district} disabled={!!busy} onChange={(event) => void loadDistrict(event.target.value)}>
            <option value="">Выберите территорию</option>
            {districts.filter((item) => item.id.startsWith("oblast-") === (mode === "municipality")).map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
          </select>
          {districtResult && <div className="address-access__result">
            <h2>{districtResult.district.name}</h2>
            <p>Оценка по населённым ячейкам · около {people.format(districtResult.population)} жителей</p>
            {mode === "municipality" ? <p className="address-access__scope">
              Больницы, школы и детские сады · выборка из {districtResult.sample_count ?? 0} точек GHSL.
              {districtResult.overall_population_covered != null && <> Оценка представляет {number.format(100 * districtResult.overall_population_covered)}% оценённого населения.</>}
            </p> : <p className="address-access__scope">Для округов использованы опубликованные доли населения. В районах вес пограничных ячеек распределён по площади.</p>}
            {districtResult.index != null ? <p className="address-access__index">{number.format(districtResult.index)} <small>из 100 · индекс доступности</small></p> : <p>{mode === "municipality" ? "Для общей оценки пока недостаточно данных о границах городов или пеших маршрутах." : "Для общего индекса пока не хватает маршрутов по всем категориям."}</p>}
            {districtResult.index != null && municipalHospitalNotes[districtResult.district.id] &&
              <p className="address-access__scope">{municipalHospitalNotes[districtResult.district.id]}</p>}
            <ul>{categories.filter((category) => category in districtResult.categories).map((category) => <li key={category}>
              <strong>{labels[category]}</strong>
              <span>{districtResult.categories[category] == null ? "Недостаточно данных" : `${number.format(districtResult.categories[category])} из 100`}</span>
              <small>Маршруты рассчитаны для {number.format(100 * (districtResult.population_covered[category] ?? 0))}% веса населения</small>
            </li>)}</ul>
          </div>}
        </>}
        {busy && <p role="status">{busy}</p>}
        {notice && <p role="alert">{notice}</p>}
      </section>
      <AccessMap address={mode === "address" ? address : null} result={mode === "address" ? result : null}
        boundary={mode !== "address" ? districtResult?.boundary : null} />
    </div>
    {mode === "address" && comparison.length > 0 && <section className="address-access__comparison" aria-labelledby="compare-addresses">
      <h2 id="compare-addresses">Сравнение адресов</h2>
      <p>До трёх адресов с одинаковыми категориями и приоритетами. Шкала индекса учитывает площадь населённого пункта.</p>
      <div className="address-access__table">
        <table>
          <thead><tr><th scope="col">Показатель</th>{comparison.map(({ result: item }) => <th scope="col" key={item.address.id}>
            {item.address.label}
            <button type="button" className="iconbtn" aria-label={`Убрать из сравнения: ${item.address.label}`}
              onClick={() => setComparison((items) => items.filter(({ result: saved }) => saved.address.id !== item.address.id))}>Убрать</button>
          </th>)}</tr></thead>
          <tbody>
            <tr><th scope="row">Индекс</th>{comparison.map(({ result: item }) => <td key={item.address.id}>{item.index == null ? "Недостаточно данных" : `${number.format(item.index)} из 100`}</td>)}</tr>
            {comparison[0].result.categories.map(({ category }) => <tr key={category}><th scope="row">{labels[category]}</th>
              {comparison.map(({ result: item }) => {
                const categoryResult = item.categories.find((row) => row.category === category);
                return <td key={item.address.id}>{categoryResult?.seconds == null ? "Нет времени" : `${number.format(categoryResult.seconds / 60)} мин`}</td>;
              })}
            </tr>)}
          </tbody>
        </table>
      </div>
    </section>}
  </main>;
}
