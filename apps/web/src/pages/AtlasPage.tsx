import { useLayoutEffect, useEffect, useRef, useState } from "react";
import { useModalFocus } from "../features/accessibility";
import {
  useAtlas,
  currentStatus,
  type Status,
} from "../features/state";
import { fixtures, categoryById } from "../mocks/model";
import { mockGateway } from "../mocks/gateway";
import { Icon, Demo } from "../shared/ui";
import { timeLabel } from "../shared/format";
import { FiltersPanel } from "../widgets/FiltersPanel";
import { RussiaMap } from "../widgets/RussiaMap";
import { CityMap } from "../widgets/CityMap";
import { ResultCard } from "../widgets/ResultCard";
import { AgentChat, type ChatHandle } from "../widgets/AgentChat";
import { TimeDock } from "../widgets/TimeDock";
import { AtlasHeader } from "../widgets/AtlasHeader";
import { AtlasShell } from "../widgets/AtlasShell";
import { MapViewport, MapCanvas, MapOverlay, MapControls, AtlasLegend, AtlasMobileSheet, AtlasStatus } from "../widgets/AtlasMapView";
import { timezone } from "../shared/timezone";
const states: [Status, string, string][] = [
  ["idle", "circle-dashed", "До расчёта"],
  ["stale", "sliders-horizontal", "Параметры изменены"],
  ["run", "loader-circle", "Расчёт выполняется"],
  ["ok", "circle-check", "Результат получен"],
  ["partial", "triangle-alert", "Частичный результат"],
  ["nd", "database", "Нет подтверждённых данных"],
  ["noroute", "ban", "Подтверждённо нет маршрута"],
  ["srcerr", "cloud-off", "Источник недоступен"],
  ["baddate", "calendar-x-2", "Дата не поддерживается"],
  ["pending", "scale", "Индекс ещё не рассчитан"],
  ["agentoff", "bot-off", "Агент недоступен"],
];
function useWindowWidth() {
  const [width, setWidth] = useState(window.innerWidth);
  useEffect(() => {
    const resize = () => setWidth(window.innerWidth);
    window.addEventListener("resize", resize);
    return () => window.removeEventListener("resize", resize);
  }, []);
  return width;
}
export function AtlasPage() {
  const { state: s, dispatch } = useAtlas(),
    p = s.p;
  const width = useWindowWidth(),
    mobile = width < 768;
  const [menu, setMenu] = useState(false),
    [zoom, setZoom] = useState({ kind: "home", id: 0 });
  const mapArea = useRef<HTMLElement>(null),
    sheet = useRef<HTMLDivElement>(null);
  const chat = useRef<ChatHandle>(null),
    request = useRef<AbortController | null>(null),
    filtersButton = useRef<HTMLButtonElement>(null),
    chatButton = useRef<HTMLButtonElement>(null);
  const st = currentStatus(s);
  useModalFocus("filters", mobile && s.mobileFilters);
  useModalFocus("chat", mobile && s.chatOpen);
  useEffect(() => {
    if (!sheet.current || !mapArea.current) return;
    const observer = new ResizeObserver(() => {
      mapArea.current?.style.setProperty(
        "--sheet-h",
        `${sheet.current?.offsetHeight || 196}px`,
      );
    });
    observer.observe(sheet.current);
    return () => observer.disconnect();
  }, []);
  useEffect(() => {
    document.body.classList.add("mode-atlas");
    return () => document.body.classList.remove("mode-atlas");
  }, []);
  useLayoutEffect(() => {
    const night =
      s.themeMode === "dark" ||
      (s.themeMode === "auto" && (p.t >= 80 || p.t <= 23));
    document.documentElement.dataset.theme = night ? "dark" : "light";
    try {
      localStorage.setItem("waydex:theme", JSON.stringify(s.themeMode));
    } catch {
      /* optional storage */
    }
    return () => {
      document.documentElement.dataset.theme = "light";
    };
  }, [s.themeMode, p.t]);
  useEffect(() => () => request.current?.abort(), []);
  useEffect(() => {
    request.current?.abort();
  }, [s.revision]);
  useEffect(() => {
    const key = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        if (menu) setMenu(false);
        else if (s.mobileFilters) {
          dispatch({ type: "ui", patch: { mobileFilters: false } });
          filtersButton.current?.focus();
        } else if (s.chatOpen) {
          dispatch({ type: "ui", patch: { chatOpen: false } });
          chatButton.current?.focus();
        }
      }
    };
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  }, [s.mobileFilters, s.chatOpen, menu, dispatch]);
  const run = async () => {
    request.current?.abort();
    const controller = new AbortController();
    request.current = controller;
    const revision = s.revision,
      params = structuredClone(p);
    dispatch({ type: "run" });
    try {
      const result = await mockGateway.calculate(params, controller.signal);
      dispatch({ type: "complete", revision, params: result });
    } catch (error) {
      if (!(error instanceof DOMException && error.name === "AbortError"))
        dispatch({ type: "ui", patch: { status: "srcerr" } });
    }
  };
  const analyze = () => chat.current?.analyze();
  const cityView =
    (p.mode === "city" && p.city != null) ||
    (p.mode === "cargo" && p.cargoScale === "city");
  const cardVisible =
    !s.cardHidden &&
    (p.mode !== "city" || !p.city || fixtures.city[p.city].grid);
  const selectedModel = p.city ? fixtures.cities[p.city] : null;
  const mobileCell =
    selectedModel?.cells.find((c) => c.id === s.selection?.id) ||
    selectedModel?.canonCell;
  const mobileFocus = (
    s.metric === "overall"
      ? Object.keys(s.applied.cats).sort(
          (a, b) =>
            (s.applied.cats[b as keyof typeof categoryById] || 0) -
            (s.applied.cats[a as keyof typeof categoryById] || 0),
        )[0]
      : s.metric
  ) as keyof typeof categoryById;
  const mobileValue = mobileCell?.values[s.applied.travel][mobileFocus];
  const noResult = ["idle", "nd", "srcerr", "baddate"].includes(st);
  const mobileDescription = noResult
    ? "Для выбранных условий результата нет"
    : p.mode === "city" &&
        mobileCell &&
        (mobileCell.ndPartial || (st === "partial" && mobileCell.j <= -6))
      ? "Для выбранной ячейки нет подтверждённых данных"
      : p.mode === "city" && st === "pending" && mobileValue
        ? `${mobileValue.time} мин · индекс ещё не рассчитан`
        : p.mode === "city" && mobileValue
          ? `${s.scenario === "done" && s.scnView === "scn" ? mobileValue.scenarioTime : mobileValue.time} мин до: ${categoryById[mobileFocus]?.short.toLowerCase()} · индекс ${(s.scenario === "done" && s.scnView === "scn" ? mobileValue.scenarioScore : mobileValue.score).toFixed(1).replace(".", ",")}`
          : p.mode === "pax" && fixtures.passenger[`${p.paxA}>${p.paxB}`]
            ? `Самолёт: 2 ч 10 мин, ${fixtures.passenger[`${p.paxA}>${p.paxB}`].air.dep} отправлений в день · поезд и авто — неизвестно`
            : "Демонстрационный результат";
  const title =
    p.mode === "city"
      ? p.city
        ? fixtures.city[p.city].name
        : "Россия · выберите город"
      : p.mode === "pax"
        ? `${fixtures.places[p.paxA].n} → ${fixtures.places[p.paxB].n}`
        : p.cargoScale === "city"
          ? "Грузы · Новороссийск"
          : `${fixtures.places[p.cargoA].n} → ${fixtures.places[p.cargoB].n}`;
  const statusTitle: Record<Status, string> = {
    idle: "Ещё не рассчитано",
    stale: "Параметры изменены",
    run: "Расчёт выполняется…",
    ok: "Результат получен",
    partial: "Частичный результат",
    nd: "Нет подтверждённых данных",
    noroute: "Подтверждённо нет маршрута",
    srcerr: "Источник недоступен",
    baddate: "Дата не поддерживается",
    pending: "Индекс ещё не рассчитан",
    agentoff: "Результат получен",
    demoonly: "Демо-результат только для одного времени",
  };
  const statusSub =
    st === "stale"
      ? `На карте — прежний результат: ${s.applied.date}, ${timeLabel(s.applied.t)} (${timezone(s.applied)})`
      : st === "partial"
        ? "Для части ячеек нет расписания — они нейтральные и не входят в средние"
        : st === "pending"
          ? "Показаны минуты и исходные показатели; индекс скрыт"
          : st === "demoonly"
            ? "В прототипе рассчитан образец на вт 10 ноября, 08:30"
            : st === "agentoff"
              ? "Агент недоступен — карта и фильтры работают"
              : `вт, 10 ноября, ${timeLabel(p.t)} (${timezone(p)}) · ${p.mode === "city" ? `${p.travel === "pt" ? "Общ. транспорт" : p.travel === "walk" ? "Пешком" : "Автомобиль"} · ${Object.keys(p.cats).length} категории` : title}`;
  const center: { [key: string]: [string, string, string] } = {
    idle: [
      "circle-dashed",
      "Расчёт ещё не выполнялся",
      "Выберите город, способ передвижения и категории, затем нажмите «Рассчитать». До расчёта карта не окрашивается.",
    ],
    run: [
      "loader-circle",
      "Расчёт выполняется",
      "Задание поставлено в очередь. Карту можно двигать — результат появится сам, прежний сохранён.",
    ],
    srcerr: [
      "cloud-off",
      "Источник расписаний недоступен",
      "Это техническая ошибка, а не плохая доступность: ячейки нейтральные, индекс не выставлен. Сохранённый расчёт доступен.",
    ],
    baddate: [
      "calendar-x-2",
      "Дата вне горизонта расписаний",
      "Расписания доступны с 1 по 30 ноября 2026. Календарь не обещает расчёт на даты без данных.",
    ],
    nd: [
      "database",
      "Нет подтверждённых данных",
      "Для выбранных условий в прототипе нет результата — это не низкая доступность.",
    ],
    demoonly: [
      "clock-3",
      "Демо-результат для одного времени",
      "Образец подготовлен на 10 ноября 2026, 08:30. Верните условия, чтобы изучить демонстрацию.",
    ],
  };
  const central =
    center[st] ||
    (p.mode === "city" && p.city === "nvr"
      ? [
          "database",
          "Новороссийск · нет городских данных",
          "Грузовой пример Новороссийска есть в режиме «Грузовые связи».",
        ]
      : null);
  const scale = p.mode === "city" && p.city && fixtures.city[p.city].grid;
  const legendTitle =
    p.mode === "city"
      ? !p.city
        ? "Россия · обзор"
        : !scale
          ? `${title} · нет данных`
          : s.layers.pop
            ? "Модельное население ячейки"
            : st === "pending"
              ? "Время, мин · индекс скрыт"
              : s.metric === "overall"
                ? "Общий индекс · демо"
                : `Индекс: ${categoryById[s.metric as keyof typeof categoryById]?.short.toLowerCase()} · демо`
      : p.mode === "pax"
        ? "Пассажирская связь А → Б"
        : p.cargoScale === "city"
          ? "Грузовой доступ к узлам"
          : "Грузовая связь А → Б";
  return (
    <AtlasShell header={<>
      <AtlasHeader mode={p.mode} title={title} theme={s.themeMode} onModeChange={(mode) => dispatch({ type: "mode", mode })}
        onThemeChange={(themeMode) => dispatch({ type: "ui", patch: { themeMode } })}
        filtersButtonRef={filtersButton} chatButtonRef={chatButton} onFilters={() => dispatch({ type: "ui", patch: { mobileFilters: true } })}
        chatOpen={s.chatOpen} onChat={() => dispatch({ type: "ui", patch: { chatOpen: !s.chatOpen, ...(width < 1520 ? { collapsed: !s.chatOpen } : {}) } })}
        controls={<>
        <div className="dropdown d-only">
          <button
            className="iconbtn"
            aria-expanded={menu}
            aria-controls="statesMenu"
            onClick={() => setMenu(!menu)}
          >
            <Icon name="layers" />
            <span className="st-lbl">Состояния</span>
          </button>
          {menu && (
            <ul
              className="dropdown-menu dropdown-menu-end wx-menu show"
              id="statesMenu"
            >
              {states.map(([v, icon, label]) => (
                <li key={v}>
                  <button
                    className="dropdown-item"
                    onClick={() => {
                      if (v === "ok") dispatch({ type: "reset" });
                      else
                        dispatch({
                          type: "ui",
                          patch: {
                            demoState: v,
                            ...(v === "agentoff"
                              ? { chatOpen: true, collapsed: width < 1520 }
                              : {}),
                          },
                        });
                      setMenu(false);
                    }}
                  >
                    <Icon name={icon} />
                    {label}
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
        </>} />
 </>}>

        <FiltersPanel run={() => void run()} />
        <MapViewport className={`${cardVisible && !mobile ? "has-card" : ""} ${s.chatOpen && !mobile ? "chat-open" : ""} ${["stale", "run", "demoonly"].includes(st) ? "dimmed" : ""}`} mapRef={mapArea}>
          <MapCanvas>
            {cityView ? (
              <CityMap zoomAction={zoom} />
            ) : (
              <RussiaMap zoomAction={zoom} />
            )}
          </MapCanvas>
          <MapOverlay position="tl"><AtlasStatus kind={["stale", "demoonly", "partial"].includes(st) ? "stale" : ["srcerr", "baddate", "noroute"].includes(st) ? "err" : st === "run" ? "run" : ["nd", "pending", "idle"].includes(st) ? "nd" : "ok"}
            title={<>{statusTitle[st]}{["ok", "agentoff"].includes(st) && <> <Demo /></>}</>} subtitle={statusSub}
            actions={<>              {["stale", "demoonly"].includes(st) && (
                <button
                  className="iconbtn primary"
                  style={{ height: 38 }}
                  onClick={() => void run()}
                >
                  <Icon name="play" />
                  Рассчитать
                </button>
              )}
              {st === "demoonly" && (
                <button
                  className="iconbtn"
                  onClick={() => dispatch({ type: "reset" })}
                >
                  <Icon name="rotate-ccw" />
                  08:30
                </button>
              )}
              {s.scenario === "done" && p.mode === "city" && (
                <div
                  className="basecmp"
                  role="group"
                  aria-label="Сравнение базы и сценария"
                >
                  {(["base", "scn"] as const).map((v, i) => (
                    <button
                      key={v}
                      aria-pressed={s.scnView === v}
                      onClick={() =>
                        dispatch({ type: "ui", patch: { scnView: v } })
                      }
                    >
                      {i ? "Сценарий" : "База"}
                    </button>
                  ))}
                </div>
              )}
</>} /></MapOverlay>
          {central && (
            <div className="ov ov-center">
              <div className="statecard panelc" role="status">
                <span className="si">
                  <Icon
                    name={central[0]}
                    className={st === "run" ? "spin" : ""}
                  />
                </span>
                <h3>{central[1]}</h3>
                <p>{central[2]}</p>
                {st === "run" ? (
                  <div className="progress">
                    <i />
                  </div>
                ) : (
                  <div className="row-act">
                    <button
                      className="iconbtn primary"
                      onClick={() =>
                        st === "idle" || st === "srcerr"
                          ? void run()
                          : dispatch({ type: "reset" })
                      }
                    >
                      {st === "srcerr"
                        ? "Повторить"
                        : st === "idle"
                          ? "Рассчитать"
                          : "Сохранённый результат"}
                    </button>
                    {p.mode === "city" && p.city === "nvr" && (
                      <button
                        className="iconbtn"
                        onClick={() =>
                          dispatch({ type: "mode", mode: "cargo" })
                        }
                      >
                        Открыть грузовой пример
                      </button>
                    )}
                  </div>
                )}
              </div>
            </div>
          )}
          <MapOverlay position="r">{!mobile && cardVisible && <ResultCard analyze={analyze} />}</MapOverlay>
          <MapOverlay position="bottom"><AtlasLegend title={legendTitle} scale={!!scale} open={s.legendOpen}
            onToggle={() => dispatch({ type: "ui", patch: { legendOpen: !s.legendOpen } })}
            ticks={s.layers.pop ? ["меньше жителей", "больше"] : [0, 25, 50, 75, 100]} note="ячейки 1 км · при приближении — точки"
            details={<>
                      <div className="lg-items">
                        <span>
                          <i className="sw nd" />
                          Нет данных · нейтрально, не худший индекс
                        </span>
                        <span>
                          <i className="sw sel" />
                          Выбрано
                        </span>
                        <span>
                          <i className="sw obj" />
                          Объект категории (пример)
                        </span>
                      </div>
  </>} items={<>
                  {p.mode === "city" ? (
                    <>
                      <span>
                        <i
                          className="sw obj"
                          style={{
                            background: "var(--accent)",
                            boxShadow: "0 0 0 3px var(--accent-soft)",
                          }}
                        />
                        Город с детализацией
                      </span>
                      <span>
                        <i
                          className="sw"
                          style={{
                            borderRadius: "50%",
                            background: "var(--map-dot)",
                            width: 8,
                            height: 8,
                            margin: "0 4px",
                          }}
                        />
                        Город из справочника · расчёта нет
                      </span>
                    </>
                  ) : (
                    <>
                      <span>
                        <i
                          className="sw"
                          style={{
                            height: 4,
                            borderRadius: 4,
                            background: "var(--accent)",
                          }}
                        />
                        {p.mode === "cargo" && p.cargoScale === "city"
                          ? "Совместимый узел"
                          : "Выбранная направленная связь"}
                      </span>
                      <span>
                        <i className="sw obj" />
                        {p.mode === "cargo" && p.cargoScale === "city"
                          ? "Склад или предприятие"
                          : "Узлы пары"}
                      </span>
                    </>
                  )}
</>} />
            <TimeDock run={() => void run()} />
            <MapControls items={[["in", "plus", "Приблизить"], ["out", "minus", "Отдалить"], ["home", "globe", "Показать всю территорию"], ...(cityView ? [["city", "locate-fixed", "К выбранному городу"]] : [])]}
              onAction={(kind) => setZoom((value) => ({ kind, id: value.id + 1 }))} />
          </MapOverlay>
          <AtlasMobileSheet title={<>{title}{p.mode === "city" && mobileCell ? " · выбрана ячейка" : ""}</>} description={mobileDescription} tag={<Demo />} sheetRef={sheet}
            timeDock={<TimeDock mobile run={() => void run()} />} onFilters={() => dispatch({ type: "ui", patch: { mobileFilters: true } })}
            onChat={() => dispatch({ type: "ui", patch: { chatOpen: true } })} />
          <svg
            width="0"
            height="0"
            style={{ position: "absolute" }}
            aria-hidden="true"
          >
            <defs>
              <pattern
                id="ndHatch"
                patternUnits="userSpaceOnUse"
                width="7"
                height="7"
                patternTransform="rotate(45)"
              >
                <rect
                  width="7"
                  height="7"
                  style={{ fill: "var(--nodata-b)" }}
                />
                <rect
                  width="3"
                  height="7"
                  style={{ fill: "var(--nodata-a)" }}
                />
              </pattern>
            </defs>
          </svg>
        </MapViewport>
        <AgentChat key={s.revision} handle={chat} />
    </AtlasShell>
  );
}
