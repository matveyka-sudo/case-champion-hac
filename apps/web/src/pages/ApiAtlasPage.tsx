import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
} from "react";
import { backend } from "../api/gateway";
import { ApiError } from "../api/client";
import type {
  AnalysisKind,
  AnalysisResult,
  Schemas,
  Territory,
  Selection,
} from "../api/types";
import {
  initialDraft,
  toRequest,
  DraftError,
  categoryIds,
  categoryLabels,
  isUrbanCity,
  eligibleSnapshots,
  resolvedSnapshotId,
  pipelineProducts,
  passengerModes,
  departureAt,
  type ApiDraft,
} from "../api/adapters/request";
import {
  measureText,
  measureLabel,
  safeSource,
  subjectMatches,
  timestampText,
  deltaText,
  statusText,
  modeText,
  trafficText,
  selectionText,
  reasonText,
  profileLabel,
  noticeText,
  snapshotLabel,
  lastReturnsForSelection,
  scoreBand,
} from "../api/adapters/result";
import { useAnalysisSession } from "../features/analysis/useAnalysisSession";
import { scenarioJourneys } from "../api/adapters/scenario";
import { scopeAllowsArea, scopeContainsTime, urbanScopeAvailability, type UrbanSelection } from "../api/adapters/urbanScopes";
import { journeyLines, segmentGeometryNotice } from "../api/adapters/journeyGeometry";
import { urbanReadiness } from "../api/adapters/urbanReadiness";
import { UrbanReadiness } from "../widgets/UrbanReadiness";
import { CalculatedJourneys } from "../widgets/CalculatedJourneys";
import { DemoModelBanner } from "../widgets/DemoModelBanner";
import { isDemoModelSnapshot } from "../api/adapters/demoModel";
import { Select } from "../shared/Select";
import { DatePicker } from "../shared/DatePicker";
import { Icon, Wordmark } from "../shared/ui";
import { NavigationLink } from "../shared/NavigationLink";
import { ApiMap } from "../widgets/ApiMap";
import { ExplanationFinding } from "../widgets/ExplanationFinding";
import { JourneyDetails } from "../widgets/JourneyDetails";
import { IndexLegend } from "../widgets/IndexLegend";
import { UrbanScopeHelp } from "../widgets/UrbanScopeHelp";
import { CalculatedPoints } from "../widgets/CalculatedPoints";
import { calculatedPoints, pointDisplayName } from "../api/adapters/pointOverview";
import { useModalFocus } from "../features/accessibility";
const kinds: Record<AnalysisKind, string> = {
  urban_passenger: "В городе",
  intercity_passenger: "Пассажирские связи",
  urban_cargo: "Грузовой доступ",
  intercity_cargo: "Грузовые связи",
};
const phaseLabels: Record<string, string> = {
  idle: "Выберите условия и запустите анализ",
  running: "Расчёт выполняется",
  done: "Результат получен",
  failed: "Новый результат не получен",
  cancelled: "Задание отменено",
};
function Sources({ refs }: { refs: Schemas["EvidenceRef"][] }) {
  return (
    <details className="src">
      <summary>Источники и условия</summary>
      {refs.length ? (
        refs.map((e) => (
          <p key={e.evidence_id}>
            {safeSource(e.url) ? (
              <a
                href={safeSource(e.url)}
                target="_blank"
                rel="noopener noreferrer"
              >
                {new URL(safeSource(e.url)!).hostname} · {e.section}
              </a>
            ) : (
              e.section || "Источник без публичной ссылки"
            )}
            <br />
            <small>
              {e.observed_at
                ? `Данные на ${new Date(e.observed_at).toLocaleDateString("ru", { timeZone: "UTC" })}`
                : "Дата наблюдения неизвестна"}{" "}
              · получено{" "}
              {new Date(e.retrieved_at).toLocaleDateString("ru", {
                timeZone: "UTC",
              })}
            </small>
          </p>
        ))
      ) : (
        <p>Ссылки отсутствуют в ответе.</p>
      )}
    </details>
  );
}
function Measure({ value }: { value: Schemas["Metric"] | Schemas["Score"] }) {
  const context = [
    {
      sample_point: "Точка анализа",
      territory: "Территория",
      category: "Категория объектов",
      freight_site: "Грузовая площадка",
      freight_node: "Грузовой узел",
      connection: "Связь",
      pipeline: "Трубопровод",
      city_pair: "Между территориями",
    }[value.subject.kind],
    value.subject.category ? categoryLabels[value.subject.category] : null,
    value.subject.mode ? modeText[value.subject.mode] : null,
  ]
    .filter(Boolean)
    .join(" · ");
  const note = [
    value.completeness === "partial" ? "Частичная оценка" : null,
    reasonText(value.reason_code),
  ]
    .filter(Boolean)
    .join(" · ");
  return (
    <div className="api-measure">
      <span>
        {measureLabel(value.name)}
        {context && <small>{context}</small>}
      </span>
      <b data-score-band={"score_id" in value ? scoreBand(value) : undefined}>{measureText(value)}</b>
      {note && <small>{note}</small>}
    </div>
  );
}
function resultTitle(result: AnalysisResult, catalog: Territory[]) {
  const r = result.request;
  if ("city_id" in r) {
    const id = r.city_id;
    return (
      catalog.find((t) => t.territory_id === id)?.name || "Выбранный город"
    );
  }
  const from = r.origin_id,
    to = r.destination_id;
  return `${catalog.find((t) => t.territory_id === from)?.name || "Территория отправления"} → ${catalog.find((t) => t.territory_id === to)?.name || "Территория назначения"}`;
}
const viewportSubscribe = (cb: () => void) => {
  window.addEventListener("resize", cb);
  return () => window.removeEventListener("resize", cb);
};
export function ApiAtlasPage() {
  const [caps, setCaps] = useState<Schemas["Capabilities"] | null>(null),
    [catalog, setCatalog] = useState<Territory[]>([]),
    [bootError, setBootError] = useState<Error | null>(null),
    [reload, setReload] = useState(0),
    [draft, setDraft] = useState(initialDraft),
    [localError, setLocalError] = useState<DraftError | null>(null),
    [search, setSearch] = useState(""),
    [searchError, setSearchError] = useState(""),
    [selection, setSelection] = useState<Selection | null>(null),
    [chatOpen, setChatOpen] = useState(window.innerWidth >= 1600),
    [filtersOpen, setFiltersOpen] = useState(false),
    [cardHidden, setCardHidden] = useState(false),
    [question, setQuestion] = useState(""),
    [scenarioView, setScenarioView] = useState(false),
    [theme, setTheme] = useState<"auto" | "light" | "dark">("auto"),
    [zoom, setZoom] = useState({ kind: "home", id: 0 });
  const session = useAnalysisSession();
  const resultCard = useRef<HTMLElement>(null), resultTitleRef = useRef<HTMLHeadingElement>(null);
  const demoBanner = useRef<HTMLElement>(null);
  const { result, scenario, dirty, phase } = session;
  const urban = draft.kind.startsWith("urban_"),
    cargo = draft.kind.endsWith("cargo");
  useEffect(() => {
    let alive = true;
    backend
      .capabilities()
      .then(async (value) => {
        const [cities, regions] = await Promise.all([
          backend.territories("city"),
          backend.territories("region"),
        ]);
        if (alive) {
          setCaps(value);
          setCatalog([...cities, ...regions]);
          setDraft((d) => ({
            ...d,
            traffic: value.default_traffic_mode,
            cats: Object.fromEntries(
              Object.entries(d.cats).filter(([id]) =>
                value.categories.includes(
                  categoryIds[id as keyof typeof categoryIds],
                ),
              ),
            ),
          }));
        }
      })
      .catch((e) => {
        if (alive)
          setBootError(e instanceof Error ? e : new Error("API недоступен"));
      });
    return () => {
      alive = false;
    };
  }, [reload]);
  const level = urban ? "city" : draft.level;
  useEffect(() => {
    if (!caps || !search.trim()) return;
    const controller = new AbortController();
    const timer = setTimeout(() => {
      void backend
        .territories(level, search.trim(), controller.signal)
        .then((items) => {
          if (!controller.signal.aborted) {
            setCatalog((old) => [
              ...new Map(
                [...old, ...items].map((t) => [t.territory_id, t]),
              ).values(),
            ]);
            setSearchError("");
          }
        })
        .catch((e) => {
          if (!controller.signal.aborted)
            setSearchError(e instanceof Error ? e.message : "Поиск недоступен");
        });
    }, 250);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [caps, search, level]);
  useEffect(() => {
    document.body.classList.add("mode-atlas");
    return () => document.body.classList.remove("mode-atlas");
  }, []);
  useLayoutEffect(() => {
    document.documentElement.dataset.theme =
      theme === "dark" ||
      (theme === "auto" && (draft.slot >= 80 || draft.slot <= 23))
        ? "dark"
        : "light";
    return () => {
      document.documentElement.dataset.theme = "light";
    };
  }, [theme, draft.slot]);
  const mobile = useSyncExternalStore(
    viewportSubscribe,
    () => window.innerWidth < 768,
    () => false,
  );
  useModalFocus("apiFilters", mobile && filtersOpen);
  useModalFocus("apiChat", mobile && chatOpen);
  useEffect(() => {
    const key = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setFiltersOpen(false);
        setChatOpen(false);
      }
    };
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  }, []);
  const change = (patch: Partial<ApiDraft>) => {
    const cityChanged = patch.cityId !== undefined && patch.cityId !== draft.cityId && !!draft.cityId && patch.kind === undefined;
    if (cityChanged) {
      session.reset();
      setCardHidden(false);
      setSearch("");
      setSearchError("");
      setZoom((z) => ({ kind: "home", id: z.id + 1 }));
    } else session.changed();
    setLocalError(null);
    setSelection(null);
    setScenarioView(false);
    setQuestion("");
    setDraft((d) => ({
      ...d,
      ...(cityChanged ? { areaId: "", snapshotId: "", travel: "" as const } : {}),
      ...patch,
      ...(["kind", "level", "cityId", "originId", "destinationId", "date", "slot"]
        .some((key) => key in patch && patch[key as keyof ApiDraft] !== d[key as keyof ApiDraft]) && !("snapshotId" in patch)
        ? { snapshotId: "" }
        : {}),
    }));
  };
  const choose = useCallback((value: Selection) => {
    setSelection(value);
    setCardHidden(false);
  }, []);
  const chooseCalculatedPoint = (value: Selection) => {
    choose(value);
    setZoom((z) => ({ kind: "selection", id: z.id + 1 }));
    requestAnimationFrame(() => {
      if (resultCard.current) resultCard.current.scrollTop = 0;
      resultTitleRef.current?.focus({ preventScroll: true });
    });
  };
  const start = () => {
    if (!caps) return;
    try {
      const request = toRequest(draft, catalog, caps);
      setLocalError(null);
      setFiltersOpen(false);
      setScenarioView(false);
      void session.run(request);
    } catch (e) {
      setLocalError(
        e instanceof DraftError
          ? e
          : new DraftError(
              "request",
              e instanceof Error ? e.message : "Проверьте параметры",
            ),
      );
    }
  };
  const uiError = localError || session.error || bootError;
  const inline = (field: string, aliases: string[] = []) => {
    const message =
      localError?.field === field
        ? localError.message
        : session.error instanceof ApiError
          ? session.error.fields
              .filter((f) =>
                f.location.some((v) => [field, ...aliases].includes(v)),
              )
              .map((f) => f.message)
              .join("; ")
          : "";
    return message ? (
      <span className="api-field-error" role="alert">
        {message}
      </span>
    ) : null;
  };
  const field = (label: string, node: React.ReactNode) => (
    <div className="fgroup">
      <p className="flabel">{label}</p>
      {node}
    </div>
  );
  const catalogOptions = (l: "city" | "region") => [
    { value: "", label: "Выберите территорию" },
    ...catalog
      .filter(
        (t) =>
          t.level === l &&
          (!urban || (!!caps && isUrbanCity(t, caps))) &&
          (!search ||
            t.name
              .toLocaleLowerCase("ru")
              .includes(search.toLocaleLowerCase("ru")) ||
            [draft.cityId, draft.originId, draft.destinationId].includes(
              t.territory_id,
            )),
      )
      .map((t) => {
        const reason = publishedScopes == null ? undefined
          : !publishedScopes.some((s) => s.city_id === t.territory_id)
            ? "Для этого города нет зарегистрированного расчёта."
            : !publishedScopes.some((s) => s.city_id === t.territory_id && scopeContainsTime(s, scopeDeparture(t.territory_id)))
              ? "Для выбранной даты и времени расчёт недоступен." : undefined;
        return { value: t.territory_id, label: t.name, reason };
      }),
  ];
  const origin = catalog.find(
    (t) => t.territory_id === (urban ? draft.cityId : draft.originId),
  );
  const savedOriginId = result
    ? "city_id" in result.request
      ? result.request.city_id
      : result.request.origin_id
    : null;
  const savedDestinationId = result
    ? "city_id" in result.request
      ? result.request.city_id
      : result.request.destination_id
    : null;
  const savedOrigin = catalog.find((t) => t.territory_id === savedOriginId),
    savedDestination = catalog.find(
      (t) => t.territory_id === savedDestinationId,
    );
  const feature = result?.map_data?.features?.find(
    (f) =>
      f.entity_id === selection?.entity_id &&
      f.entity_kind === selection.entity_kind,
  );
  const linkedIds = selection
    ? [
        selection.entity_id,
        feature?.point_id,
        ...(result?.map_data?.features || [])
          .filter((f) => f.cell_id === selection.entity_id)
          .map((f) => f.entity_id),
      ].filter((id): id is string => !!id)
    : [];
  const values = scenarioView && scenario ? scenario.after : result;
  const pointOverview = useMemo(
    () => result?.analysis_kind === "urban_passenger" ? calculatedPoints(result, values?.scores || []) : null,
    [result, values?.scores],
  );
  const scenarioPaths = useMemo(
    () => result && scenario ? scenarioJourneys(result, scenario) : null,
    [result, scenario],
  );
  const visibleJourneys = scenarioView ? scenarioPaths?.journeys || [] : result?.journeys || [];
  const segmentGeometry = visibleJourneys.some((j) => journeyLines(j).some((part) => part.segment));
  const scenarioGeometryCount = scenarioPaths?.journeys.filter((j) => journeyLines(j).length > 0).length || 0;
  const scenarioMapNotice = !scenarioPaths?.journeys.length
    ? "Пути сценария не предоставлены. Показаны объекты исходного анализа."
    : !scenarioGeometryCount
      ? "Геометрия путей сценария не предоставлена. Показаны объекты исходного анализа; сведения о путях — в карточке."
      : segmentGeometry || scenarioGeometryCount < scenarioPaths.journeys.length || scenarioPaths.missingJourneyIds.length
        ? "Часть геометрии путей сценария не предоставлена. Показаны только полученные линии; объекты — из исходного анализа."
        : "Показаны пути сценария; объекты — из исходного анализа.";
  const shownMetrics =
      values?.metrics.filter(
        (m) =>
          !selection ||
          linkedIds.some((id) =>
            subjectMatches(m.subject, { ...selection, entity_id: id }),
          ),
      ) || [],
    shownScores =
      values?.scores.filter(
        (m) =>
          !selection ||
          linkedIds.some((id) =>
            subjectMatches(m.subject, { ...selection, entity_id: id }),
          ),
      ) || [];
  const ready = caps?.analyses.find((a) => a.analysis_kind === draft.kind);
  const running = phase === "running";
  const requestTitle = result ? resultTitle(result, catalog) : "Россия";
  const toggleMode = (kind: AnalysisKind) =>
    change({
      kind,
      cityId: "",
      originId: "",
      destinationId: "",
      areaId: "",
      profileId: "",
      pipeline: false,
    });
  const selectedJourney =
    visibleJourneys.filter(
      (j) =>
        !selection ||
        linkedIds.includes(j.point_id || "") ||
        selection.entity_id === j.destination_object_id ||
        selection.entity_id === j.origin_node_id ||
        selection.entity_id === j.destination_node_id,
    );
  const profileOptions =
    caps?.cargo_profiles
      .filter((p) =>
        !urban && draft.pipeline
          ? p.kind === "pipeline" && !!draft.pipelineProduct &&
            p.product === pipelineProducts[draft.pipelineProduct] &&
            caps.pipeline_products.includes(p.product)
          : p.kind === "delivery",
      )
      .map((p) => ({
        value: p.profile_id,
        label: profileLabel(p),
      })) || [];
  const snapshots = caps ? eligibleSnapshots(draft, catalog, caps) : [];
  const snapshotId = resolvedSnapshotId(draft, snapshots);
  const savedDemoModel = isDemoModelSnapshot(result?.snapshot_id);
  const demoModel = isDemoModelSnapshot(snapshotId) || savedDemoModel;
  useLayoutEffect(() => {
    const banner = demoBanner.current;
    if (!demoModel || !banner) return;
    const update = () => banner.parentElement?.style.setProperty("--demo-banner-height", `${banner.getBoundingClientRect().height}px`);
    update();
    const observer = new ResizeObserver(update);
    observer.observe(banner);
    return () => observer.disconnect();
  }, [demoModel]);
  const publishedScopes = draft.kind === "urban_passenger" ? caps?.urban_passenger_scopes : null;
  const scopeDeparture = (cityId: string) => {
    const city = catalog.find((t) => t.territory_id === cityId && t.level === "city");
    try { return city ? departureAt(draft.date, draft.slot, city.timezone) : null; }
    catch { return null; }
  };
  const scopeSelection: UrbanSelection = {
    cityId: draft.cityId, snapshotId, areaId: draft.areaId, departureAt: scopeDeparture(draft.cityId),
    mode: draft.travel ? passengerModes[draft.travel] : "", traffic: draft.traffic,
    categories: Object.keys(draft.cats).flatMap((id) => categoryIds[id as keyof typeof categoryIds] || []),
  };
  const scopeState = urbanScopeAvailability(scopeSelection, publishedScopes);
  const readiness = caps && draft.kind === "urban_passenger" ? urbanReadiness(draft, catalog, caps, result, dirty) : null;
  const scopeBlocked = scopeState.published && !!scopeState.problem;
  const categoryReason = (category: Schemas["UrbanPassengerRequest"]["categories"][number]) => {
    if (!scopeState.published) return undefined;
    if (!scopeState.traffic.some((s) => s.categories.includes(category))) return "Категория недоступна для выбранных условий.";
    if (!scopeState.traffic.some((s) => [...new Set([...scopeSelection.categories, category])].every((c) => s.categories.includes(c))))
      return "Нет общего набора расчёта. Снимите несовместимые категории.";
    return undefined;
  };
  const missingSnapshotCoverage = caps?.snapshots !== undefined && !snapshots.length;
  const missingPassengerMode = draft.kind === "urban_passenger" && !draft.travel;
  const shownReturns = lastReturnsForSelection(
    result?.last_returns || [],
    selection,
    result?.map_data?.features || [],
  );
  const hasRanking = result?.scores.some(
    (s) =>
      s.ranking_eligible &&
      s.status === "available" &&
      s.completeness === "full",
  );
  return (
    <div id="atlas" className={`atlas api-atlas${demoModel ? " has-demo-model" : ""}`} aria-label="Атлас Waydex — API">
      <header className="at-top">
        <NavigationLink
          className="at-back"
          to="top"
          aria-label="Вернуться на главную"
        >
          <Icon name="arrow-left" />
        </NavigationLink>
        <Wordmark small />
        <div className="modes" role="group" aria-label="Режим анализа">
          {Object.entries(kinds).map(([kind, label]) => (
            <button
              key={kind}
              aria-pressed={draft.kind === kind}
              className={draft.kind === kind ? "on" : ""}
              onClick={() => toggleMode(kind as AnalysisKind)}
            >
              {label}
            </button>
          ))}
        </div>
        <div className="at-sp" />

        <button
          className="iconbtn api-mobile"
          aria-label="Фильтры"
          onClick={() => {
            setFiltersOpen((v) => !v);
            setChatOpen(false);
          }}
        >
          <Icon name="sliders-horizontal" />
        </button>
        <button
          className="iconbtn"
          aria-label="Тема оформления"
          onClick={() =>
            setTheme((t) =>
              t === "auto" ? "dark" : t === "dark" ? "light" : "auto",
            )
          }
        >
          <Icon name={theme === "dark" ? "moon" : "sun"} />
        </button>
        <button
          className="iconbtn"
          aria-label="Агент"
          aria-pressed={chatOpen}
          onClick={() => {
            setChatOpen((v) => !v);
            setFiltersOpen(false);
          }}
        >
          <Icon name="sparkles" />
          Агент
        </button>
      </header>
      <DemoModelBanner selectedSnapshotId={snapshotId} savedSnapshotId={result?.snapshot_id} hasSavedResult={!!result} bannerRef={demoBanner} />
      <div className={`api-body ${chatOpen ? "with-chat" : ""}`}>
        <aside
          id="apiFilters"
          className={`api-filters ${filtersOpen ? "m-open" : ""}`}
          aria-label="Условия анализа"
        >
          <div className="api-panel-head">
            <b>Условия</b>
            <button
              className="ctx-x api-mobile"
              aria-label="Закрыть фильтры"
              onClick={() => setFiltersOpen(false)}
            >
              <Icon name="x" />
            </button>
          </div>
          {!caps && (
            <div className="stmsg">
              <span>
                {bootError
                  ? "API недоступен"
                  : "Загрузка возможностей сервера…"}
              </span>
              <button
                className="iconbtn"
                onClick={() => {
                  setBootError(null);
                  setReload((v) => v + 1);
                }}
              >
                Повторить
              </button>
            </div>
          )}
          {caps && (
            <>
              {!urban &&
                field(
                  "Масштаб",
                  <Select
                    label="Масштаб"
                    value={draft.level}
                    options={[
                      { value: "city", label: "Города" },
                      {
                        value: "region",
                        label: cargo
                          ? "Субъекты · грузовые узлы"
                          : "Субъекты · пассажиры через столицы",
                      },
                    ]}
                    onChange={(value) =>
                      change({
                        level: value as ApiDraft["level"],
                        originId: "",
                        destinationId: "",
                      })
                    }
                  />,
                )}
              {field(
                "Поиск территорий",
                <>
                  <input
                    className="api-input"
                    aria-label="Поиск территорий"
                    value={search}
                    onChange={(e) => setSearch(e.target.value)}
                    placeholder="Название города или субъекта"
                  />
                  {searchError && <span role="alert">{searchError}</span>}
                </>,
              )}
              {urban ? (
                field(
                  "Город",
                  <>
                    <Select
                      label="Город"
                      value={draft.cityId}
                      options={catalogOptions("city")}
                      onChange={(cityId) => change({ cityId, areaId: "" })}
                    />
                    {inline("cityId", ["city_id"])}
                  </>,
                )
              ) : (
                <>
                  {field(
                    "Откуда",
                    <>
                      <Select
                        label="Территория А"
                        value={draft.originId}
                        options={catalogOptions(draft.level)}
                        onChange={(originId) => change({ originId })}
                      />
                      {inline("originId", ["origin_id"])}
                    </>,
                  )}
                  {field(
                    "Куда",
                    <>
                      <Select
                        label="Территория Б"
                        value={draft.destinationId}
                        options={catalogOptions(draft.level)}
                        onChange={(destinationId) => change({ destinationId })}
                      />
                      {inline("destinationId", ["destination_id"])}
                    </>,
                  )}
                </>
              )}
              {urban &&
                !!caps.areas?.some((a) => a.city_id === draft.cityId) &&
                field(
                  "Район",
                  <Select
                    label="Район"
                    value={draft.areaId}
                    options={[
                      { value: "", label: "Весь город", disabled: scopeState.published && !scopeState.snapshot.some((s) => s.whole_city),
                        reason: scopeState.published && !scopeState.snapshot.some((s) => s.whole_city) ? "Для всего города запуск не зарегистрирован." : undefined },
                      ...caps.areas
                        .filter((a) => a.city_id === draft.cityId)
                        .map((a) => ({ value: a.area_id, label: a.name,
                          disabled: scopeState.published && !scopeState.snapshot.some((s) => scopeAllowsArea(s, a.area_id)),
                          reason: scopeState.published && !scopeState.snapshot.some((s) => scopeAllowsArea(s, a.area_id)) ? "Для этого района запуск не зарегистрирован." : undefined })),
                    ]}
                    onChange={(areaId) => change({ areaId })}
                  />,
                )}
              {caps.snapshots !== undefined &&
                field(
                  "Набор данных",
                  <>
                    <Select
                      label="Набор данных"
                      value={snapshotId}
                      options={[
                        { value: "", label: "Выберите набор данных" },
                        ...snapshots.map((s) => ({
                          value: s.snapshot_id,
                          label: `${isDemoModelSnapshot(s.snapshot_id) ? "ДЕМО · " : ""}${snapshotLabel(s, catalog, caps.snapshots || [])}`,
                          disabled: scopeState.published && !scopeState.time.some((scope) => scope.snapshot_id === s.snapshot_id),
                          reason: scopeState.published && !scopeState.time.some((scope) => scope.snapshot_id === s.snapshot_id) ? "Для этого набора и времени городской расчёт не зарегистрирован." : undefined,
                        })),
                      ]}
                      onChange={(snapshotId) => change({ snapshotId })}
                    />
                    <p className="fhint api-snapshot-hint">
                      {!(urban ? draft.cityId : draft.originId && draft.destinationId)
                        ? "Сначала выберите территорию или пару."
                        : !snapshots.length
                          ? "Нет подтверждённых данных для этих территорий и даты. Измените условия."
                          : snapshots.length === 1
                            ? "Единственный подходящий набор выбран автоматически."
                            : "Есть несколько подходящих наборов. Выберите версию для расчёта."}
                    </p>
                    {snapshots.length === 1 && (
                      <p className="fhint api-snapshot-coverage">
                        {snapshotLabel(snapshots[0], catalog, caps.snapshots)}
                      </p>
                    )}
                    {inline("snapshotId", ["snapshot_id"])}
                  </>,
                )}
              {scopeState.published && (
                <UrbanScopeHelp key={draft.cityId} state={scopeState} selection={scopeSelection} draft={draft} caps={caps} catalog={catalog} onApply={change} />
              )}
              {readiness && <UrbanReadiness value={readiness} />}
              {!cargo &&
                (urban
                  ? field(
                      "Передвижение",
                      <Select
                        label="Передвижение"
                        value={draft.travel}
                        options={[
                          { value: "", label: "Выберите способ передвижения" },
                          { value: "pt", label: "Общественный транспорт" },
                          { value: "walk", label: "Пешком" },
                          { value: "car", label: "Личный автомобиль" },
                        ].map((option) => ({ ...option,
                          disabled: !!option.value && scopeState.published && !scopeState.area.some((s) => s.passenger_modes.includes(passengerModes[option.value as keyof typeof passengerModes])),
                          reason: option.value && scopeState.published && !scopeState.area.some((s) => s.passenger_modes.includes(passengerModes[option.value as keyof typeof passengerModes])) ? "Для выбранных города, времени и набора данных этот способ недоступен." : undefined,
                        }))}
                        onChange={(travel) =>
                          change({ travel: travel as ApiDraft["travel"] })
                        }
                      />,
                    )
                  : field(
                      "Виды транспорта",
                      <div className="chipset">
                        {[
                          ["air", "Самолёт"],
                          ["rail", "Поезд"],
                          ["bus", "Автобус"],
                          ["car", "Автомобиль"],
                          ["sea", "Судно / паром"],
                          ["mixed", "Смешанные"],
                        ].map(([id, label]) => (
                          <button
                            className="fchip"
                            key={id}
                            aria-pressed={draft.paxModes.includes(id)}
                            onClick={() =>
                              change({
                                paxModes: draft.paxModes.includes(id)
                                  ? draft.paxModes.filter((m) => m !== id)
                                  : [...draft.paxModes, id],
                              })
                            }
                          >
                            {label}
                          </button>
                        ))}
                        {inline("paxModes", ["passenger_modes"])}
                      </div>,
                    ))}
              {!cargo &&
                urban &&
                field(
                  "Категории и приоритеты",
                  <>
                    {Object.entries(categoryIds)
                      .filter(([, id]) => caps.categories.includes(id))
                      .map(([id, category]) => (
                        <div className="api-category" key={id} data-unavailable={!!categoryReason(category)}>
                          <label>
                            <input
                              type="checkbox"
                              aria-label={categoryLabels[category]}
                              aria-invalid={!!categoryReason(category)}
                              aria-describedby={categoryReason(category) ? `api-category-${id}-reason` : undefined}
                              disabled={!draft.cats[id as keyof typeof categoryIds] && !!categoryReason(category)}
                              checked={
                                !!draft.cats[id as keyof typeof categoryIds]
                              }
                              onChange={(e) => {
                                const cats = { ...draft.cats };
                                if (e.target.checked)
                                  cats[id as keyof typeof categoryIds] = 2;
                                else
                                  delete cats[id as keyof typeof categoryIds];
                                change({ cats });
                              }}
                            />
                            <span>{categoryLabels[category]}{categoryReason(category) && <small className="api-choice-reason" id={`api-category-${id}-reason`}>{categoryReason(category)}</small>}</span>
                          </label>
                          {draft.cats[id as keyof typeof categoryIds] && (
                            <div
                              role="group"
                              aria-label={
                                "Приоритет: " + categoryLabels[category]
                              }
                            >
                              {[1, 2, 3].map((n) => (
                                <button
                                  className="api-weight"
                                  aria-pressed={
                                    draft.cats[
                                      id as keyof typeof categoryIds
                                    ] === n
                                  }
                                  key={n}
                                  onClick={() =>
                                    change({ cats: { ...draft.cats, [id]: n } })
                                  }
                                >
                                  {n}
                                </button>
                              ))}
                            </div>
                          )}
                        </div>
                      ))}
                    {inline("cats", ["categories", "priorities"])}
                  </>,
                )}
              {cargo && (
                <>
                  {!urban &&
                    field(
                      "Тип грузового анализа",
                      <Select
                        label="Тип грузового анализа"
                        value={draft.pipeline ? "pipeline" : "delivery"}
                        options={[
                          { value: "delivery", label: "Перевозка / доставка" },
                          {
                            value: "pipeline",
                            label: "Трубопровод · отдельно",
                          },
                        ]}
                        onChange={(v) =>
                          change({ pipeline: v === "pipeline", profileId: "" })
                        }
                      />,
                    )}
                  {draft.pipeline &&
                    !urban &&
                    field(
                      "Продукт",
                      <Select
                        label="Продукт трубопровода"
                        value={draft.pipelineProduct}
                        options={[
                          { value: "", label: "Выберите продукт" },
                          { value: "oil", label: "Нефть" },
                          { value: "oilprod", label: "Нефтепродукты" },
                          { value: "gas", label: "Газ" },
                        ].filter((p) => !p.value || caps.pipeline_products.includes(
                          pipelineProducts[p.value as Exclude<ApiDraft["pipelineProduct"], "">],
                        )).map((p) => ({
                          ...p,
                          disabled: !!p.value && !caps.cargo_profiles.some((profile) =>
                            profile.kind === "pipeline" &&
                            profile.product === pipelineProducts[p.value as Exclude<ApiDraft["pipelineProduct"], "">],
                          ),
                        }))}
                        onChange={(v) =>
                          change({
                            pipelineProduct: v as ApiDraft["pipelineProduct"],
                            profileId: "",
                          })
                        }
                      />,
                    )}
                  {field(
                    "Профиль груза",
                    <>
                      <Select
                        label="Профиль груза"
                        value={draft.profileId}
                        options={[
                          { value: "", label: "Выберите профиль груза" },
                          ...profileOptions,
                        ]}
                        onChange={(profileId) => change({ profileId })}
                      />
                      {inline("profileId", ["cargo_profile_id"])}
                      <p className="fhint">
                        {draft.pipeline && !urban
                          ? draft.pipelineProduct
                            ? "Показаны профили выбранного продукта трубопровода."
                            : "Сначала выберите продукт трубопровода."
                          : "Срочность, температура и обработка определены профилем, не угадываются из вида груза."}
                      </p>
                    </>,
                  )}
                  {urban
                    ? field(
                        "Направление",
                        <Select
                          label="Направление"
                          value={draft.direction}
                          options={[
                            { value: "out", label: "Отправка" },
                            { value: "in", label: "Приём" },
                          ]}
                          onChange={(v) =>
                            change({ direction: v as ApiDraft["direction"] })
                          }
                        />,
                      )
                    : !draft.pipeline &&
                      field(
                        "Виды грузового транспорта",
                        <div className="chipset">
                          {[
                            ["road", "Авто"],
                            ["rail", "Ж/д"],
                            ["air", "Авиа"],
                            ["water", "Вода"],
                            ["mixed", "Смешанные"],
                          ].map(([id, label]) => (
                            <button
                              className="fchip"
                              key={id}
                              aria-pressed={draft.cargoModes.includes(id)}
                              onClick={() =>
                                change({
                                  cargoModes: draft.cargoModes.includes(id)
                                    ? draft.cargoModes.filter((m) => m !== id)
                                    : [...draft.cargoModes, id],
                                })
                              }
                            >
                              {label}
                            </button>
                          ))}
                          {inline("cargoModes", ["cargo_modes"])}
                        </div>,
                      )}
                </>
              )}
              {field(
                "Дорожные условия",
                <Select
                  label="Режим трафика"
                  value={draft.traffic}
                  options={caps.traffic_modes.map((v) => ({
                    value: v,
                    label: {
                      static: "Без пробок",
                      typical: "Типичные условия",
                      live: "Текущие условия",
                      historical: "Исторические условия",
                    }[v],
                    disabled: scopeState.published && !scopeState.mode.some((s) => s.traffic_modes.includes(v)),
                    reason: scopeState.published && !scopeState.mode.some((s) => s.traffic_modes.includes(v)) ? "Для выбранного способа передвижения этот режим недоступен." : undefined,
                  }))}
                  onChange={(v) =>
                    change({ traffic: v as ApiDraft["traffic"] })
                  }
                />,
              )}
              <p className="fhint">
                {ready?.calculation_available
                  ? "Доступность данных проверяется для выбранных условий."
                  : "Расчёт этого направления пока недоступен."}
              </p>
              <button
                className="iconbtn primary"
                disabled={!ready?.calculation_available || running || missingSnapshotCoverage || scopeBlocked || missingPassengerMode}
                onClick={start}
              >
                <Icon name="play" />
                Рассчитать
              </button>
            </>
          )}
        </aside>
        <main className="at-map api-map" aria-label="Результат анализа">
          <ApiMap
            result={result}
            scores={values?.scores || []}
            selection={selection}
            onSelect={choose}
            zoomAction={zoom}
            scenarioView={scenarioView}
            journeys={visibleJourneys}
            viewId={scenario?.run_id}
          />
          <div className="api-status panelc" role="status">
            <b>
              {dirty
                ? "Параметры изменены — показан прежний результат"
                : phaseLabels[phase]}
            </b>
            {result && (
              <span>
                {requestTitle} ·{" "}
                {timestampText(
                  result.request.departure_at,
                  savedOrigin?.timezone,
                )}{" "}
                · {trafficText[result.traffic_mode]} ·{" "}
                {statusText[result.status]}
                {"territory_level" in result.request &&
                result.request.territory_level === "region" &&
                result.analysis_kind === "intercity_passenger"
                  ? " · через столицы субъектов"
                  : ""}
              </span>
            )}
            {running && (
              <span>
                {session.job
                  ? statusText[session.job.status] || "Получаю результат"
                  : "Отправляю запрос"}
              </span>
            )}
            {session.job &&
              ["queued", "running"].includes(session.job.status) && (
                <button
                  className="iconbtn"
                  onClick={() => void session.cancel()}
                >
                  Отменить серверное задание
                </button>
              )}
          </div>
          {uiError && (
            <div className="api-error panelc" role="alert">
              <b>
                {uiError instanceof ApiError &&
                uiError.code === "stored_result_incompatible"
                  ? "Сохранённый результат несовместим — требуется новый расчёт"
                  : uiError.message}
              </b>
              {localError && inline(localError.field)}
            </div>
          )}
          {!result && !running && !uiError && (
            <div className="api-empty panelc">
              <Icon name="map" />
              <h3>Выберите условия анализа</h3>
              <p>
                Данные появятся после расчёта. Отсутствие сведений не означает
                низкую связанность.
              </p>
            </div>
          )}
          {result &&
            (!result.map_data ||
              result.map_data.status !== "available" ||
              result.map_data.completeness === "partial" || segmentGeometry) && (
              <div className="api-map-note">
                {segmentGeometry ? `${segmentGeometryNotice}.` : result.map_data?.status === "available"
                  ? "Геометрия доступна частично."
                  : "Геометрия недоступна."}{" "}
                Доступные показатели сохранены.
              </div>
            )}
          {scenarioView && (
            <div className="api-overlay-note">
              {scenarioMapNotice}
            </div>
          )}
          {result && !cardHidden && (
            <section
              ref={resultCard}
              id="apiResultCard"
              className="ctx panelc api-card"
              aria-label="Выбранный объект"
            >
              <div className="ctx-h">
                <div>
                  <span className="k">
                    {savedDemoModel ? scenarioView ? "Демонстрационный сценарий" : "Демонстрационный анализ" : scenarioView ? "Сценарий" : "Сохранённый анализ"}
                  </span>
                  <h3 ref={resultTitleRef} tabIndex={-1}>
                    {selection?.entity_kind === "sample_point"
                      ? pointOverview?.points.find((p) => p.pointId === selection.entity_id)?.name || pointDisplayName(selection.entity_id, feature)
                      : feature?.name ||
                      (selection
                        ? selectionText[selection.entity_kind]
                        : requestTitle)}
                  </h3>
                </div>
                <button
                  className="ctx-x"
                  aria-label="Закрыть карточку"
                  onClick={() => setCardHidden(true)}
                >
                  <Icon name="x" />
                </button>
              </div>
              <div className="ctx-b">
                {selection && (
                  <button
                    className="iconbtn"
                    onClick={() => setSelection(null)}
                  >
                    Все показатели
                  </button>
                )}
                {pointOverview && <CalculatedPoints overview={pointOverview} selection={selection} areaSelected={!!("area_id" in result.request && result.request.area_id)} onSelect={chooseCalculatedPoint} />}
                {!scenarioView && <CalculatedJourneys key={result.analysis_id} result={result} onSelect={chooseCalculatedPoint} />}
                {!shownMetrics.length && !shownScores.length && (
                  <p>
                    Для выбранного объекта нет показателей в ответе сервера.
                  </p>
                )}
                {shownScores.map((s) => (
                  <Measure key={s.score_id} value={s} />
                ))}
                {shownScores.length > 0 && <IndexLegend />}
                {shownMetrics.map((m) => (
                  <Measure key={m.metric_id} value={m} />
                ))}
                {!hasRanking && result.status === "partial" && (
                  <p className="fhint">
                    Оценка неполная и не участвует в полном рейтинге территорий.
                  </p>
                )}
                {feature?.population !== undefined &&
                  (feature.entity_kind === "cell" ||
                    feature.entity_kind === "sample_point" || feature.cell_id) && (
                    <p>
                      Население ячейки:{" "}
                      {feature.population === null
                        ? "неизвестно"
                        : feature.population}{" "}
                      · {feature.population_year || "год неизвестен"} ·{" "}
                      {feature.population_method || "метод неизвестен"}
                    </p>
                  )}
                {selectedJourney.map((journey) => (
                  <JourneyDetails
                    key={journey.journey_id}
                    journey={journey}
                    features={result.map_data?.features || []}
                    originTimezone={savedOrigin?.timezone}
                    destinationTimezone={savedDestination?.timezone}
                    scenario={scenarioView}
                  />
                ))}
                {scenarioView && !selectedJourney.length && (
                  <p className="fhint">Для выбранного объекта путь сценария не предоставлен.</p>
                )}
                {scenario && (
                  <div className="sect">
                    <h4>Сравнение до / после</h4>
                    <div className="segc">
                      <button
                        aria-pressed={!scenarioView}
                        onClick={() => setScenarioView(false)}
                      >
                        База
                      </button>
                      <button
                        aria-pressed={scenarioView}
                        onClick={() => setScenarioView(true)}
                      >
                        Сценарий
                      </button>
                    </div>
                    {scenario.deltas.map((d) => (
                      <p key={d.effect_id}>{deltaText(d)}</p>
                    ))}
                    <p>
                      {scenario.affected_population !== null
                        ? `${scenario.affected_population} жителей`
                        : scenario.affected_sites !== null
                          ? `${scenario.affected_sites} площадок`
                          : "Охват неизвестен"}
                    </p>
                    <p>
                      {scenario.feasibility === "unverified"
                        ? "Модельный эксперимент, реализуемость не подтверждена"
                        : "Реализуемость подтверждена"}
                    </p>
                    {scenario.limitations.map((t, i) => (
                      <p key={i}>{noticeText(t)}</p>
                    ))}
                  </div>
                )}
                {!scenarioView &&
                  (result.counts.length > 0 ||
                    shownReturns.length > 0) && (
                    <details>
                      <summary>Дополнительные показатели</summary>
                      {result.counts.map((c) => (
                        <p key={c.metric_id}>
                          Число доступных объектов ·{" "}
                          {c.status === "available" && c.value !== null
                            ? c.value
                            : statusText[c.status]}
                          {c.threshold_seconds !== null
                            ? ` · за ${(c.threshold_seconds / 60).toLocaleString("ru", { maximumFractionDigits: 1 })} мин`
                            : ""}
                        </p>
                      ))}
                      {shownReturns.map((r, i) => (
                        <p key={i}>
                          Последний обратный рейс · {statusText[r.status]}
                          <br />
                          {result.map_data?.features?.find((f) =>
                            f.entity_kind === "sample_point" && f.entity_id === r.point_id,
                          )?.name || "Точка анализа"}{" → "}
                          {result.map_data?.features?.find((f) =>
                            f.entity_kind === "destination" && f.entity_id === r.destination_object_id,
                          )?.name || "Объект назначения"}
                          <br />
                          Выйти: {timestampText(r.leave_at, r.timezone)}
                          <br />
                          Отправление:{" "}
                          {timestampText(r.first_departure_at, r.timezone)}
                          <br />
                          Прибытие: {timestampText(r.arrival_at, r.timezone)}
                        </p>
                      ))}
                    </details>
                  )}
                {result.limitations.map((t, i) => (
                  <p className="fhint" key={i}>
                    {noticeText(t)}
                  </p>
                ))}
                <Sources refs={scenarioView && scenario ? scenario.evidence_refs : result.evidence_refs} />
                <button
                  className="iconbtn"
                  onClick={() => {
                    setChatOpen(true);
                    setFiltersOpen(false);
                  }}
                >
                  Спросить агента
                </button>
              </div>
            </section>
          )}
          {result && cardHidden && (
            <button
              className="iconbtn panelc api-reopen"
              onClick={() => setCardHidden(false)}
            >
              Показать результат
            </button>
          )}
          <div className="api-bottom">
            <div className="timedock panelc">
              <div className="td-date">
                <label htmlFor="apiDate">Дата</label>
                <DatePicker
                  id="apiDate"
                  value={draft.date}
                  min="1900-01-01"
                  max="2100-12-31"
                  onChange={(date) => change({ date })}
                />
                <span className="tz">
                  {origin?.timezone || "Выберите территорию"}
                </span>
              </div>
              <div className="api-time">
                <label htmlFor="apiTime">
                  {String(Math.floor(draft.slot / 4)).padStart(2, "0")}:
                  {String((draft.slot % 4) * 15).padStart(2, "0")}
                </label>
                <input
                  id="apiTime"
                  aria-label="Время отправления, шаг 15 минут"
                  type="range"
                  min="0"
                  max="95"
                  step="1"
                  value={draft.slot}
                  onChange={(e) => change({ slot: +e.target.value })}
                />
                {inline("date", ["departure_at"])}
              </div>
              <button
                className="iconbtn primary"
                disabled={!ready?.calculation_available || running || missingSnapshotCoverage || scopeBlocked || missingPassengerMode}
                onClick={start}
              >
                Рассчитать
              </button>
            </div>
            <div className="zgroup panelc">
              {[
                ["in", "plus", "Приблизить"],
                ["out", "minus", "Отдалить"],
                ["home", "globe", "Показать Россию"],
              ].map(([kind, icon, label]) => (
                <button
                  className="zbtn"
                  key={kind}
                  aria-label={label}
                  onClick={() => setZoom((z) => ({ kind, id: z.id + 1 }))}
                >
                  <Icon name={icon} />
                </button>
              ))}
            </div>
          </div>
        </main>
        <aside
          id="apiChat"
          className={`api-chat ${chatOpen ? "m-open" : ""}`}
          aria-label="Агент Waydex"
          hidden={!chatOpen}
        >
          <div className="api-panel-head">
            <b>Агент Waydex</b>
            <button
              className="ctx-x"
              aria-label="Свернуть чат"
              onClick={() => setChatOpen(false)}
            >
              <Icon name="x" />
            </button>
          </div>
          <p className="fhint">
            {result
              ? `${requestTitle}${dirty ? " · прежние условия" : " · сохранённый результат"}`
              : "Объяснение доступно после анализа"}
          </p>
          <div className="api-chat-log" aria-live="polite">
            {session.messages.map((m, i) => (
              <div key={i}>
                <div className="msg u">{m.question}</div>
                <div className="msg a">
                  <small>
                    {m.answer.status === "template"
                      ? "Шаблонное объяснение"
                      : "Объяснение модели"}
                  </small>
                  {m.answer.findings.map((finding, j) => (
                    <ExplanationFinding key={j} finding={finding} result={result} onSelect={choose} />
                  ))}
                  {m.answer.proposals.map((p, j) => (
                    <p className="fhint" key={j}>
                      Идея агента: {p.rationale}. Запустить можно только
                      разрешённое сервером предложение ниже.
                    </p>
                  ))}
                  {m.answer.limitations.map((t, j) => (
                    <p key={j}>{noticeText(t)}</p>
                  ))}
                  <Sources refs={m.answer.retrieved_evidence || []} />
                </div>
              </div>
            ))}
            {session.chatBusy && <p role="status">Получаю объяснение…</p>}
            {session.warning && <p role="alert">{session.warning}</p>}
            {session.proposals.map((p) => (
              <div className="proposal" key={p.scenario_id}>
                <b>
                  {p.operation_type === "coordinate_transfer"
                    ? "Согласовать пересадку"
                    : "Дополнительное отправление"}
                </b>
                <p>
                  {p.feasibility === "unverified"
                    ? "Модельный эксперимент, реализуемость не подтверждена"
                    : "Реализуемость подтверждена"}
                </p>
                {p.assumptions.map((t, i) => (
                  <p key={i}>{noticeText(t)}</p>
                ))}
                {p.limitations.map((t, i) => (
                  <p key={i}>{noticeText(t)}</p>
                ))}
                <button
                  className="iconbtn primary"
                  disabled={!p.editable_local_graph || dirty || running}
                  onClick={() => void session.runScenario(p)}
                >
                  Проверить сценарий
                </button>
                {!p.editable_local_graph && (
                  <p>Нет изменяемого локального графа.</p>
                )}
              </div>
            ))}
          </div>
          <form
            className="composer"
            onSubmit={(e) => {
              e.preventDefault();
              void session.explain(question, selection);
              setQuestion("");
            }}
          >
            <textarea
              aria-label="Вопрос агенту"
              placeholder="Спросите о сохранённом результате"
              value={question}
              maxLength={2000}
              disabled={!result || session.chatBusy}
              onChange={(e) => setQuestion(e.target.value)}
            />
            <button
              className="send"
              aria-label="Отправить"
              disabled={!result || session.chatBusy || !question.trim()}
            >
              <Icon name="arrow-up" />
            </button>
          </form>
          <p className="ch-note">
            Объяснение не запускает сценарий. Числа поступают из сохранённого
            расчёта.
          </p>
        </aside>
      </div>
    </div>
  );
}
