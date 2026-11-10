import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
} from "react";
import { EvidenceSources as Sources } from "../widgets/EvidenceSources";
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
  pipelineProducts,
  passengerModes,
  departureAt,
  type ApiDraft,
} from "../api/adapters/request";
import {
  measureText,
  errorText,
  measureLabel,
  subjectMatches,
  timestampText,
  statusText,
  modeText,
  trafficText,
  selectionText,
  reasonText,
  noticeText,
  lastReturnsForSelection,
  scoreBand,
} from "../api/adapters/result";
import { useAnalysisSession } from "../features/analysis/useAnalysisSession";
import { scenarioJourneys } from "../api/adapters/scenario";
import { scopeContainsTime, urbanScopeAvailability, type UrbanSelection } from "../api/adapters/urbanScopes";
import { journeyLines, segmentGeometryNotice } from "../api/adapters/journeyGeometry";
import { urbanReadiness } from "../api/adapters/urbanReadiness";
import { UrbanReadiness } from "../widgets/UrbanReadiness";
import { CalculatedJourneys } from "../widgets/CalculatedJourneys";
import { DemoModelBanner } from "../widgets/DemoModelBanner";
import { demoModelWarning, isDemoModelSnapshot } from "../api/adapters/demoModel";
import { proposalShiftText } from "../api/adapters/result";
import { ResultHeadline } from "../widgets/ResultHeadline";
import { ResultCardFrame, ResultCardHeader, ResultFigure, ResultCategoryTable } from "../widgets/ResultCardView";
import { pointScorePresentation } from "../api/adapters/scorePresentation";
import { ScenarioComparison } from "../widgets/ScenarioComparison";
import { Select } from "../shared/Select";
import { Icon } from "../shared/ui";
import { ApiMap } from "../widgets/ApiMap";
import { ExplanationFinding } from "../widgets/ExplanationFinding";
import { JourneyDetails } from "../widgets/JourneyDetails";
import { IndexLegend } from "../widgets/IndexLegend";
import { CalculatedPoints } from "../widgets/CalculatedPoints";
import { calculatedPoints, routeEstimatePoints, pointDisplayName } from "../api/adapters/pointOverview";
import { useModalFocus } from "../features/accessibility";
import { AtlasShell } from "../widgets/AtlasShell";
import { AtlasHeader } from "../widgets/AtlasHeader";
import { TimeDockView } from "../widgets/TimeDockView";
import { Seg } from "../widgets/SegmentedControl";
import { FiltersPanelView } from "../widgets/FiltersPanelView";
import { CategoryChoices } from "../widgets/CategoryChoices";
import { matchingCargoProfiles } from "../api/adapters/cargoBinding";
import { ChoiceChips, TerritoryPair, CargoProfileChoices } from "../widgets/FilterControlViews";
import { boundSnapshotId, initialSourceDraft } from "../api/adapters/sourceBinding";
import { AgentChatView } from "../widgets/AgentChatView";
import { MapViewport, MapCanvas, MapOverlay, MapControls, AtlasLegend, AtlasMobileSheet, AtlasStatus, StateCard } from "../widgets/AtlasMapView";
const phaseLabels: Record<string, string> = {
  idle: "Выберите условия и запустите анализ",
  running: "Расчёт выполняется",
  done: "Результат получен",
  failed: "Новый результат не получен",
  cancelled: "Задание отменено",
};
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
function ResultDetails({ summary, initialOpen, className, children }: { summary: string; initialOpen: boolean; className: string; children: () => React.ReactNode }) {
  const [open, setOpen] = useState(initialOpen);
  return <details className={`more ${className}`} open={open} onToggle={(event) => setOpen(event.currentTarget.open)}><summary>{summary}</summary>{open && children()}</details>;
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
    [filtersCollapsed, setFiltersCollapsed] = useState(false),
    [cardHidden, setCardHidden] = useState(false),
    [question, setQuestion] = useState(""),
    [interfaceMessages, setInterfaceMessages] = useState<{ question: string; text: string }[]>([]),
    [scenarioView, setScenarioView] = useState(false),
    [theme, setTheme] = useState<"auto" | "light" | "dark">("auto"),
    [legendOpen, setLegendOpen] = useState(false),
    [mapMetric, setMapMetric] = useState("overall"),
    [cargoProfile, setCargoProfile] = useState({ group: "nonfood", handling: "container", limits: {} as Record<string, boolean> }),
    [cargoLayers, setCargoLayers] = useState<Record<string, boolean>>({ ent: true, wh: true, port: true, term: true, rail: true }),
    [layers, setLayers] = useState({ index: true, objects: true, pop: false }),
    [zoom, setZoom] = useState({ kind: "home", id: 0 });
  const session = useAnalysisSession();
  const draftTouched = useRef(false);
  const resultCard = useRef<HTMLElement>(null), resultTitleRef = useRef<HTMLHeadingElement>(null);
  const demoBanner = useRef<HTMLElement>(null);
  const mapArea = useRef<HTMLElement>(null), mobileSheet = useRef<HTMLDivElement>(null);
  const chatInput = useRef<HTMLTextAreaElement>(null);
  const { result, scenario, dirty, phase } = session;
  useLayoutEffect(() => {
    const sheet = mobileSheet.current, area = mapArea.current;
    if (!sheet || !area) return;
    const update = () => area.style.setProperty("--sheet-h", `${sheet.offsetHeight || 196}px`);
    update();
    const observer = new ResizeObserver(update);
    observer.observe(sheet);
    return () => observer.disconnect();
  }, []);
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
          setDraft((d) => draftTouched.current ? d : initialSourceDraft({ ...d, traffic: value.default_traffic_mode }, [...cities, ...regions], value));
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
  useModalFocus("chat", mobile && chatOpen);
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
    draftTouched.current = true;
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
    setInterfaceMessages([]);
    setDraft((d) => ({
      ...d,
      ...(cityChanged ? { areaId: "", snapshotId: "", travel: "" as const } : {}),
      ...patch,
      ...(["kind", "level", "cityId", "originId", "destinationId", "date", "slot", "travel", "cats", "traffic", "areaId"]
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
      const body = resultCard.current?.querySelector<HTMLElement>(".ctx-b");
      if (body) body.scrollTop = 0;
      resultTitleRef.current?.focus({ preventScroll: true });
    });
  };
  const start = () => {
    if (!caps) return;
    try {
      if (draft.kind === "urban_passenger" && draft.cityId && caps.urban_passenger_scopes != null &&
        !caps.urban_passenger_scopes.some((scope) => scope.city_id === draft.cityId))
        throw new DraftError("cityId", caps.urban_passenger_scopes.length ? "Для выбранного города нет зарегистрированного расчёта." : "Для городского расчёта нет зарегистрированных наборов параметров.");
      const profiles = matchingCargoProfiles(caps.cargo_profiles, cargoProfile);
      const profileId = cargo && !draft.pipeline ? profiles.length === 1 ? profiles[0].profile_id : "" : draft.profileId;
      if (cargo && !draft.pipeline && !profileId) throw new DraftError("profileId", "Для выбранного профиля груза нет однозначно подтверждённых данных. Выбранные параметры сохранены.");
      const request = toRequest({ ...draft, profileId, snapshotId: boundSnapshotId(draft, catalog, caps) }, catalog, caps);
      setLocalError(null);
      setFiltersOpen(false);
      setScenarioView(false);
      setInterfaceMessages([]);
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
  const estimateOverview = useMemo(() => result?.analysis_kind === "urban_passenger" ? routeEstimatePoints(result, values?.scores || []) : null, [result, values?.scores]);
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
  const runLabel = urban ? "Рассчитать" : "Проанализировать";
  const draftInstructions = !cargo
    ? urban ? "Выберите город, передвижение и категории" : "Выберите территории и виды транспорта"
    : urban ? "Выберите город, направление и профиль груза" : "Выберите территории и параметры грузовой связи";
  const requestTitle = result ? resultTitle(result, catalog) : origin?.name || "Россия";
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
  const headlinePointScores = shownScores.filter((score) => score.subject.kind === "sample_point" && score.subject.category == null);
  const headlineCategoryScores = shownScores.filter((score) => score.subject.kind === "sample_point" && score.subject.category != null);
  const headlineMinimums = shownMetrics.filter((metric) => metric.name === "minimum_journey_seconds" && metric.status === "available" && metric.value !== null);
  const headlineObservedTimes = shownMetrics.filter((metric) => ["best_observed_time", "best_observed_journey_seconds"].includes(metric.name) && metric.status === "available" && metric.value !== null);
  const exactHeadlineCategories = headlineCategoryScores.filter((score) => score.name !== "observed_route_accessibility");
  const headlineCanonicalScore = headlinePointScores.length === 1 ? headlinePointScores[0] : !headlinePointScores.length && exactHeadlineCategories.length === 1 ? exactHeadlineCategories[0] : undefined;
  const headlineScore = result ? pointScorePresentation(result, shownScores, headlineCanonicalScore).score : undefined;
  const categoryRows = result?.request.analysis_kind === "urban_passenger" && selection ? result.request.categories.map((category) => {
    const candidates = shownScores.filter((score) => score.subject.kind === "sample_point" && score.subject.category === category);
    const exact = candidates.filter((score) => score.name === "category_index");
    const presentation = pointScorePresentation(result, candidates, exact.length === 1 ? exact[0] : undefined, category);
    const minimum = shownMetrics.filter((metric) => metric.subject.category === category && metric.name === "minimum_journey_seconds" && metric.status === "available");
    const observed = shownMetrics.filter((metric) => metric.subject.category === category && ["best_observed_time", "best_observed_journey_seconds"].includes(metric.name) && metric.status === "available");
    const time = minimum.length === 1 ? minimum[0] : observed.length === 1 ? observed[0] : null;
    const priority = result.request.analysis_kind === "urban_passenger" ? result.request.priorities[category] : undefined;
    return { id: category, label: categoryLabels[category] + (presentation.observed ? " · предварительно" : ""), priority: priority ? ({ low: "Н", medium: "С", high: "В" } as const)[priority] : undefined,
      time: time ? measureText(time) : "Нет данных", value: presentation.score ? measureText(presentation.score) : "Нет данных" };
  }) : [];
  const snapshotId = caps ? boundSnapshotId(draft, catalog, caps) : "";
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
  const sendQuestion = () => {
    const text = question.trim();
    if (!text || text.length > 2000 || session.chatBusy) return;
    if (result) void session.explain(text, selection);
    else {
      const guidance = !caps
        ? bootError ? "Возможности сервера сейчас недоступны. Повторите загрузку в фильтрах." : "Возможности сервера загружаются. Дождитесь списка доступных условий в фильтрах."
        : !ready?.calculation_available
          ? "Расчёт выбранного направления сейчас недоступен. Выберите другое направление или вернитесь позже."
          : draft.kind === "urban_passenger" && scopeState.problem
            ? `${scopeState.problem.message} Доступные параметры показаны в фильтрах.`
            : `${draftInstructions}. Проверьте дату и время, затем нажмите «${runLabel}». Доступность результата проверяется сервером.`;
      setInterfaceMessages((messages) => [...messages, { question: text, text: guidance }]);
    }
    setQuestion("");
  };
  const readiness = caps && draft.kind === "urban_passenger" ? urbanReadiness(draft, catalog, caps, result, dirty) : null;
  const categoryReason = (category: Schemas["UrbanPassengerRequest"]["categories"][number]) => {
    if (!scopeState.published) return undefined;
    if (!scopeState.traffic.some((s) => s.categories.includes(category))) return "Категория недоступна для выбранных условий.";
    if (!scopeState.traffic.some((s) => [...new Set([...scopeSelection.categories, category])].every((c) => s.categories.includes(c))))
      return "Нет общего набора расчёта. Снимите несовместимые категории.";
    return undefined;
  };
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
  const renderResultCard = () => result && <ResultCardFrame id="apiResultCard" sectionRef={resultCard} compact={chatOpen && !filtersCollapsed && window.innerWidth < 1600}>
              <ResultCardHeader label={savedDemoModel ? scenarioView ? "Демонстрационный сценарий" : "Демонстрационный анализ" : scenarioView ? "Сценарий" : "Сохранённый анализ"}
                title={selection?.entity_kind === "sample_point" ? pointOverview?.points.find((point) => point.pointId === selection.entity_id)?.name || pointDisplayName(selection.entity_id, feature)
                  : feature?.name || (selection ? selectionText[selection.entity_kind] : requestTitle)} onClose={() => setCardHidden(true)} titleRef={resultTitleRef} />
              <div className="ctx-b">
                {!selection && <p className="fhint">Выберите точку или объект на карте для подробного результата.</p>}
                {selection && (
                  <button
                    className="iconbtn"
                    onClick={() => setSelection(null)}
                  >
                    Все показатели
                  </button>
                )}
                <ResultHeadline score={selection?.entity_kind === "sample_point"
                  ? headlineScore
                  : !selection ? pointOverview?.territoryScore : null}
                  metric={selection ? headlineMinimums.length === 1 ? headlineMinimums[0] : headlineObservedTimes.length === 1 ? headlineObservedTimes[0] : null : null}
                  journey={selection && selectedJourney.length === 1 ? selectedJourney[0] : null} />
                {scenario && <ScenarioComparison scenario={scenario} metrics={shownMetrics} scores={shownScores} selectionActive={!!selection} scenarioView={scenarioView} onViewChange={setScenarioView} />}
                {categoryRows.length > 0 && <div className="sect"><h4>Категории и приоритеты</h4><ResultCategoryTable rows={categoryRows} overall={headlinePointScores.length === 1 ? measureText(headlinePointScores[0]) : "Нет данных"} /></div>}
                {selection && <div className="kvs tnum"><ResultFigure title="Население точки" value={feature?.population == null ? "Неизвестно" : feature.population.toLocaleString("ru")} />
                  <ResultFigure title="Индекс города" value={pointOverview?.territoryScore ? measureText(pointOverview.territoryScore) : "Нет данных"} /></div>}
                {pointOverview && <CalculatedPoints overview={pointOverview} selection={selection} areaSelected={!!("area_id" in result.request && result.request.area_id)} onSelect={chooseCalculatedPoint} />}
                {!!estimateOverview?.points.length && <CalculatedPoints overview={estimateOverview} estimate selection={selection} areaSelected={!!("area_id" in result.request && result.request.area_id)} onSelect={chooseCalculatedPoint} />}
                {!scenarioView && <CalculatedJourneys key={`journey-picker-${result.analysis_id}`} result={result} onSelect={chooseCalculatedPoint} />}
                {!shownMetrics.length && !shownScores.length && (
                  <p>
                    Для выбранного объекта нет показателей в ответе сервера.
                  </p>
                )}
                <ResultDetails className="api-all-measures" key={`measures-${selection?.entity_id || result.analysis_id}`} initialOpen={!!selection || shownScores.length + shownMetrics.length <= 12} summary={`Все показатели (${shownScores.length + shownMetrics.length})`}>{() => <>
                {shownScores.map((s) => (
                  <Measure key={s.score_id} value={s} />
                ))}
                {shownMetrics.map((m) => (
                  <Measure key={m.metric_id} value={m} />
                ))}
                </>}</ResultDetails>
                {!hasRanking && result.status === "partial" && (
                  <p className="fhint">
                    Оценка неполная и не участвует в полном рейтинге территорий.
                  </p>
                )}
                {feature?.population !== undefined &&
                  (feature.entity_kind === "cell" ||
                    feature.entity_kind === "sample_point" || feature.cell_id) && (
                    <div className="api-population">
                      Население ячейки:{" "}
                      {feature.population === null
                        ? "неизвестно"
                        : feature.population}{" "}
                      · {feature.population_year || "год неизвестен"} ·{" "}
                      <details className="more"><summary>Источник населения</summary>{feature.population_method || "метод неизвестен"}</details>
                    </div>
                  )}
                {selectedJourney.length > 0 && <ResultDetails className="api-route-details" key={`routes-${selection?.entity_id || result.analysis_id}`} initialOpen={!!selection || selectedJourney.length <= 3} summary={`Маршруты из ответа (${selectedJourney.length})`}>{() => <>
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
                </>}</ResultDetails>}
                {scenarioView && !selectedJourney.length && (
                  <p className="fhint">Для выбранного объекта путь сценария не предоставлен.</p>
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
                <div className="ctx-actions"><button className="iconbtn" onClick={() => { setChatOpen(true); if (window.innerWidth < 1520) setFiltersCollapsed(true); }}><Icon name="sparkles" />Спросить агента</button></div>
              </div>
            </ResultCardFrame>;
  const moscowView = savedDemoModel || (result ? result.request.analysis_kind === "urban_passenger" && result.request.city_id === "moscow" : urban && draft.cityId === "moscow");
  const timeDock = (compact: boolean) => <TimeDockView date={draft.date} slot={draft.slot} timezoneLabel={origin?.timezone || "Выберите территорию"}
    min="1900-01-01" max="2100-12-31" dateInputId={compact ? "apiMobileDate" : "apiDate"} timeInputId={compact ? "apiMobileTime" : "apiTime"} mobile={compact}
    onDateChange={(date) => change({ date })} onSlotChange={(slot) => change({ slot })} run={start} disabled={!caps || running} highlighted={dirty} />;
  return (
    <AtlasShell className={demoModel ? "has-demo-model" : ""} label="Атлас Waydex — API"
      header={<AtlasHeader mode={draft.kind === "urban_passenger" ? "city" : draft.kind === "intercity_passenger" ? "pax" : "cargo"}
        title={origin?.name || "Россия · выберите город"} theme={theme} onThemeChange={setTheme}
        onModeChange={(mode) => toggleMode(mode === "city" ? "urban_passenger" : mode === "pax" ? "intercity_passenger" : draft.kind.endsWith("cargo") ? draft.kind : "urban_cargo")}
        onFilters={() => { setFiltersOpen(true); setChatOpen(false); }} chatOpen={chatOpen}
        onChat={() => { const open = !chatOpen; setChatOpen(open); setFiltersOpen(false); if (window.innerWidth < 1520) setFiltersCollapsed(open); }} />} notice={      <DemoModelBanner selectedSnapshotId={snapshotId} savedSnapshotId={result?.snapshot_id} hasSavedResult={!!result} bannerRef={demoBanner} />}>
        <FiltersPanelView id="apiFilters" collapsed={filtersCollapsed} mobileOpen={filtersOpen} mobile={mobile} notice={mobile && demoModel ? demoModelWarning : undefined}
          onClose={() => setFiltersOpen(false)} onExpand={() => setFiltersCollapsed(false)}
          onToggle={() => { setFiltersCollapsed((value) => !value); if (filtersCollapsed && window.innerWidth < 1360) setChatOpen(false); }}
          railIcons={!cargo && urban ? ["map-pin", draft.travel === "pt" ? "tram-front" : draft.travel === "walk" ? "footprints" : "car-front", "list-checks"] : ["sliders-horizontal"]}
          footer={<><button className="iconbtn primary runbtn" disabled={!caps || running} onClick={start}><Icon name="play" /><span>{runLabel}</span></button>
            <small>{ready?.calculation_available ? "Данные проверяются для выбранных условий" : "Расчёт этого направления пока недоступен"}</small></>}>
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
              {cargo && field("Режим грузовых связей", <Seg name="Режим грузовых связей" value={urban ? "city" : "inter"}
                items={[["city", "В городе", "building-2"], ["inter", "Между территориями", "waypoints"]]}
                onChange={(value) => toggleMode(value === "city" ? "urban_cargo" : "intercity_cargo")} />)}
              {!urban && !cargo && field("Масштаб", <Seg name="Масштаб" value={draft.level} items={[["city", "Город — город"], ["region", "Субъект — субъект"]]}
                onChange={(value) => change({ level: value as ApiDraft["level"], originId: "", destinationId: "" })} />)}

              {urban ? (
                field(
                  "Город",
                  <>
                    <Seg name="Город" value={draft.cityId || null} items={catalogOptions("city").filter((option) => option.value).map((option) => [option.value, option.label, undefined, false, "reason" in option ? option.reason : undefined])}
                      onChange={(cityId) => change({ cityId, areaId: "" })} />
                    {inline("cityId", ["city_id"])}
                  </>,
                )
              ) : field("Направление", <><TerritoryPair origin={draft.originId} destination={draft.destinationId} options={catalogOptions(draft.level)}
                onChange={(side, value) => change(side === "A" ? { originId: value } : { destinationId: value })}
                onSwap={() => change({ originId: draft.destinationId, destinationId: draft.originId })} />
                {inline("originId", ["origin_id"])}{inline("destinationId", ["destination_id"])}</>)}
              {urban &&
                !!caps.areas?.some((a) => a.city_id === draft.cityId) &&
                field(
                  "Район",
                  <Select
                    label="Район"
                    value={draft.areaId}
                    options={[
                      { value: "", label: "Весь город" },
                      ...caps.areas
                        .filter((a) => a.city_id === draft.cityId)
                        .map((a) => ({ value: a.area_id, label: a.name })),
                    ]}
                    onChange={(areaId) => change({ areaId })}
                  />,
                )}
              {!cargo &&
                (urban
                  ? field(
                      "Передвижение",
                      <><Seg name="Передвижение" value={draft.travel || null}
                        items={[["pt", "Общ. транспорт", "tram-front"], ["walk", "Пешком", "footprints"], ["car", "Авто", "car-front"]]}
                        onChange={(travel) => change({ travel: travel as ApiDraft["travel"] })} />
                        {!draft.travel && <p className="fhint">Выберите способ передвижения для выбранного города.</p>}
                        {inline("travel", ["passenger_mode"])}</>,

                    )
                  : field("Виды транспорта", <><ChoiceChips items={[["air", "Самолёт", "plane"], ["rail", "Поезд", "train-front"], ["bus", "Автобус", "bus-front"], ["car", "Автомобиль", "car-front"], ["sea", "Судно и паром", "ship"], ["mixed", "Смешанные", "route"]]}
                    values={Object.fromEntries(draft.paxModes.map((mode) => [mode, true]))} onChange={(id) => change({ paxModes: draft.paxModes.includes(id) ? draft.paxModes.filter((mode) => mode !== id) : [...draft.paxModes, id] })} />{inline("paxModes", ["passenger_modes"])}</>))}
              {!cargo &&
                urban &&
                field("Категории", <>
                  <CategoryChoices items={Object.entries(categoryIds).map(([id, category]) => ({ id, name: categoryLabels[category], reason: categoryReason(category) }))}
                    selected={draft.cats} onToggle={(id) => { const cats = { ...draft.cats }, key = id as keyof typeof categoryIds; if (cats[key]) delete cats[key]; else cats[key] = 2; change({ cats }); }}
                    onPriority={(id, priority) => change({ cats: { ...draft.cats, [id]: priority } })} />
                  <div className="prio-legend"><span>Н — низкий · 1</span><span>С — средний · 2</span><span>В — высокий · 3</span></div>
                  {inline("cats", ["categories", "priorities"])}
                </>)}
              {!cargo && urban && field("Цвет карты", <>
                <div className="fsel"><span className="k"><Icon name="palette" /></span><Select label="Показатель, которым окрашена карта" value={mapMetric}
                  options={[{ value: "overall", label: "Общий индекс" }, ...Object.keys(draft.cats).map((id) => ({ value: id, label: categoryLabels[categoryIds[id as keyof typeof categoryIds]] }))]} onChange={setMapMetric} /></div>
                <p className="flabel" style={{ marginTop: 14 }}>Слои</p><ChoiceChips items={[["index", "Индекс", "grid-3x3"], ["objects", "Объекты", "map-pin"], ["pop", "Население", "users"]]}
                  values={layers} onChange={(id) => setLayers((current) => ({ ...current, [id]: !current[id as keyof typeof layers] }))} />
                <p className="fhint">Население — оценка источника по ячейке, не число пассажиров.</p>
              </>)}
              {cargo && <>
                {urban ? field("Направление", <><Seg name="Направление" value={draft.direction} items={[["out", "Отправка", "arrow-up-right"], ["in", "Приём", "arrow-down-left"]]}
                  onChange={(value) => change({ direction: value as ApiDraft["direction"] })} /><p className="fhint">Объект → узел и узел → объект считаются отдельно.</p>
                  <p className="flabel" style={{ marginTop: 14 }}>Слои</p><ChoiceChips items={[["ent", "Предприятия", "factory"], ["wh", "Склады", "warehouse"], ["port", "Порты", "anchor"], ["term", "Терминалы", "container"], ["rail", "Грузовые станции", "train-front-tunnel"]]}
                    values={cargoLayers} onChange={(key) => setCargoLayers((current) => ({ ...current, [key]: !current[key] }))} /></>)
                : field("Виды транспорта", <ChoiceChips items={[["road", "Авто", "truck"], ["rail", "Ж/д", "train-front-tunnel"], ["air", "Воздух", "plane-takeoff"], ["water", "Вода", "anchor"], ["pipe", "Трубопровод", "droplet"]]}
                  values={{ ...Object.fromEntries(draft.cargoModes.map((mode) => [mode, true])), pipe: draft.pipeline }} onChange={(key) => key === "pipe" ? change({ pipeline: !draft.pipeline, profileId: "" }) : change({ cargoModes: draft.cargoModes.includes(key) ? draft.cargoModes.filter((mode) => mode !== key) : [...draft.cargoModes, key] })} />)}
                <CargoProfileChoices value={cargoProfile} onChange={(profile) => { setCargoProfile(profile); change({ profileId: "", pipeline: profile.handling === "liquid" && draft.pipeline }); }} />
                {inline("profileId", ["cargo_profile_id"])}
                {!urban ? field("Продукт трубопровода", <><Seg name="Продукт трубопровода" value={draft.pipelineProduct || null} items={[["oil", "Нефть", undefined, cargoProfile.handling !== "liquid" || !draft.pipeline], ["oilprod", "Нефтепродукты", undefined, cargoProfile.handling !== "liquid" || !draft.pipeline], ["gas", "Газ", undefined, cargoProfile.handling !== "liquid" || !draft.pipeline]]}
                  onChange={(value) => { const product = value as Exclude<ApiDraft["pipelineProduct"], "">; const profiles = caps.cargo_profiles.filter((profile) => profile.kind === "pipeline" && profile.product === pipelineProducts[product]); change({ pipelineProduct: product, profileId: profiles.length === 1 ? profiles[0].profile_id : "" }); }} />
                  <p className="fhint">Трубопровод применим к наливному грузу. Нефть, нефтепродукты и газ считаются отдельно.</p></>) : <div className="fgroup"><div className="disabled-note"><Icon name="info" />Трубопроводы учитываются только между территориями.</div></div>}
              </>}
              <details className="more"><summary>Источники и условия данных</summary>
                {readiness && <UrbanReadiness value={readiness} />}
                <p className="fhint">Набор данных определяется по городу, дате, передвижению и выбранным категориям. {scopeState.problem?.message}</p>
                {inline("snapshotId", ["snapshot_id"])}
              </details>
              <details className="more api-territory-search"><summary>Поиск по каталогу</summary>{field(
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
              )}</details>
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
            </>
          )}
        </FiltersPanelView>
        <MapViewport mapRef={mapArea} className={`${result && !cardHidden && !mobile ? "has-card" : ""} ${!result ? "has-empty-state" : ""} ${chatOpen && !mobile ? "chat-open" : ""} ${dirty ? "dimmed" : ""}`}><MapCanvas>
          <ApiMap
            result={result}
            scores={values?.scores || []}
            selection={selection}
            onSelect={choose}
            zoomAction={zoom}
            scenarioView={scenarioView}
            journeys={visibleJourneys}
            cityId={urban ? draft.cityId : undefined} viewId={scenario?.run_id}
            category={mapMetric === "overall" ? null : categoryIds[mapMetric as keyof typeof categoryIds]} layers={layers}
          /></MapCanvas>
          <MapOverlay position="tl"><AtlasStatus kind={uiError ? "err" : running ? "run" : dirty || result?.status === "partial" ? "stale" : result ? "ok" : "nd"}
            title={dirty ? "Параметры изменены — показан прежний результат" : phaseLabels[phase]}
            subtitle={<>{result ? `${requestTitle} · ${timestampText(result.request.departure_at, savedOrigin?.timezone)} · ${trafficText[result.traffic_mode]} · ${statusText[result.status]}` : draftInstructions}
              {running && ` · ${session.job ? statusText[session.job.status] || "Получаю результат" : "Отправляю запрос"}`}</>}
            actions={session.job && ["queued", "running"].includes(session.job.status) && <button className="iconbtn" onClick={() => void session.cancel()}>Отменить</button>} /></MapOverlay>
          {uiError && <StateCard role="alert" icon="cloud-off" title={uiError instanceof ApiError && uiError.code === "stored_result_incompatible" ? "Сохранённый результат несовместим — требуется новый расчёт" : "Новый результат не получен"}
            description={errorText(uiError)} actions={<button className="iconbtn primary" onClick={start}>Повторить</button>} />}
          {!result && !uiError && <StateCard icon={running ? "loader-circle" : "circle-dashed"} loading={running}
            title={running ? "Расчёт выполняется" : "Расчёт ещё не выполнялся"} description={running ? "Карту можно двигать — результат появится после завершения расчёта." : `${draftInstructions}, затем нажмите «${runLabel}». До расчёта карта не окрашивается.`}
            actions={<button className="iconbtn primary" disabled={!caps} onClick={start}>{runLabel}</button>} />}
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
          {result && !cardHidden && !mobile && <MapOverlay position="r">{renderResultCard()}</MapOverlay>}
          {result && cardHidden && (
            <button
              className="iconbtn panelc api-reopen"
              onClick={() => setCardHidden(false)}
            >
              Показать результат
            </button>
          )}
          <MapOverlay position="bottom">
            <AtlasLegend title={layers.pop ? "Население ячейки" : values?.scores.some((score) => score.name === "observed_route_accessibility" && score.status === "available") ? "Предварительная оценка по найденному маршруту" : "Индекс доступности"}
              scale={!!values?.scores.length || layers.pop} open={legendOpen} onToggle={() => setLegendOpen((value) => !value)} ticks={layers.pop ? ["меньше жителей", "больше"] : [0, 25, 50, 75, 100]}
              note={layers.pop ? "По данным источника · неизвестное нейтрально" : "Ниже 50 · 50–<75 · 75–100"}
              barStyle={layers.pop ? undefined : { background: "linear-gradient(to right,#c72d44 0%,#c72d44 50%,#d49520 50%,#d49520 75%,#0b8a5c 75%,#0b8a5c 100%)" }}
              details={<IndexLegend />} items={<span><i className="sw nd" />Нет подтверждённых данных</span>} />
            {timeDock(false)}
            <MapControls items={[["in", "plus", "Приблизить"], ["out", "minus", "Отдалить"], ["home", "globe", moscowView ? "Центр Москвы" : "Показать Россию"],
              ...(moscowView ? [["all_points", "locate-fixed", "Все точки"]] : [])]} onAction={(kind) => setZoom((z) => ({ kind, id: z.id + 1 }))} />
          </MapOverlay>
          <AtlasMobileSheet sheetRef={mobileSheet} title={requestTitle} description={selection ? `${feature?.name || "Выбранная точка"}${headlineScore ? ` · ${measureLabel(headlineScore.name)} ${measureText(headlineScore)}` : ""}` : result ? `${pointOverview?.inventoryCount ?? ""} точек · выберите точку на карте` : "Для выбранных условий результата нет"}
            tag={savedDemoModel ? <span className="badge-s">ДЕМО</span> : undefined} timeDock={timeDock(true)}
            onFilters={() => { setFiltersOpen(true); setChatOpen(false); }} onChat={() => { setChatOpen(true); setFiltersOpen(false); }}>
            {mobile && result && <details className="more"><summary>Результат и рассчитанные точки</summary>{!cardHidden ? renderResultCard() : <button className="iconbtn" onClick={() => setCardHidden(false)}>Показать результат</button>}</details>}
          </AtlasMobileSheet>
        </MapViewport>
        <AgentChatView open={chatOpen} onClose={() => { setChatOpen(false); if (window.innerWidth < 1520) setFiltersCollapsed(false); }} onNewDialog={() => { setInterfaceMessages([]); session.clearExplanation(); }}
          subtitle={result ? "Объясняет по сохранённому результату" : "Помогает выбрать условия до расчёта"}
          error={session.explanationError ? <p role="alert">{errorText(session.explanationError)}</p> : undefined}
          contextLabel={result ? `${requestTitle} · ${dirty ? "прежние условия" : "сохранённый результат"}` : "Помощь по интерфейсу · расчёта ещё нет"}
          contextRows={[{ label: "Контекст", value: result ? requestTitle : "Результат ещё не получен" },
            { label: "Условия", value: result ? timestampText(result.request.departure_at, savedOrigin?.timezone) : "Текущие параметры фильтров" },
            { label: "Результат", value: dirty ? "Для предыдущих условий" : result ? statusText[result.status] : "Не получен" }]}
          messages={<>{!result && interfaceMessages.map((message, index) => <div key={`help-${index}`}>
            <div className="msg u">{message.question}</div>
            <div className="msg a"><small>Помощь по интерфейсу · локально</small><p>{message.text}</p>
              <p>Эта справка не запускает расчёт. Объяснение показателей и проверка сценария доступны после получения результата.</p>
            </div>
          </div>)}{session.messages.map((m, i) => (
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
                <p>{proposalShiftText(p.fixed_parameters.shift_seconds)}</p>
                <p>
                  {p.feasibility === "unverified"
                    ? "Модельный эксперимент, реализуемость не подтверждена"
                    : "Реализуемость подтверждена"}
                </p>
                {p.assumptions.filter((text) => !/^(scenario-document:|scenario-plan:)/i.test(text)).map((t, i) => (
                  <p key={i}>{noticeText(t)}</p>
                ))}
                {p.assumptions.some((text) => /^(scenario-document:|scenario-plan:)/i.test(text)) && <details className="more"><summary>Источники сценария</summary>
                  {p.assumptions.filter((text) => /^(scenario-document:|scenario-plan:)/i.test(text)).map((text, index) => <p key={index}>{text}</p>)}
                </details>}
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
</>} hasMessages={(!result && interfaceMessages.length > 0) || session.messages.length > 0 || session.chatBusy || session.proposals.length > 0}
          suggestions={result ? ["Объясни выбранную точку и ограничения результата", "Какая часть пути занимает больше всего времени?", "Какие изменения можно проверить?"] : ["Как запустить расчёт?", "Какие условия сейчас доступны?", "Когда появится объяснение результата?"]}
          onSuggestion={(value) => { setQuestion(value); chatInput.current?.focus(); }} draft={question} onDraftChange={setQuestion}
          onSend={sendQuestion} disabled={false} busy={session.chatBusy}
          inputRef={chatInput} maxLength={2000} notice={mobile && demoModel ? demoModelWarning : undefined} note={result ? "Числа берутся только из сохранённого расчёта. Сценарий запускается отдельно по кнопке." : "До расчёта доступна локальная помощь по интерфейсу. Вопросы к модели доступны после получения результата."} />
    </AtlasShell>
  );
}
