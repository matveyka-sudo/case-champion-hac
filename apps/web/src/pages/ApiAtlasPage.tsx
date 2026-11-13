import { indexColor, indexGradient } from "../api/adapters/mapIndexColor";
import { CompactResultSummary } from "../widgets/CompactResultSummary";
import { PassengerSchedule } from "../widgets/PassengerSchedule";
import { compactAvailability, compactSlots } from "../api/adapters/compactScopes";
import { timeLabel } from "../shared/format";
import {
  useCallback,
  useEffect,
  useEffectEvent,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
} from "react";
import { EvidenceSources as Sources } from "../widgets/EvidenceSources";
import { backend as defaultBackend, BackendGateway } from "../api/gateway";
import { ApiClient, ApiError } from "../api/client";
import type {
  AnalysisKind,
  AnalysisRequest,
  AnalysisResult,
  Schemas,
  Territory,
  Selection,
} from "../api/types";
import {
  initialDraft,
  toRequest,
  toPresetRequest,
  presetDraft,
  DraftError,
  categoryIds,
  categoryLabels,
  destinationCategoryLabels,
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
  profileLabel,
  subjectMatches,
  timestampText,
  statusText,
  modeText,
  trafficText,
  selectionText,
  reasonText,
  noticeTexts,
  lastReturnsForSelection,
  scoreBand,
} from "../api/adapters/result";
import { atlasFilterHelp } from "../api/adapters/filterHelp";
import { useAnalysisSession } from "../features/analysis/useAnalysisSession";
import { atlasLocation, parseSavedPointer, savedPointerHash, savedPointerWriter, subscribeAtlasLocation, writeSavedPointer, type SavedPointer } from "../features/analysis/savedPointer";
import { savedRequestDraft } from "../api/adapters/savedRequest";
import { scenarioJourneys } from "../api/adapters/scenario";
import { scopeContainsTime, urbanScopeAvailability, type UrbanSelection } from "../api/adapters/urbanScopes";
import { journeyLines, transitLegend } from "../api/adapters/journeyGeometry";
import { urbanReadiness } from "../api/adapters/urbanReadiness";
import { UrbanReadiness } from "../widgets/UrbanReadiness";
import { CalculatedJourneys } from "../widgets/CalculatedJourneys";
import { DemoModelBanner } from "../widgets/DemoModelBanner";
import { demoModelWarning, isDemoModel, demoModelSelection, demoModelMarker } from "../api/adapters/demoModel";
import { proposalShiftText } from "../api/adapters/result";
import { ResultHeadline } from "../widgets/ResultHeadline";
import { ResultCardFrame, ResultCardHeader, ResultFigure, ResultCategoryTable } from "../widgets/ResultCardView";
import { pointScorePresentation } from "../api/adapters/scorePresentation";
import { CargoHandlingChanges } from "../widgets/CargoHandlingChanges";
import { ScenarioComparison } from "../widgets/ScenarioComparison";
import { Select } from "../shared/Select";
import { Icon } from "../shared/ui";
import { accessAssessment, accessCategoryStyle, accessNumber } from "../shared/accessibilityPresentation";
import { AddressFilters, AddressResultCard, DistrictAccess, DistrictResultCard, CategoryMapLegend } from "../widgets/AddressAccess";
import { addressModeLabels, urbanMovement } from "../widgets/urbanMovement";
import { useAddressAccess, labels as addressCategoryLabels } from "../features/useAddressAccess";
import { ApiMap } from "../widgets/ApiMap";
import { ExplanationFinding } from "../widgets/ExplanationFinding";
import { JourneyDetails } from "../widgets/JourneyDetails";
import { IndexLegend } from "../widgets/IndexLegend";
import { CalculatedPoints } from "../widgets/CalculatedPoints";
import { calculatedPoints, routeEstimatePoints, pointDisplayName, sampledPointNames } from "../api/adapters/pointOverview";
import { useModalFocus } from "../features/accessibility";
import { AtlasShell } from "../widgets/AtlasShell";
import { AtlasHeader } from "../widgets/AtlasHeader";
import { TimeDockView } from "../widgets/TimeDockView";
import { Seg } from "../widgets/SegmentedControl";
import { FiltersPanelView } from "../widgets/FiltersPanelView";
import { CategoryChoices } from "../widgets/CategoryChoices";
import { compactCargoOptions, matchingCargoProfiles } from "../api/adapters/cargoBinding";
import { ChoiceChips, TerritoryPair, CargoProfileChoices } from "../widgets/FilterControlViews";
import { boundSnapshotId, clinicPreset, initialSourceDraft, staticPilotPreset } from "../api/adapters/sourceBinding";
import { AnalysisGroupPanel } from "../widgets/AnalysisGroupPanel";
import { AgentChatView } from "../widgets/AgentChatView";
import { evidenceResolver, explanationSources, journeyEvidenceReferences } from "../api/adapters/evidenceGroups";
import { explanationLabel } from "../api/adapters/explanationStatus";
import { jobProgressText } from "../api/adapters/jobProgress";
import { MapViewport, MapCanvas, MapOverlay, MapControls, AtlasLegend, AtlasMobileSheet, AtlasStatus, StateCard } from "../widgets/AtlasMapView";
const passengerModeChoices = [
  ["air", "Самолёт", "plane"],
  ["rail", "Поезд", "train-front"],
  ["bus", "Автобус", "bus-front"],
  ["car", "Автомобиль", "car-front"],
  ["sea", "Судно и паром", "ship"],
];
const kaliningradSurfaceModes = ["rail", "bus", "car"];
const kaliningradSurfaceNote = "Для Калининграда доступны самолёт и водный маршрут. Поезд, автобус и автомобиль отключены для этого направления, поскольку наземные маршруты требуют международного транзита.";

function isKaliningradPair({ originId, destinationId }: ApiDraft) {
  return Boolean(originId && destinationId && originId !== destinationId &&
    [originId, destinationId].some((id) => id === "kaliningrad" || id === "region-ru-kgd"));
}

const standardCargo = { group: "nonfood", handling: "container", limits: {} as Record<string, boolean> };
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
  const note = reasonText(value.reason_code);
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
const isAddressLink = (hash: string) => hash === "#atlas/address" || hash === "#address-access" || hash === "#atlas/districts";
const viewportSubscribe = (cb: () => void) => {
  window.addEventListener("resize", cb);
  return () => window.removeEventListener("resize", cb);
};
const districtTransitDate = "2026-11-13";
const districtTransitSlot = 8 * 4 + 3;
const districtBackend = new BackendGateway(new ApiClient(fetch, "/cao-api/v1"));
export function DistrictAtlasPage() {
  return <ApiAtlasPage caoDistrict />;
}
export function ApiAtlasPage({ caoDistrict = false }: { caoDistrict?: boolean } = {}) {
  const backend = caoDistrict ? districtBackend : defaultBackend;
  const [caps, setCaps] = useState<Schemas["Capabilities"] | null>(null),
    [catalog, setCatalog] = useState<Territory[]>([]),
    [bootError, setBootError] = useState<Error | null>(null),
    [reload, setReload] = useState(0),
    [draft, setDraft] = useState(initialDraft),
    [automaticDate, setAutomaticDate] = useState(""),
    [selectedPresetId, setSelectedPresetId] = useState(""),
    [restoreAttempt, setRestoreAttempt] = useState(0),
    [restoreMode, setRestoreMode] = useState(false),
    [savedWarning, setSavedWarning] = useState(""),
    [localError, setLocalError] = useState<DraftError | null>(null),
    [search, setSearch] = useState(""),
    [searchError, setSearchError] = useState(""),
    [selection, setSelection] = useState<Selection | null>(null),
    [chatOpen, setChatOpen] = useState(window.innerWidth >= 1600),
    [filtersOpen, setFiltersOpen] = useState(false),
    [filtersCollapsed, setFiltersCollapsed] = useState(false),
    [cardHidden, setCardHidden] = useState(false),
    [question, setQuestion] = useState(""),
    [lastQuestion, setLastQuestion] = useState(""),
    [interfaceMessages, setInterfaceMessages] = useState<{ question: string; text: string }[]>([]),
    [scenarioView, setScenarioView] = useState(false),
    [theme, setTheme] = useState<"auto" | "light" | "dark">("auto"),
    [legendOpen, setLegendOpen] = useState(false),
    [mapMetric, setMapMetric] = useState("overall"),
    [cargoProfile, setCargoProfile] = useState(standardCargo),
    [cargoLayers, setCargoLayers] = useState<Record<string, boolean>>({ ent: true, wh: true, port: true, term: true, rail: true }),
    [layers, setLayers] = useState({ index: true, objects: true, pop: false }),
    [zoom, setZoom] = useState({ kind: "home", id: 0 });
  const session = useAnalysisSession(backend);
  const selectedPreset = caps?.analysis_presets?.find((preset) => preset.preset_id === selectedPresetId);
  const draftTouched = useRef(false);
  const hash = useSyncExternalStore(subscribeAtlasLocation, atlasLocation, () => "");
  const handledPointer = useRef<string | null>(null);
  const [addressSelected, setAddressSelected] = useState(false);
  const [lifeMessages, setLifeMessages] = useState<{question: string; text: string; generated?: boolean}[]>([]);
  const [lifeChatBusy, setLifeChatBusy] = useState(false);
  const [addressCardHidden, setAddressCardHidden] = useState(false);
  const [districtReports, setDistrictReports] = useState<Schemas["DistrictResult"][]>([]);
  const [districtMetric, setDistrictMetric] = useState("overall");
  const [districtReport, setDistrictReport] = useState<Schemas["DistrictResult"] | null>(null);
  const access = useAddressAccess();
  const [lifeTab, setLifeTab] = useState<"address" | "district">(() => hash === "#atlas/districts" ? "district" : "address");
  const addressMode = draft.kind === "urban_passenger" && (isAddressLink(hash) || addressSelected);
  const { setPicking } = access;
  let groupId: string | null = null;
  if (hash.startsWith("#atlas?")) {
    try {
      const pointer = parseSavedPointer(hash);
      if (pointer?.kind === "group") groupId = pointer.id;
    } catch { /* handled by saved result validation */ }
  }
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
  }, [addressMode]);
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
          setDraft((d) => {
            if (draftTouched.current) return d;
            return initialSourceDraft({ ...d, traffic: value.default_traffic_mode }, [...cities, ...regions], value);
          });
        }
      })
      .catch((e) => {
        if (alive)
          setBootError(e instanceof Error ? e : new Error("API недоступен"));
      });
    return () => {
      alive = false;
    };
  }, [backend, reload]);
  const requestedConnection = useRef(caoDistrict ? "" : new URLSearchParams(window.location.search).get("connections"));
  useEffect(() => {
    if (!caps || !requestedConnection.current) return;
    const mode = requestedConnection.current;
    requestedConnection.current = "";
    if (mode === "pax" || mode === "cargo") {
      setDraft((d) => ({ ...d, kind: mode === "pax" ? "intercity_passenger" : "urban_cargo" }));
    }
  }, [caps]);
  const restoreSaved = useEffectEvent(async () => {
    if (!caps || handledPointer.current === hash || isAddressLink(hash)) return;
    const previous = handledPointer.current;
    handledPointer.current = hash;
    setSelectedPresetId("");
    if (groupId) {
      session.reset();
      setRestoreMode(false);
      return;
    }
    if (hash === "#atlas" || !hash) {
      if (previous !== null) {
        session.reset();
        draftTouched.current = false;
        setDraft(initialSourceDraft(initialDraft(), catalog, caps));
      }
      setRestoreMode(false);
      return;
    }
    draftTouched.current = true;
    setRestoreMode(true);
    setLocalError(null);
    setSavedWarning("");
    setSelection(null);
    setScenarioView(false);
    setQuestion("");
    setInterfaceMessages([]);
    setCardHidden(false);
    const snapshot = await session.restore(hash, () => window.location.hash === hash);
    if (!snapshot || handledPointer.current !== hash || window.location.hash !== hash) return;
    const value = snapshot.result;
    const restored = savedRequestDraft(value, catalog, caps);
    setAutomaticDate("");
    const restoredCargo = restored.cargoProfile;
    const removedCargoLimits = !!(restoredCargo?.limits.temp || restoredCargo?.limits.perish);
    const paxModes = restored.draft.paxModes.filter((mode) => mode !== "mixed");
    setDraft({
      ...restored.draft,
      paxModes,
      profileId: removedCargoLimits ? "" : restored.draft.profileId,
    });
    if ("cargo_profile_id" in value.request) {
      setCargoProfile(restoredCargo
        ? { ...restoredCargo, limits: { ...restoredCargo.limits, temp: false, perish: false } }
        : { group: "", handling: "", limits: {} });
    }
    setSavedWarning(restored.warning || (removedCargoLimits || paxModes.length !== restored.draft.paxModes.length
      ? "В сохранённом результате есть условия, которых больше нет в фильтрах. Перед новым расчётом проверьте параметры."
      : ""));
    setRestoreMode(false);
    setScenarioView(!!snapshot.scenario);
    const pointer: SavedPointer = snapshot.scenario ? { kind: "scenario", id: snapshot.scenario.run_id } : { kind: "analysis", id: value.analysis_id };
    handledPointer.current = savedPointerHash(pointer);
    writeSavedPointer(pointer, true);
  });
  useEffect(() => {
    const timer = setTimeout(() => { void restoreSaved(); }, 0);
    return () => clearTimeout(timer);
  }, [hash, caps, catalog, restoreAttempt]);
  const retrySaved = () => { handledPointer.current = null; setRestoreAttempt((value) => value + 1); };
  const retryBootstrap = () => { setBootError(null); setReload((value) => value + 1); };
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
  }, [backend, caps, search, level]);
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
        if (addressMode) setPicking(false);
      }
    };
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  }, [addressMode, setPicking]);
  const change = (patch: Partial<ApiDraft>) => {
    setDistrictReport(null);
    if (patch.date !== undefined || patch.kind !== undefined) setAutomaticDate("");
    const passengerFiltersChanged = ["originId", "destinationId", "paxModes", "level"].some((key) => key in patch);
    if (caps?.compact_scopes && passengerFiltersChanged && patch.date === undefined) {
      const next = { ...draft, ...patch };
      if (next.kind === "intercity_passenger" && next.originId && next.destinationId &&
          next.originId !== next.destinationId && next.paxModes.length === 1) {
        const { dates } = compactAvailability(next, catalog, caps.compact_scopes, "");
        if (dates.length && !dates.includes(next.date)) {
          const date = dates.find((available) => available >= next.date) ?? dates[dates.length - 1];
          patch = { ...patch, date };
          setAutomaticDate(date);
        }
      }
    }
    setSelectedPresetId("");
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
    setSavedWarning("");
    setRestoreMode(false);
    setSelection(null);
    setScenarioView(false);
    setQuestion("");
    setInterfaceMessages([]);
    setDraft((d) => {
      const next = {
        ...d,
        ...(cityChanged ? { areaId: "", snapshotId: "", ...(caps?.compact_scopes ? {} : { travel: "" as const }) } : {}),
        ...patch,
        ...(["kind", "level", "cityId", "originId", "destinationId", "date", "slot", "travel", "cats", "traffic", "areaId"]
          .some((key) => key in patch && patch[key as keyof ApiDraft] !== d[key as keyof ApiDraft]) && !("snapshotId" in patch)
          ? { snapshotId: "" }
          : {}),
      };
      if (caps?.compact_scopes && next.kind === "intercity_passenger" && isKaliningradPair(next)) {
        next.paxModes = next.paxModes.filter((mode) => !kaliningradSurfaceModes.includes(mode));
      }
      return next;
    });
  };
  const selectCargoHandling = (profile: typeof cargoProfile, nextDraft: ApiDraft) => {
    if (!caps || !["kaliningrad", "moscow"].includes(nextDraft.cityId) || profile.group !== "raw") return profile;
    const options = compactCargoOptions(nextDraft, catalog, caps, profile);
    if (!options?.length || options.some(({ handling }) => handling === profile.handling)) return profile;
    const availableNow = options.find(({ times }) =>
      times.some(({ time, onSelectedDate }) => onSelectedDate && time === timeLabel(nextDraft.slot)));
    return { ...profile, handling: (availableNow ?? options[0]).handling };
  };
  const choose = useCallback((value: Selection) => {
    setSelection(value);
    setCardHidden(false);
  }, []);
  const chooseCalculatedPoint = useCallback((value: Selection) => {
    choose(value);
    setZoom((z) => ({ kind: "selection", id: z.id + 1 }));
    requestAnimationFrame(() => {
      if (resultCard.current) resultCard.current.scrollTop = 0;
      const body = resultCard.current?.querySelector<HTMLElement>(".ctx-b");
      if (body) body.scrollTop = 0;
      resultTitleRef.current?.focus({ preventScroll: true });
    });
  }, [choose]);
  const start = () => {
    if (!caps || unavailableReason || phase === "running") return;
    try {
      let request: AnalysisRequest;
      if (selectedPresetId) {
        request = toPresetRequest(selectedPresetId, caps);
      } else {
        if (draft.kind === "urban_passenger" && draft.cityId && caps.urban_passenger_scopes != null &&
          !caps.urban_passenger_scopes.some((scope) => scope.city_id === draft.cityId)) {
          setLocalError(new DraftError(
            "cityId",
            caps.urban_passenger_scopes.length
              ? "Для выбранного города нет зарегистрированного расчёта."
              : "Для городского расчёта нет зарегистрированных наборов параметров.",
          ));
          return;
        }
        const profileId = cargo ? chosenCargoProfile : draft.profileId;
        if (cargo && !draft.pipeline && !profileId) {
          setLocalError(new DraftError(
            "profileId",
            "Для выбранного профиля груза нет однозначно подтверждённых данных. Выбранные параметры сохранены.",
          ));
          return;
        }
        const requestDraft = caoDistrict && draft.travel === "pt"
          ? { ...draft, date: districtTransitDate, slot: districtTransitSlot } : draft;
        request = toRequest({ ...requestDraft, profileId, snapshotId: compact?.snapshotId || boundSnapshotId(requestDraft, catalog, caps) }, catalog, caps);
      }
      setLocalError(null);
      setDistrictReport(null);
      setRestoreMode(false);
      setFiltersOpen(false);
      setScenarioView(false);
      setInterfaceMessages([]);
      void session.run(request, savedPointerWriter((hash) => { handledPointer.current = hash; }));
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
      .sort((a, b) => urban && caps ? caps.urban_city_names.indexOf(a.name) - caps.urban_city_names.indexOf(b.name) : 0)
      .map((t) => {
        const reason = caps?.compact_scopes || publishedScopes == null ? undefined
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
  const walkPreset = !caps?.compact_scopes && caps && origin && draft.kind === "urban_passenger" ? clinicPreset(draft, origin, catalog, caps, "walk") : null;
  const transitPreset = !caps?.compact_scopes && caps && origin && draft.kind === "urban_passenger" ? clinicPreset(draft, origin, catalog, caps, "pt") : null;
  const standardProfiles = draft.kind === "urban_cargo" && caps ? matchingCargoProfiles(caps.cargo_profiles, standardCargo) : [];
  const roadPreset = !caps?.compact_scopes && caps && (draft.kind === "intercity_passenger" || standardProfiles.length === 1) ? staticPilotPreset(draft, catalog, caps) : null;
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
  const terminalRouteScheme = result?.limitations.some((item) => item === "WATER_WITH_GROUND_ACCESS" || item === "STATIC_ROAD_ACCESS_TO_TERMINALS") &&
    visibleJourneys.some((journey) => journey.geometry_kind === "schematic");
  const hasRouteGeometry = visibleJourneys.some((j) => journeyLines(j).length > 0);
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
  const availableCargo = caps ? compactCargoOptions(draft, catalog, caps, cargoProfile) : null;
  const matchingProfiles = cargo && caps ? matchingCargoProfiles(caps.cargo_profiles, cargoProfile) : [];
  const cityProfiles = draft.kind === "urban_cargo" && caps?.compact_scopes
    ? matchingProfiles.filter((profile) => caps.compact_scopes?.some((scope) =>
        scope.analysis_kind === "urban_cargo" && scope.city_id === draft.cityId &&
        scope.direction === (draft.direction === "out" ? "outbound" : "inbound") &&
        scope.cargo_profile_id === profile.profile_id))
    : matchingProfiles;
  const compactScopes = caps?.compact_scopes;
  const deliveryScopes = draft.kind === "intercity_cargo" && compactScopes
    ? compactScopes.filter((scope) => scope.analysis_kind === draft.kind &&
      scope.territory_level === draft.level && scope.origin_id === draft.originId &&
      scope.destination_id === draft.destinationId && scope.cargo_transport === "multimodal" &&
      draft.cargoModes.length > 0 && draft.cargoModes.every((mode) => scope.modes?.includes(mode)))
    : null;
  const deliveryProfiles = deliveryScopes
    ? matchingProfiles.filter((profile) => deliveryScopes.some((scope) => scope.cargo_profile_id === profile.profile_id))
    : matchingProfiles;
  const chosenCargoProfile = draft.kind === "urban_cargo"
    ? cityProfiles.find((profile) => profile.profile_id === draft.profileId)?.profile_id ??
      (cityProfiles.length === 1 ? cityProfiles[0].profile_id : "")
    : draft.pipeline ? draft.profileId :
      deliveryProfiles.find((profile) => profile.profile_id === draft.profileId)?.profile_id ??
      (deliveryProfiles.length === 1 ? deliveryProfiles[0].profile_id : "");
  const kaliningradPassenger = !!compactScopes && draft.kind === "intercity_passenger" && isKaliningradPair(draft);
  const compact = compactScopes ? compactAvailability(draft, catalog, compactScopes, chosenCargoProfile) : null;
  const showSchedule = draft.kind === "intercity_passenger" && draft.level === "city" &&
    draft.paxModes.some((mode) => mode !== "car") &&
    !!compactScopes?.some((scope) => scope.analysis_kind === "intercity_passenger" &&
      scope.origin_id === draft.originId && scope.destination_id === draft.destinationId);
  const [scheduleState, setScheduleState] = useState<{
    pair: string; value: Schemas["PassengerScheduleObservation"];
  } | null>(null);
  const schedulePair = showSchedule ? draft.originId + ":" + draft.destinationId : "";
  useEffect(() => {
    if (!showSchedule) return;
    const controller = new AbortController();
    let expiryTimer: ReturnType<typeof setTimeout> | undefined;
    void backend.passengerSchedules(draft.originId, draft.destinationId, controller.signal).then(([value]) => {
      if (controller.signal.aborted) return;
      setScheduleState(value ? { pair: schedulePair, value } : null);
      if (value) {
        expiryTimer = setTimeout(() => setScheduleState(null),
          Math.max(0, Date.parse(value.expires_at) - Date.now()));
      }
    }).catch(() => {
      if (!controller.signal.aborted) setScheduleState(null);
    });
    return () => {
      controller.abort();
      clearTimeout(expiryTimer);
    };
  }, [backend, showSchedule, draft.originId, draft.destinationId, schedulePair]);
  const schedule = scheduleState?.pair === schedulePair ? scheduleState.value : null;
  const calculationDates = compact?.dates;
  const suggestedDate = !compact?.snapshotId && compact?.dates.length
    ? compact.dates.find((date) => date >= draft.date) ?? compact.dates.at(-1)
    : undefined;
  const dateSuggestion = suggestedDate && <button className="iconbtn" type="button" onClick={() => change({ date: suggestedDate })}>
    Выбрать {suggestedDate.split("-").reverse().join(".")}
  </button>;
  let scheduleDeparture: string | null = null;
  if (schedule && origin) {
    try { scheduleDeparture = departureAt(draft.date, draft.slot, origin.timezone); }
    catch { /* No valid local date and time yet. */ }
  }
  const compatibleCargoSlots = cargo && chosenCargoProfile && compactScopes && !compact?.snapshotId
    ? compactSlots.filter(({ slot }) =>
        compactAvailability({ ...draft, slot }, catalog, compactScopes, chosenCargoProfile).dates.includes(draft.date))
    : [];
  const cargoExample = cargo ? compactScopes?.find((scope) =>
    scope.analysis_kind === draft.kind && (draft.kind === "urban_cargo" ? scope.city_id === draft.cityId :
      scope.origin_id === draft.originId && scope.destination_id === draft.destinationId) &&
    scope.cargo_profile_id === chosenCargoProfile &&
    (!compact?.snapshotId || scope.snapshot_id === compact.snapshotId) && scope.cargo_example)?.cargo_example : undefined;
  const resultRequest = result?.request;
  const resultCargoExample = resultRequest && "cargo_profile_id" in resultRequest ? compactScopes?.find((scope) =>
    scope.analysis_kind === resultRequest.analysis_kind && scope.snapshot_id === resultRequest.snapshot_id &&
    scope.cargo_profile_id === resultRequest.cargo_profile_id)?.cargo_example : undefined;
  let compactUnavailable = "Нет данных для выбранных условий.";
  if (compact && draft.kind === "intercity_passenger") {
    if (!draft.originId || !draft.destinationId || draft.originId === draft.destinationId) {
      compactUnavailable = "Выберите разные пункты отправления и прибытия.";
    } else if (kaliningradPassenger && draft.paxModes.some((mode) => kaliningradSurfaceModes.includes(mode))) {
      compactUnavailable = kaliningradSurfaceNote;
    } else if (!draft.paxModes.length) {
      compactUnavailable = "Выберите вид транспорта.";
    } else if (!compact.hasMatchingTerritory) {
      compactUnavailable = "Для этого направления ещё не подготовлены маршруты для расчёта.";
    } else if (!compact.hasMatchingConditions) {
      compactUnavailable = draft.paxModes.length > 1
        ? "Для этого сочетания нет общего набора для расчёта. Выберите один вид транспорта, чтобы проверить его отдельно."
        : "Для этого вида транспорта пока нет расчётного набора маршрутов и эталонного времени.";
    } else if (!compact.snapshotId) {
      compactUnavailable = "Для выбранных даты и времени нет подходящего набора данных. Проверьте доступные даты в календаре.";
    }
  }
  if (compact && cargo) {
    if (deliveryScopes?.length && !chosenCargoProfile) {
      compactUnavailable = "Для этой пары доступна модель автодоставки генерального груза. Выберите «Непрод.» или «Продовольствие» и обработку «Генеральный».";
    } else if (!compact.hasMatchingTerritory && draft.kind === "urban_cargo" && draft.cityId === "moscow") {
      compactUnavailable = "Нет данных: для Москвы ещё не подтверждены грузовые въезды и доступ площадок к совместимому терминалу.";
    } else if (!compact.hasMatchingTerritory) {
      compactUnavailable = urban
        ? "Нет данных: для этого города не подготовлены грузовые площадки и узлы для расчёта."
        : "Нет данных: для этого направления не подготовлена полная грузовая цепочка.";
    } else if (draft.kind === "urban_cargo" && cityProfiles.length > 1 && !chosenCargoProfile) {
      compactUnavailable = "Выберите конкретный профиль груза.";
    } else if (urban && !chosenCargoProfile && availableCargo?.length) {
      compactUnavailable = "Для выбранной обработки нет расчёта. Выберите доступный вариант в разделе «Обработка».";
    } else if (urban && !chosenCargoProfile && cargoProfile.handling === "liquid") {
      compactUnavailable = "Нет данных: нужны совместимые точки налива и слива и проверенный маршрут для автоцистерны.";
    } else if (urban && !chosenCargoProfile && cargoProfile.handling === "bulk") {
      compactUnavailable = "Нет данных: нужен подтверждённый вид навалочного груза и допуск подходящего грузовика на обоих концах.";
    } else if (urban && !chosenCargoProfile) {
      compactUnavailable = "Нет данных: не подтверждены операции с выбранным грузом на площадке и в грузовом узле.";
    } else if (!compact.hasMatchingConditions) {
      compactUnavailable = urban
        ? "Нет данных для выбранного груза и направления в этом городе."
        : "Нет данных для этого сочетания груза, направления и транспорта.";
    } else if (draft.kind === "urban_cargo" && !compact.dates.length) {
      const scopes = caps?.compact_scopes ?? [];
      const availableSlots = compactSlots.filter(({ slot }) =>
        compactAvailability({ ...draft, slot }, catalog, scopes, chosenCargoProfile).dates.length > 0);
      const labels = availableSlots.map(({ label }) => `«${label}»`).join(", ");
      if (labels) compactUnavailable = `Для этого груза выберите время: ${labels}.`;
    } else if (compact.dates.length && !compact.dates.includes(draft.date)) {
      compactUnavailable = "Нет данных на выбранную дату. Выберите доступную дату в календаре.";
    }
  }
  const blockedPassengerMode = kaliningradPassenger && draft.paxModes.some((mode) => kaliningradSurfaceModes.includes(mode));
  const unavailableReason = compact && (!compact.snapshotId || blockedPassengerMode) ? compactUnavailable :
    !selectedPreset && ready?.calculation_available === false
    ? ready.reason_code === "RUNTIME_INPUTS_UNAVAILABLE"
      ? "На этом стенде нет входных данных для расчёта выбранного направления."
      : reasonText(ready.reason_code) || "Сервер пока не поддерживает расчёт этого направления."
    : null;
  const runDisabled = !caps || running || unavailableReason !== null;
  const executionExpired = uiError instanceof ApiError && uiError.message === "Approved execution validity expired";
  const snapshotExpired = uiError?.message === "Unknown immutable snapshot";
  const refreshInputs = () => {
    session.changed();
    setLocalError(null);
    setSelectedPresetId("");
    setCaps(null);
    retryBootstrap();
  };
  const unavailableRequest = uiError instanceof ApiError && uiError.message === "RUNTIME_INPUTS_UNAVAILABLE_FOR_REQUEST";
  const progressText = jobProgressText(session.job, phase);
  const runningTitle = restoreMode && !session.job ? "Открываю сохранённый расчёт"
    : session.job?.status === "queued" ? "В очереди"
    : session.job?.status === "succeeded" ? "Открываю результат"
    : session.job && "progress" in session.job && session.job.progress?.phase === "finalizing" ? "Сохраняем результат"
    : session.job ? "Вычисляется" : "Отправляем запрос";
  const runLabel = urban || compact ? "Рассчитать" : "Проанализировать";
  const draftInstructions = !cargo
    ? urban ? "Выберите город, передвижение и категории" : "Выберите территории и виды транспорта"
    : urban ? "Выберите город, направление и профиль груза" : "Выберите территории и параметры грузовой связи";
  const requestTitle = result ? resultTitle(result, catalog) : origin?.name || "Россия";
  const toggleMode = (kind: AnalysisKind) => {
    const cityCargo = kind === "urban_cargo";
    if (cityCargo && cargoProfile.limits.nonurgent) {
      setCargoProfile({ ...cargoProfile, limits: { ...cargoProfile.limits, nonurgent: false } });
    }
    change({
      kind,
      cityId: "",
      originId: "",
      destinationId: "",
      areaId: "",
      profileId: "",
      pipeline: false,
      ...((cityCargo || kind === "intercity_passenger") && (!caps || compact) &&
        !compactSlots.some(({ slot }) => slot === draft.slot)
        ? { slot: compactSlots[0].slot }
        : {}),
      ...(compact ? {
        level: kind === "intercity_cargo" ? "region" : "city",
        paxModes: ["car"],
        cargoModes: ["road"],
        travel: "walk",
      } : {}),
      ...(kind === "intercity_passenger" && !caps ? { level: "city", paxModes: ["car"] } : {}),
    });
  };
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
  const compactResult = result?.limitations.includes("BOUNDED_ANALYSIS_SCOPE") ?? false;
  const sampleResult = result?.limitations.includes("BOUNDED_URBAN_SAMPLE") ?? false;
  const pointNames = useMemo(() => sampledPointNames(result), [result]);
  const headlineTime = selection ? headlineMinimums.length === 1 ? headlineMinimums[0] : headlineObservedTimes.length === 1 ? headlineObservedTimes[0] : null : null;
  const territoryHeadline = !selection && pointOverview?.territoryScore?.status === "available" && pointOverview.territoryScore.value !== null ? pointOverview.territoryScore : null;
  const headlineDestination = selection?.entity_kind === "sample_point" && selectedJourney.length === 1
    ? result?.map_data?.features?.find((item) => item.entity_id === (selectedJourney[0].destination_object_id || selectedJourney[0].destination_node_id))?.name?.trim() || undefined
    : undefined;
  const categoryRows = result?.request.analysis_kind === "urban_passenger" && selection ? result.request.categories.map((category) => {
    const candidates = shownScores.filter((score) => score.subject.kind === "sample_point" && score.subject.category === category);
    const exact = candidates.filter((score) => score.name === "category_index");
    const presentation = pointScorePresentation(result, candidates, exact.length === 1 ? exact[0] : undefined, category);
    const minimum = shownMetrics.filter((metric) => metric.subject.category === category && metric.name === "minimum_journey_seconds" && metric.status === "available");
    const observed = shownMetrics.filter((metric) => metric.subject.category === category && ["best_observed_time", "best_observed_journey_seconds"].includes(metric.name) && metric.status === "available");
    const time = minimum.length === 1 ? minimum[0] : observed.length === 1 ? observed[0] : null;
    const priority = result.request.analysis_kind === "urban_passenger" ? result.request.priorities[category] : undefined;
    return { id: category, label: categoryLabels[category] + (presentation.observed ? " · предварительно" : ""), priority: !compactResult && priority ? ({ low: "Н", medium: "С", high: "В" } as const)[priority] : undefined,
      time: time ? measureText(time) : "Нет данных", value: presentation.score ? measureText(presentation.score) : "Нет данных", score: presentation.score?.value ?? null };
  }) : [];
  const snapshotId = selectedPreset?.request.snapshot_id || compact?.snapshotId || (caps ? boundSnapshotId(draft, catalog, caps) : "");
  const savedDemoModel = isDemoModel(result);
  const selectedDemoLimitations = selectedPreset?.limitations || (demoModelSelection(snapshotId, caps?.analysis_presets) ? [demoModelMarker] : []);
  const demoModel = isDemoModel({ limitations: selectedDemoLimitations }) || savedDemoModel;
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
  const requestErrorMessage = snapshotExpired ? "Набор данных на сервере обновился. Обновите данные страницы, затем нажмите «Рассчитать»." : executionExpired ? "Срок действия расчётного профиля истёк. На стенде нужно обновить данные." : unavailableRequest
    ? draft.kind === "urban_passenger" && scopeState.matching.length === 1
      ? "Сервер указал эти условия как доступные, но не смог запустить расчёт. Выбор сохранён."
      : "Для выбранных условий на стенде нет доступного набора расчёта. Выбор сохранён."
    : uiError ? errorText(uiError) : null;
  const agentResult = result && !dirty && !running ? result : null;
  const destination = catalog.find((item) => item.territory_id === draft.destinationId);
  const draftTerritory = [origin?.name, !urban && destination?.name].filter(Boolean).join(" → ");
  const filterIssue = unavailableReason || (unavailableRequest && requestErrorMessage) || (draft.kind === "urban_passenger" ? scopeState.problem?.message : null);
  const changeTravel = (travel: ApiDraft["travel"]) => {
    if (caoDistrict && travel === "pt") {
      change({ travel, date: districtTransitDate, slot: districtTransitSlot });
      return;
    }
    change({ travel });
  };
  const sendQuestion = () => {
    const text = question.trim();
    if (!text || text.length > 2000 || session.chatBusy) return;
    if (agentResult) {
      setLastQuestion(text);
      void session.explain(text, selection);
    } else {
      const guidance = atlasFilterHelp({ caps, selection: scopeSelection, territory: draftTerritory, date: draft.date,
        kind: draft.kind, issue: filterIssue || null, question: text });
      setInterfaceMessages((messages) => [...messages, { question: text, text: guidance }]);
    }
    setQuestion("");
  };
  const readiness = caps && draft.kind === "urban_passenger" ? urbanReadiness(draft, catalog, caps, result, dirty) : null;
  const categoryReason = (category: Schemas["UrbanPassengerRequest"]["categories"][number]) => {
    if (compact) {
      const scopes = caps?.compact_scopes?.filter((scope) => scope.city_id === draft.cityId &&
        scope.analysis_kind === "urban_passenger" && !!draft.travel && scope.modes?.includes(passengerModes[draft.travel]));
      if (scopes?.length && !scopes.some((scope) => scope.categories?.includes(category)))
        return "Для выбранного передвижения нет данных по категории.";
      return undefined;
    }
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
  const selectionTitle = selection?.entity_kind === "sample_point"
    ? pointNames.get(selection.entity_id) || pointOverview?.points.find((point) => point.pointId === selection.entity_id)?.name || pointDisplayName(selection.entity_id, feature)
    : feature?.name || (compactResult && selection?.entity_kind === "destination" && feature?.categories?.length
      ? feature.categories.map((category) => destinationCategoryLabels[category]).join(" · ")
      : selection ? selectionText[selection.entity_kind] : requestTitle);
  const selectedSources = () => {
    const context = scenarioView && scenario ? scenario : result;
    if (!context) return [];
    function* references() {
      for (const metric of shownMetrics) yield* metric.source_refs;
      for (const score of shownScores) yield* score.evidence_refs;
      if (scenarioView && scenario) {
        yield* scenario.turnaround_evidence;
        yield* scenario.resource_evidence || [];
        for (const journey of scenario.after_journeys || [])
          if (selectedJourney.some((selected) => selected.journey_id === journey.journey_id)) yield* journeyEvidenceReferences(journey);
      } else if (result) {
        if (feature) yield* feature.source_refs;
        for (const journey of selectedJourney) {
          yield* journeyEvidenceReferences(journey);
          const reference = result.references.find((item) => item.reference_id === journey.reference_id);
          if (reference) yield* reference.calibration_evidence;
        }
        for (const count of result.counts) if (shownMetrics.some((metric) => metric.metric_id === count.metric_id)) yield* count.evidence_refs;
        for (const lastReturn of shownReturns) yield* lastReturn.evidence_refs;
      }
    }
    return evidenceResolver(context).sources(references());
  };
  const districtMovement = result?.request.analysis_kind === "urban_passenger" && result.request.passenger_mode === "public_transport"
    ? "Общественный транспорт" : "Пешком";
  const renderDistrictGridCard = () => {
    if (!result) return null;
    const value = headlineScore?.value ?? null;
    const assessment = accessAssessment(value);
    const pointSelected = selection?.entity_kind === "sample_point" || selection?.entity_kind === "cell";
    return <ResultCardFrame id="apiResultCard" label="Результат по району" sectionRef={resultCard}>
      <ResultCardHeader label={districtMovement} title={selection ? selectionTitle : "Центральный административный округ"}
        onClose={() => setCardHidden(true)} titleRef={resultTitleRef} />
      <div className="ctx-b life-result-content">
        {dirty && <p className="fhint">Показан результат для предыдущих условий.</p>}
        <section className="api-result-summary" style={{ "--result-color": indexColor(value) } as React.CSSProperties}>
          <div className="api-summary-label">{pointSelected ? "Доступность из выбранной точки" : "Доступность в районе"}</div>
          {value != null ? <>
            <div className="api-summary-value tnum" style={{ color: assessment.color }}><strong>{accessNumber.format(value)}</strong><span>из 100</span></div>
            <p className="district-assessment" style={{ color: assessment.color }}>{assessment.text}</p>
          </> : <p>{selection?.entity_kind === "destination" ? "Выберите ячейку, чтобы увидеть время пути до объектов." : pointSelected ? assessment.text : "Выберите ячейку на карте, чтобы посмотреть её оценку и маршруты."}</p>}
          <button className="iconbtn result-ask-agent" disabled={dirty} onClick={() => {
            setChatOpen(true);
            if (window.innerWidth < 1520) setFiltersCollapsed(true);
          }}><Icon name="sparkles" />Спросить агента<Icon name="arrow-up-right" /></button>
        </section>
        {pointSelected && <ul className="api-address-results">{categoryRows.map((row) => {
          const metric = shownMetrics.find((item) => item.subject.category === row.id && item.journey_id && item.unit === "seconds");
          const journey = selectedJourney.find((item) => item.journey_id === metric?.journey_id);
          const destination = result.map_data?.features?.find((item) => item.entity_kind === "destination" && item.entity_id === journey?.destination_object_id);
          const { icon, color } = accessCategoryStyle(row.id);
          const walk = journey?.legs.every((leg) => leg.kind === "walk");
          const alternative = journey?.journey_id.endsWith("-transit") ? result.metrics.find((item) =>
            item.metric_id === journey.journey_id.replace(/-transit$/, walk ? "-pt-option" : "-walk")) : undefined;
          return <li key={row.id}>
            <strong className="district-category-label"><span className="category-card-icon" style={{ "--category-color": color } as React.CSSProperties}><Icon name={icon} /></span>{categoryLabels[row.id]}</strong>
            {destination?.name && <span>{destination.name}</span>}
            <span>{journey?.total_seconds != null ? `${accessNumber.format(journey.total_seconds / 60)} мин ${walk ? "пешком" : "на ОТ"}` : "Маршрут не найден"}
              {row.score != null && ` · индекс ${accessNumber.format(row.score)}`}</span>
            {alternative?.value != null && <small>{walk ? "Вариант на ОТ" : "Пеший вариант"}{alternative.subject.destination_id !== journey?.destination_object_id ? " до другого объекта категории" : ""} · {accessNumber.format(alternative.value / 60)} мин</small>}
          </li>;
        })}</ul>}
        <details className="more"><summary>Источники и условия</summary>
          {districtMovement === "Общественный транспорт" && <p>Отправление 13 ноября 2026, 08:45 МСК.</p>}
          {noticeTexts(result.limitations).map((text) => <p key={text}>{text}</p>)}
        </details>
      </div>
    </ResultCardFrame>;
  };
  const renderResultCard = () => caoDistrict ? renderDistrictGridCard() : result && <ResultCardFrame id="apiResultCard" sectionRef={resultCard} compact={chatOpen && !filtersCollapsed && window.innerWidth < 1600} label={compactResult ? "Результат расчёта" : undefined}>
              <ResultCardHeader label={savedDemoModel ? scenarioView ? "Демонстрационный сценарий" : "Демонстрационный анализ" : scenarioView ? "Сценарий" : compactResult ? selection?.entity_kind === "destination" ? "Объект назначения" : selection ? requestTitle : "Результат расчёта" : "Сохранённый анализ"}
                title={selectionTitle} onClose={() => setCardHidden(true)} titleRef={resultTitleRef} />
              {compactResult ? <div className="ctx-b">
                <CompactResultSummary result={result} selection={selection}
                  originTimezone={resultRequest?.analysis_kind === "intercity_passenger"
                    ? catalog.find((entry) => entry.territory_id === resultRequest.origin_id)?.timezone
                    : undefined}
                  score={selection?.entity_kind === "sample_point" ? headlineScore : territoryHeadline}
                  time={headlineTime} metrics={shownMetrics} scores={shownScores} cargoExample={resultCargoExample} />
                {selection?.entity_kind === "sample_point" && categoryRows.length > 1 && <div className="sect">
                  <ResultCategoryTable rows={categoryRows} columns={["Объекты", "Время в пути", "Индекс"]} />
                </div>}
                {selection?.entity_kind === "sample_point" && shownReturns.length > 0 && <details className="more api-compact-returns">
                  <summary>Последний обратный рейс</summary>
                  {shownReturns.map((r) => {
                    const destination = result.map_data?.features?.find((f) => f.entity_kind === "destination" && f.entity_id === r.destination_object_id);
                    const name = destination?.name || destination?.categories?.map((category) => destinationCategoryLabels[category]).join(" · ");
                    return <div key={r.destination_object_id || r.point_id}>
                      {name && shownReturns.length > 1 && <b>{name}</b>}
                      {r.status === "available" ? <dl>
                        <div><dt>Выйти от объекта</dt><dd>{timestampText(r.leave_at, r.timezone, false)}</dd></div>
                        <div><dt>Вернуться к точке</dt><dd>{timestampText(r.arrival_at, r.timezone, false)}</dd></div>
                      </dl> : <p>Нет данных о последнем рейсе.</p>}
                    </div>;
                  })}
                </details>}
                {!selection && pointOverview && <CalculatedPoints overview={pointOverview} sample={sampleResult} showTerritory={false} selection={null} areaSelected={false} onSelect={chooseCalculatedPoint} />}
                {selection && <button className="iconbtn api-result-back" onClick={() => setSelection(null)}>К общему результату</button>}
                {result.limitations.includes("BOUNDED_KUDIKINA_ROUTE_DATE_SUBSET") && <p className="fhint">
                  Расписание · <a href="https://kudikina.ru/" target="_blank" rel="noopener noreferrer">Кудыкина</a>
                </p>}
                <Sources refs={result.evidence_refs} label="Источники" resolveSources={selection && result.evidence_groups?.length ? selectedSources : undefined} />
              </div> : <div className="ctx-b">
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
                  : territoryHeadline}
                  metric={headlineTime}
                  destination={headlineDestination}
                  journey={selection && selectedJourney.length === 1 ? selectedJourney[0] : null} />
                {scenario && <ScenarioComparison scenario={scenario} metrics={shownMetrics} scores={shownScores} selectionActive={!!selection} scenarioView={scenarioView} onViewChange={setScenarioView} />}
                {categoryRows.length > 0 && <div className="sect"><h4>Категории и приоритеты</h4><ResultCategoryTable rows={categoryRows} overall={headlinePointScores.length === 1 ? measureText(headlinePointScores[0]) : "Нет данных"} /></div>}
                {selection && <div className="kvs tnum"><ResultFigure title="Население точки" value={feature?.population == null ? "Неизвестно" : feature.population.toLocaleString("ru")} />
                  <ResultFigure title="Индекс города" value={pointOverview?.territoryScore ? measureText(pointOverview.territoryScore) : "Нет данных"} /></div>}
                {pointOverview && <CalculatedPoints overview={pointOverview} showTerritory={!selection && !territoryHeadline} selection={selection} areaSelected={!!("area_id" in result.request && result.request.area_id)} onSelect={chooseCalculatedPoint} />}
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
                {feature?.population !== undefined &&
                  (feature.entity_kind === "cell" ||
                    feature.entity_kind === "sample_point" || feature.cell_id) && (
                    <div className="api-population">
                      Население ячейки:{" "}
                      {feature.population === null
                        ? "неизвестно"
                        : feature.population.toLocaleString("ru", { maximumFractionDigits: 1 })}{" "}
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
                    comparison={(() => {
                      if (!result.analysis_id.startsWith("cao-transit-500m")) return undefined;
                      const walk = shownMetrics.find((m) => m.metric_id === journey.journey_id.replace(/-transit$/, "-walk"));
                      const transit = shownMetrics.find((m) => m.metric_id === journey.journey_id.replace(/-transit$/, "-pt-option"));
                      const transfers = shownMetrics.find((m) => m.metric_id === journey.journey_id.replace(/-transit$/, "-transfers"));
                      if (walk?.value == null || transit?.value == null || transfers?.value == null) return undefined;
                      return { walk: walk.value, transit: transit.value, transfers: transfers.value,
                        sameDestination: transit.subject.destination_id === journey.destination_object_id };
                    })()}
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
                {noticeTexts(result.limitations).map((text, i) => (
                  <p className="fhint" key={i}>{text}</p>
                ))}
                <Sources refs={scenarioView && scenario ? scenario.evidence_refs : result.evidence_refs}
                  resolveSources={selection && (scenarioView && scenario ? scenario.evidence_groups?.length : result.evidence_groups?.length) ? selectedSources : undefined} />
                <div className="ctx-actions"><button className="iconbtn" onClick={() => { setChatOpen(true); if (window.innerWidth < 1520) setFiltersCollapsed(true); }}><Icon name="sparkles" />Спросить агента</button></div>
              </div>}
            </ResultCardFrame>;
  const moscowView = (result ? result.request.analysis_kind === "urban_passenger" && result.request.city_id === "moscow" : urban && draft.cityId === "moscow");
  const timeDock = (mobileDock: boolean) => caoDistrict || selectedPreset ? null : <TimeDockView date={draft.date} slot={draft.slot} timezoneLabel={origin?.timezone || "Выберите территорию"}
    compact={!!compact} dates={calculationDates} min={calculationDates?.[0] || "1900-01-01"} max={calculationDates?.at(-1) || "2100-12-31"} runLabel={compact ? runLabel : undefined} dateInputId={mobileDock ? "apiMobileDate" : "apiDate"} timeInputId={mobileDock ? "apiMobileTime" : "apiTime"} mobile={mobileDock}
    onDateChange={(date) => change({ date })} onSlotChange={(slot) => change({ slot })} run={start} disabled={runDisabled} highlighted={dirty} />;
  if (groupId) return <AnalysisGroupPanel key={groupId} id={groupId} />;
  const setAddressMode = (enabled: boolean) => {
    setAddressSelected(enabled);
    access.setPicking(false);
    if (enabled) {
      setChatOpen(false);
      setFiltersCollapsed(false);
      if (mobile) setFiltersOpen(true);
    }
    if (!enabled && isAddressLink(hash)) {
      const next = session.job && ["queued", "running"].includes(session.job.status)
        ? savedPointerHash({ kind: "job", id: session.job.job_id })
        : result ? savedPointerHash({ kind: "analysis", id: result.analysis_id }) : "#atlas";
      handledPointer.current = next;
      window.history.replaceState(null, "", next);
      window.dispatchEvent(new Event("waydex:navigate"));
    }
  };
  const calculateAddress = () => {
    if (!access.canRun) return;
    setAddressCardHidden(false);
    setFiltersOpen(false);
    void access.calculate();
  };
  const openLifeAgent = () => { setChatOpen(true); setFiltersOpen(false); if (window.innerWidth < 1520) setFiltersCollapsed(true); };
  const addressCard = access.result && <AddressResultCard access={access} result={access.result} stale={access.stale}
    onAsk={openLifeAgent} onClose={() => setAddressCardHidden(true)} />;
  const activeDistrict = addressMode && lifeTab === "district" ? districtReport : null;
  const districtCard = activeDistrict && <DistrictResultCard onAsk={openLifeAgent} metric={districtMetric} result={activeDistrict} onClose={() => setDistrictReport(null)} />;
  const sendLifeQuestion = async () => {
    const text = question.trim();
    if (!text || lifeChatBusy) return;
    setQuestion("");
    setLifeChatBusy(true);
    try {
      const response = await fetch("/api/v1/address-access/explanation", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question: text, city: "Москва",
          ...(activeDistrict ? {district: {...activeDistrict, boundary: null, sample_points: [], destinations: []}}
            : lifeTab === "address" && access.result && !access.stale ? {
              address: {...access.result, categories: access.result.categories.map(item => ({...item, path: []}))}
            } : {}) }),
      });
      if (!response.ok) throw new Error("Объяснение временно недоступно.");
      const answer = await response.json();
      setLifeMessages(old => [...old, {question: text, text: answer.text, generated: answer.status === "generated"}]);
    } catch {
      setLifeMessages(old => [...old, {question: text, text: "Объяснение временно недоступно. Повторите вопрос."}]);
    } finally {
      setLifeChatBusy(false);
    }
  };
  const addressScope = (addressMode || caoDistrict) && <div className="fgroup">
    <p className="flabel">Для жизни</p>
    <Seg name="Область расчёта" value={caoDistrict ? "district" : lifeTab} items={[["address", "Адрес или точка"], ["district", "Район"]]}
      onChange={(value) => {
        if (caoDistrict && value === "address") window.location.assign("/map#atlas/address");
        else if (!caoDistrict && value === "district") { if (lifeTab !== "district") window.location.assign("/district"); }
        else if (!caoDistrict) { access.setPicking(false); setLifeTab("address"); }
      }} />
  </div>;


  return (
    <AtlasShell className={demoModel ? "has-demo-model" : ""} label="Атлас Waydex — API"
      header={<AtlasHeader life={addressMode || caoDistrict} onLife={() => { if (caoDistrict) { window.location.assign("/map#atlas/address"); return; } if (draft.kind !== "urban_passenger") toggleMode("urban_passenger"); setAddressMode(true); window.location.hash = "atlas/address"; }} mode={draft.kind === "urban_passenger" ? "city" : draft.kind === "intercity_passenger" ? "pax" : "cargo"}
        title={addressMode ? "Москва и Московская область" : origin?.name || "Россия · выберите город"} theme={theme} onThemeChange={setTheme}
        onModeChange={(mode) => {
          if (caoDistrict) {
            window.location.assign("/map?connections=" + mode + "#atlas");
            return;
          }
          setAddressMode(false);
          if (mode === "city" && draft.kind === "urban_passenger") return;
          toggleMode(mode === "city" ? "urban_passenger" : mode === "pax" ? "intercity_passenger" : draft.kind.endsWith("cargo") ? draft.kind : "urban_cargo");
        }}
        onFilters={() => { setFiltersOpen(true); setChatOpen(false); }} chatOpen={chatOpen} showChat={true}
        onChat={() => { const open = !chatOpen; setChatOpen(open); setFiltersOpen(false); if (window.innerWidth < 1520) setFiltersCollapsed(open); }} />} notice={!addressMode && <DemoModelBanner selectedLimitations={selectedDemoLimitations} savedLimitations={result?.limitations} hasSavedResult={!!result} bannerRef={demoBanner} />}>
        <FiltersPanelView id="apiFilters" collapsed={filtersCollapsed} mobileOpen={filtersOpen} mobile={mobile} notice={!addressMode && mobile && demoModel ? demoModelWarning : undefined}
          onClose={() => setFiltersOpen(false)} onExpand={() => setFiltersCollapsed(false)}
          onToggle={() => { setFiltersCollapsed((value) => !value); if (filtersCollapsed && window.innerWidth < 1360) setChatOpen(false); }}
          railIcons={!cargo && urban ? ["map-pin", draft.travel === "pt" ? "tram-front" : draft.travel === "walk" ? "footprints" : "car-front", "list-checks"] : ["sliders-horizontal"]}
          footer={addressMode && lifeTab === "district" ? <small>Выберите район или муниципалитет, чтобы увидеть его индекс.</small> : addressMode ? <button className="iconbtn primary runbtn" disabled={!access.canRun} onClick={calculateAddress}><Icon name="play" />Рассчитать</button> : <><button className="iconbtn primary runbtn" disabled={runDisabled} onClick={start}><Icon name="play" /><span>{runLabel}</span></button>
            {(!caoDistrict || unavailableReason) && <small>{unavailableReason || (caps ? "Данные проверяются для выбранных условий" : "Проверяем доступность расчёта")}</small>}
            {!caoDistrict && unavailableReason && dateSuggestion}</>}>
          {addressScope}
          {addressMode && lifeTab === "district" && <div className="fgroup"><a className="iconbtn" href="/district">Расчёт по сетке ЦАО</a></div>}
          {caoDistrict ? <>
            {field("Центральный административный округ", <>
              <p className="fhint">Выберите ячейку на карте, чтобы посмотреть доступность объектов.</p>
              <a className="iconbtn" href="/map#atlas/districts"><Icon name="map" />Сравнить районы</a>
            </>)}
            {field("Передвижение", <><Seg name="Передвижение" value={draft.travel || null}
              items={urbanMovement.filter(([mode]) => mode !== "car")}
              onChange={(travel) => changeTravel(travel as ApiDraft["travel"])} />
              {inline("travel", ["passenger_mode"])}
            </>)}
            {field("Категории", <>
              <CategoryChoices items={Object.entries(categoryIds)
                .filter(([, category]) => caps?.categories.includes(category))
                .map(([id, category]) => ({ id, name: categoryLabels[category], reason: categoryReason(category) }))}
                selected={draft.cats} onToggle={(id) => {
                  const cats = { ...draft.cats }, key = id as keyof typeof categoryIds;
                  if (cats[key]) delete cats[key]; else cats[key] = 2;
                  change({ cats });
                }} onPriority={(id, priority) => change({ cats: { ...draft.cats, [id]: priority } })} />
              {inline("cats", ["categories", "priorities"])}
            </>)}
            {field("Цвет карты", <Select label="Показатель, которым окрашена карта" value={mapMetric}
              options={[{ value: "overall", label: "Общий индекс" }, ...Object.keys(draft.cats).map((id) => ({ value: id, label: categoryLabels[categoryIds[id as keyof typeof categoryIds]] }))]}
              onChange={setMapMetric} />)}
          </> : addressMode && lifeTab === "district" ? <DistrictAccess results={districtReports} metric={districtMetric} onMetric={setDistrictMetric} onSelect={setDistrictReport} onRemove={(id) => { setDistrictReports((reports) => reports.filter((report) => report.district.id !== id)); if (districtReport?.district.id === id) setDistrictReport(null); }} onResult={(report) => { if (!report) return; setDistrictReports((reports) => [...reports.filter((item) => item.district.id !== report.district.id), report]); setDistrictReport(report); }} /> : addressMode ? <AddressFilters access={access} onPick={() => {
            if (!access.picking) { setFiltersOpen(false); setChatOpen(false); }
            access.setPicking(!access.picking);
          }} /> : <>

          {!caps && (
            <div className="stmsg">
              <span>
                {bootError
                  ? "API недоступен"
                  : "Загрузка возможностей сервера…"}
              </span>
              <button
                className="iconbtn"
                onClick={retryBootstrap}
              >
                Повторить
              </button>
            </div>
          )}
          {caps && (
            <>
              {!!caps.analysis_presets?.length && field("Готовые серверные условия", <>
                <Select label="Готовые серверные условия" value={selectedPresetId} options={[{ value: "", label: "Свои параметры" }, ...caps.analysis_presets.map((preset) => ({ value: preset.preset_id, label: preset.label }))]}
                  onChange={(id) => {
                    if (!id) { change({}); return; }
                    try {
                      const request = toPresetRequest(id, caps);
                      change(presetDraft(request, catalog));
                      setSelectedPresetId(id);
                    } catch (error) { setLocalError(error instanceof DraftError ? error : new DraftError("preset", "Не удалось выбрать набор условий")); }
                  }} />
                {inline("preset")}
              </>)}
              {selectedPreset && <div className="fgroup" aria-label="Выбранные серверные условия">
                <b>{selectedPreset.label}</b>
                <p>{timestampText(selectedPreset.request.departure_at, origin?.timezone)} · {trafficText[selectedPreset.request.traffic_mode]}</p>
                <p className="fhint">Условия заданы сервером и будут отправлены без изменений. Чтобы изменить их, выберите «Свои параметры».</p>
                {noticeTexts(selectedPreset.limitations || []).map((text) => <p className="fhint" key={text}>{text}</p>)}
              </div>}
              {!selectedPreset && <>
              {cargo && field("Режим грузовых связей", <Seg name="Режим грузовых связей" value={urban ? "city" : "inter"}
                items={[["city", "В городе", "building-2"], ["inter", "Между территориями", "waypoints"]]}
                onChange={(value) => toggleMode(value === "city" ? "urban_cargo" : "intercity_cargo")} />)}
              {!urban && field("Масштаб", <Seg name="Масштаб" singleLine value={draft.level} items={[["city", "Город — город"], ["region", "Субъект — субъект"]]}
                onChange={(value) => change({ level: value as ApiDraft["level"], originId: "", destinationId: "" })} />)}

              {urban ? (
                field(
                  "Город",
                  <>
                    <Seg name="Город" value={draft.cityId || null} items={catalogOptions("city").filter((option) => option.value).map((option) => [option.value, option.label, undefined, false, "reason" in option ? option.reason : undefined])}
                      onChange={(cityId) => {
                        if (draft.kind === "urban_cargo") {
                          const profile = selectCargoHandling(cargoProfile, { ...draft, cityId, areaId: "" });
                          if (profile !== cargoProfile) setCargoProfile(profile);
                          change({ cityId, areaId: "", profileId: "" });
                        } else change({ cityId, areaId: "" });
                      }} />
                    {inline("cityId", ["city_id"])}
                  </>,
                )
              ) : field("Направление", <><TerritoryPair origin={draft.originId} destination={draft.destinationId} options={catalogOptions(draft.level)}
                onChange={(side, value) => change(side === "A" ? { originId: value } : { destinationId: value })}
                onSwap={() => change({ originId: draft.destinationId, destinationId: draft.originId })} />
                {inline("originId", ["origin_id"])}{inline("destinationId", ["destination_id"])}</>)}
              {(walkPreset || transitPreset || roadPreset) && field("Готовые условия", <div className="api-preset-options api-quick-presets">
                {walkPreset && <button type="button" className="iconbtn" disabled={runDisabled} aria-label="Пешком до взрослых поликлиник" onClick={() => change(walkPreset)}>Пешком · поликлиники</button>}
                {transitPreset && <button type="button" className="iconbtn" disabled={runDisabled} aria-label="На общественном транспорте до поликлиник" onClick={() => change(transitPreset)}>ОТ · поликлиники</button>}
                {roadPreset && <button type="button" className="iconbtn" disabled={runDisabled} onClick={() => {
                  if (standardProfiles.length === 1) {
                    setCargoProfile(standardCargo);
                    change({ ...roadPreset, profileId: standardProfiles[0].profile_id });
                  } else change(roadPreset);
                }}>{cargo ? "Городской грузовой расчёт" : "Авто без пробок"}</button>}
                <p className="fhint">Подставляют условия. Запуск — по кнопке «{runLabel}».</p>
              </div>)}
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
                        items={urbanMovement}
                        onChange={(travel) => changeTravel(travel as ApiDraft["travel"])} />
                        {!draft.travel && <p className="fhint">Выберите способ передвижения для выбранного города.</p>}
                        {inline("travel", ["passenger_mode"])}</>,

                    )
                  : field(compact ? "Вид транспорта" : "Виды транспорта", <>
                    <ChoiceChips items={passengerModeChoices}
                      disabled={kaliningradPassenger ? kaliningradSurfaceModes : undefined}
                      descriptionId={kaliningradPassenger ? "kaliningrad-passenger-modes-note" : undefined}
                      values={Object.fromEntries(draft.paxModes.map((mode) => [mode, true]))}
                      onChange={(id) => change({ paxModes: compact ? [id] : draft.paxModes.includes(id)
                        ? draft.paxModes.filter((mode) => mode !== id)
                        : [...draft.paxModes, id] })} />
                    {kaliningradPassenger && <p className="fhint" id="kaliningrad-passenger-modes-note">{kaliningradSurfaceNote}</p>}
                    {automaticDate === draft.date && compact?.snapshotId && <p className="fhint" role="status">
                      Выбрана доступная дата {automaticDate.split("-").reverse().join(".")}. Её можно изменить в календаре.
                    </p>}
                    {inline("paxModes", ["passenger_modes"])}
                  </>))}
              {schedule && <PassengerSchedule schedule={schedule} selectedModes={draft.paxModes}
                day={draft.date} departure={scheduleDeparture} fromTimezone={origin?.timezone}
                toTimezone={catalog.find((item) => item.territory_id === draft.destinationId)?.timezone} />}
              {!cargo &&
                urban &&
                field("Категории", <>
                  <CategoryChoices items={Object.entries(categoryIds).map(([id, category]) => ({ id, name: categoryLabels[category], reason: categoryReason(category) }))}
                    selected={draft.cats} onToggle={(id) => { const cats = { ...draft.cats }, key = id as keyof typeof categoryIds; if (cats[key]) delete cats[key]; else cats[key] = 2; change({ cats }); }}
                    onPriority={(id, priority) => change({ cats: { ...draft.cats, [id]: priority } })} />
                  <div className="prio-legend"><span>Н — низкий</span><span>С — средний</span><span>В — высокий</span></div>
                  {inline("cats", ["categories", "priorities"])}
                </>)}
              {filterIssue && <div className="fgroup api-filter-availability">
                <p className="fhint">{filterIssue}</p>
              </div>}
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
                <CargoProfileChoices showNonurgent={!!compact && !urban} value={cargoProfile} available={availableCargo} onChange={(choice, field) => {
                  const profile = field === "group" ? selectCargoHandling(choice, draft) : choice;
                  setCargoProfile(profile);
                  change({ profileId: "", pipeline: profile.handling === "liquid" && draft.pipeline });
                }} />
                {draft.kind === "urban_cargo" && cityProfiles.length > 1 && field("Профиль груза",
                  <Select label="Профиль груза" value={chosenCargoProfile}
                    options={[{ value: "", label: "Выберите профиль" }, ...cityProfiles.map((profile) => {
                      const example = caps.compact_scopes?.find((scope) =>
                        scope.analysis_kind === "urban_cargo" && scope.city_id === draft.cityId &&
                        scope.cargo_profile_id === profile.profile_id && scope.cargo_example)?.cargo_example;
                      return { value: profile.profile_id, label: example ? `${example} · ${profileLabel(profile)}` : profileLabel(profile) };
                    })]}
                    onChange={(profileId) => change({ profileId })} />)}
                {cargoExample && <p className="fhint">Пример груза: {cargoExample}</p>}
                {compatibleCargoSlots.map(({ slot }) => <button key={slot} type="button" className="iconbtn"
                  onClick={() => change({ slot })}>Выбрать {timeLabel(slot)}</button>)}
                {inline("profileId", ["cargo_profile_id"])}
                {!urban ? field("Продукт трубопровода", <><Seg name="Продукт трубопровода" value={draft.pipelineProduct || null} items={[["oil", "Нефть", undefined, cargoProfile.handling !== "liquid" || !draft.pipeline], ["oilprod", "Нефтепродукты", undefined, cargoProfile.handling !== "liquid" || !draft.pipeline], ["gas", "Газ", undefined, cargoProfile.handling !== "liquid" || !draft.pipeline]]}
                  onChange={(value) => { const product = value as Exclude<ApiDraft["pipelineProduct"], "">; const profiles = caps.cargo_profiles.filter((profile) => profile.kind === "pipeline" && profile.product === pipelineProducts[product]); change({ pipelineProduct: product, profileId: profiles.length === 1 ? profiles[0].profile_id : "" }); }} />
                  <p className="fhint">Трубопровод применим к наливному грузу. Нефть, нефтепродукты и газ считаются отдельно.</p></>) : <div className="fgroup"><div className="disabled-note"><Icon name="info" />Трубопроводы учитываются только между территориями.</div></div>}
              </>}
              {!compact && <>
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
              </>}
              {caps.traffic_modes.length > 1 && field(
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
              </>}
            </>
          )}
          </>}
        </FiltersPanelView>
        <MapViewport mapRef={mapArea} className={`${caoDistrict ? "life-district-map " : ""}${(addressMode ? activeDistrict || (lifeTab === "address" && access.result && !addressCardHidden) : activeDistrict || (result && !cardHidden)) && !mobile ? "has-card" : ""} ${!addressMode && !result && !activeDistrict ? "has-empty-state" : ""} ${chatOpen && !mobile ? "chat-open" : ""} ${!addressMode && dirty ? "dimmed" : ""}`}><MapCanvas>
          <ApiMap
            result={addressMode || activeDistrict ? null : result}
            addressMode={addressMode && lifeTab === "address"}
            lifeDistrict={caoDistrict}
            district={activeDistrict}
            districts={addressMode && lifeTab === "district" ? districtReports : undefined}
            districtMetric={districtMetric} onDistrictSelect={setDistrictReport}
            addressPlace={addressMode && lifeTab === "address" ? access.place : null}
            addressResult={addressMode && lifeTab === "address" && !access.stale ? access.result : null}
            onAddressPick={addressMode && lifeTab === "address" && access.picking ? access.choosePoint : undefined}
            scores={addressMode ? [] : values?.scores || []}
            selection={selection}
            onSelect={caoDistrict ? chooseCalculatedPoint : choose}
            zoomAction={zoom}
            scenarioView={!addressMode && scenarioView}
            journeys={addressMode || activeDistrict ? undefined : visibleJourneys}
            cityId={addressMode ? "moscow" : urban ? draft.cityId : undefined} viewId={scenario?.run_id}
            category={mapMetric === "overall" ? null : categoryIds[mapMetric as keyof typeof categoryIds]} layers={layers}
          /></MapCanvas>
          {addressMode && lifeTab === "district" ? <>
            <MapOverlay position="tl"><AtlasStatus kind={activeDistrict ? "ok" : "nd"} title={activeDistrict?.district.name || "Индексы районов и муниципалитетов"} subtitle="Пешком · точки — начала маршрутов, иконки — объекты назначения" /></MapOverlay>
            {activeDistrict && (mobile ? <div className="m-sheet api-address-sheet"><details className="more" open><summary>Результат по территории</summary>{districtCard}</details></div> : <MapOverlay position="r">{districtCard}</MapOverlay>)}
            <MapOverlay position="bottom"><CategoryMapLegend categoryIds={districtReports.flatMap((report) => (report.destinations || []).filter((item) => districtMetric === "overall" || item.category === districtMetric).map((item) => item.category))} /><AtlasLegend title="Индекс доступности" scale open={legendOpen} onToggle={() => setLegendOpen((value) => !value)} ticks={[0, 25, 50, 75, 100]} barStyle={{background: indexGradient}} note={<>Розовый — ниже · мятный — выше<br />Серый — недостаточно данных</>} details={<div className="district-legend-explanation"><p>Менее 40 — низкая доступность.<br />40–59 — средняя.<br />60–79 — хорошая.<br />80–100 — высокая.</p><p>Переходы цветов плавные. Полупрозрачная заливка соответствует выбранному показателю. Синий контур выделяет открытый район.</p></div>} /><MapControls items={[["in", "plus", "Приблизить"], ["out", "minus", "Отдалить"], ["home", "globe", "Центр Москвы"]]} onAction={(kind) => setZoom((value) => ({ kind, id: value.id + 1 }))} /></MapOverlay>
          </> : addressMode ? <>
          <MapOverlay position="tl"><AtlasStatus
              kind={access.error ? "err" : access.running ? "run" : access.stale ? "stale" : access.result ? "ok" : "nd"}
              title={access.picking ? "Нажмите на карту, чтобы выбрать место" : access.running ? "Считаем маршруты…" : access.error ? "Расчёт не выполнен" : access.result && !access.stale ? "Результат получен" : "Доступность по адресу или точке"}
              subtitle={`${access.place?.label || "Москва и Московская область"} · ${addressModeLabels[access.mode]}`}
              actions={access.picking && <button type="button" className="iconbtn" onClick={() => access.setPicking(false)}>Отменить выбор</button>} /></MapOverlay>
            {access.error && <StateCard role="alert" icon="cloud-off" title="Не удалось рассчитать доступность"
              description={access.error} actions={<button className="iconbtn" onClick={calculateAddress} disabled={!access.canRun}>Повторить</button>} />}
            {access.result && !mobile && (addressCardHidden
              ? <button className="iconbtn panelc api-reopen" onClick={() => setAddressCardHidden(false)}>Показать результат</button>
              : <MapOverlay position="r">{addressCard}</MapOverlay>)}
            <MapOverlay position="bottom">{access.result && !access.stale && <CategoryMapLegend categoryIds={access.result.categories.filter((item) => item.destination).map((item) => item.category)} />}<MapControls items={[["in", "plus", "Приблизить"], ["out", "minus", "Отдалить"], ["home", "globe", "Центр Москвы"]]}
              onAction={(kind) => setZoom((value) => ({ kind, id: value.id + 1 }))} /></MapOverlay>
            {mobile && <div className="m-sheet api-address-sheet" ref={mobileSheet}>
              {access.result && <details className="more"><summary>{access.stale ? "Предыдущий результат" : "Результат расчёта"}</summary>
                {addressCardHidden ? <button className="iconbtn" onClick={() => setAddressCardHidden(false)}>Показать результат</button> : addressCard}
              </details>}
              <div className="m-btns">
                <button className="iconbtn" onClick={() => { setFiltersOpen(true); setChatOpen(false); }}>Фильтры</button>
                <button className="iconbtn primary" disabled={!access.canRun} onClick={calculateAddress}>Рассчитать</button>
              </div>
            </div>}
          </> : <>
          <MapOverlay position="tl"><AtlasStatus kind={activeDistrict ? activeDistrict.index == null ? "stale" : "ok" : uiError ? "err" : running ? "run" : dirty || (!caoDistrict && !compactResult && result?.status === "partial") ? "stale" : result ? "ok" : "nd"}
            title={activeDistrict ? activeDistrict.district.name : running ? runningTitle : dirty ? "Параметры изменены — показан прежний результат" : phaseLabels[phase]}
            subtitle={<>{caoDistrict ? `ЦАО · ${districtMovement}` : activeDistrict ? "Синие точки — места расчёта, фиолетовые — цели маршрутов" : result ? `${requestTitle} · ${timestampText(result.request.departure_at, savedOrigin?.timezone, !compactResult)}${compactResult ? "" : ` · ${trafficText[result.traffic_mode]} · ${statusText[result.status]}`}` : restoreMode ? "Исходные параметры появятся после получения результата." : draftInstructions}
              {progressText && <><br />{progressText}</>}</>}
            actions={session.job && ["queued", "running"].includes(session.job.status) && <button className="iconbtn" onClick={() => void session.cancel()}>Отменить</button>} />
          {result?.analysis_id.startsWith("cao-transit-500m") &&
            <div aria-label="Цвета маршрутов" style={{background: "var(--surface, #fff)", borderRadius: 10,
              padding: "8px 12px", color: "#101a31", display: "flex", gap: 12, flexWrap: "wrap"}}>
              {transitLegend.map(({color, label}) => <span key={label} style={{whiteSpace: "nowrap"}}>
                <span aria-hidden style={{display: "inline-block", width: 15, borderTop: `3px solid ${color}`,
                  marginRight: 5, verticalAlign: "middle"}} />{label}
              </span>)}
            </div>
          }
          </MapOverlay>
          {uiError && <StateCard role="alert" icon="cloud-off" title={uiError instanceof ApiError && uiError.code === "stored_result_incompatible" ? "Сохранённый результат несовместим — требуется новый расчёт" : "Новый результат не получен"}
            description={requestErrorMessage} actions={<div className="api-preset-options">
              <button className="iconbtn primary" onClick={snapshotExpired ? refreshInputs : bootError ? retryBootstrap : restoreMode ? retrySaved : start}>{snapshotExpired ? "Обновить данные" : result && !restoreMode && uiError instanceof ApiError && uiError.code === "incompatible_versions" ? "Пересчитать базу" : "Повторить"}</button>
            </div>} />}
          {!result && !activeDistrict && !uiError && <StateCard icon={running ? "loader-circle" : "circle-dashed"} loading={running}
            title={running ? runningTitle : unavailableReason ? "Расчёт пока недоступен" : "Расчёт ещё не выполнялся"} description={running ? restoreMode && !session.job ? "Получаю результат с сервера без повторного запуска расчёта." : "Карту можно двигать — результат появится после завершения расчёта." : unavailableReason || `${draftInstructions}, затем нажмите «${runLabel}». До расчёта карта не окрашивается.`}
            actions={unavailableReason && !caoDistrict ? dateSuggestion : <button className="iconbtn primary" disabled={runDisabled} onClick={start}>{runLabel}</button>} />}
          {terminalRouteScheme && <div className="api-map-note">Пунктир показывает порядок остановок и терминалов. Точная траектория движения недоступна.</div>}
          {!caoDistrict && result && !terminalRouteScheme &&
            (!result.map_data ||
              result.map_data.status !== "available" ||
              (!compactResult && result.map_data.completeness === "partial") || segmentGeometry) && (
              <div className="api-map-note">
                {segmentGeometry ? "Геометрия маршрутов доступна частично." : hasRouteGeometry ? "Показана геометрия маршрута. Объекты территории доступны не полностью." : result.map_data?.status === "available"
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
          {!mobile && (districtCard || (result && !cardHidden)) && <MapOverlay position="r">{districtCard || renderResultCard()}</MapOverlay>}
          {result && cardHidden && (
            <button
              className="iconbtn panelc api-reopen"
              onClick={() => setCardHidden(false)}
            >
              Показать результат
            </button>
          )}
          <MapOverlay position="bottom">
            {caoDistrict && result?.request.analysis_kind === "urban_passenger" && <CategoryMapLegend
              categoryIds={result.request.categories.filter((category) => mapMetric === "overall" || category === categoryIds[mapMetric as keyof typeof categoryIds])}
              names={categoryLabels} note="Цвет пешего маршрута соответствует категории объекта." />}
            <AtlasLegend title={caoDistrict ? "Индекс доступности" : layers.pop ? "Население ячейки" : values?.scores.some((score) => score.name === "observed_route_accessibility" && score.status === "available") ? "Предварительная оценка по найденному маршруту" : "Индекс доступности"}
              scale={(!!compact && draft.kind === "urban_passenger") || !!values?.scores.some((score) => score.status === "available" && score.value != null) || layers.pop} open={legendOpen} onToggle={() => setLegendOpen((value) => !value)} ticks={layers.pop ? ["меньше жителей", "больше"] : [0, 25, 50, 75, 100]}
              note={caoDistrict ? "Розовый — ниже · мятный — выше" : layers.pop ? "Оценка населения по исходным ячейкам" : undefined}
              barStyle={{ background: layers.pop ? "linear-gradient(90deg, hsl(250 45% 90%), hsl(250 45% 35%))"
                : compact || caoDistrict ? indexGradient : "linear-gradient(to right,#c72d44 0%,#c72d44 50%,#d49520 50%,#d49520 75%,#0b8a5c 75%,#0b8a5c 100%)" }}
              details={layers.pop ? <p className="fhint">Серый цвет — данных о населении нет.</p> : <IndexLegend continuous={!!compact || caoDistrict} sample={sampleResult} />} items={<span><i className="sw nd" />Нет подтверждённых данных</span>} />
            {timeDock(false)}
            <MapControls items={[["in", "plus", "Приблизить"], ["out", "minus", "Отдалить"], ["home", "globe", moscowView && !sampleResult ? "Центр Москвы" : "Показать Россию"],
              ...(moscowView || sampleResult ? [["all_points", "locate-fixed", "Все точки"]] : [])]} onAction={(kind) => setZoom((z) => ({ kind, id: z.id + 1 }))} />
          </MapOverlay>
          <AtlasMobileSheet sheetRef={mobileSheet} title={caoDistrict ? "Центральный административный округ" : requestTitle} description={selection ? `${selectionTitle}${headlineScore ? ` · ${measureLabel(headlineScore.name)} ${measureText(headlineScore)}` : ""}` : result ? (territoryHeadline && sampleResult ? `Доступность в зоне · ${measureText(territoryHeadline)}` : compactResult ? "Откройте результат расчёта" : pointOverview?.inventoryCount != null && pointOverview.inventoryCount > 0 ? `${pointOverview.inventoryCount} точек · выберите точку на карте` : "Откройте показатели сохранённого результата") : "Для выбранных условий результата нет"}
            tag={savedDemoModel ? <span className="badge-s">ДЕМО</span> : undefined} timeDock={timeDock(true)}
            onFilters={() => { setFiltersOpen(true); setChatOpen(false); }} onChat={() => { setChatOpen(true); setFiltersOpen(false); }}>
            {mobile && districtCard && <details className="more"><summary>Индекс территории</summary>{districtCard}</details>}
            {mobile && !districtCard && result && <details className="more"><summary>{compactResult || caoDistrict ? "Результат расчёта" : "Результат и рассчитанные точки"}</summary>{!cardHidden ? renderResultCard() : <button className="iconbtn" onClick={() => setCardHidden(false)}>Показать результат</button>}</details>}
          </AtlasMobileSheet>
          </>}
        </MapViewport>
        {addressMode ? <AgentChatView open={chatOpen} onClose={() => { setChatOpen(false); setFiltersCollapsed(false); }} onNewDialog={() => { setQuestion(""); setLifeMessages([]); }}
          subtitle="Вопрос о выбранном результате"
          contextLabel={lifeTab === "district" ? activeDistrict?.district.name || "Выберите район" : access.result?.address.label || "Выберите адрес"}
          contextRows={lifeTab === "district" && activeDistrict ? [
            {label: "Территория", value: activeDistrict.district.name},
            {label: "Показатель", value: districtMetric === "overall" ? "Общий индекс" : addressCategoryLabels[districtMetric as keyof typeof addressCategoryLabels]},
            {label: "Индекс", value: String((districtMetric === "overall" ? activeDistrict.index : activeDistrict.categories[districtMetric]) ?? "Недостаточно данных")},
          ] : access.result ? [{label: "Место", value: access.result.address.label}, {label: "Передвижение", value: addressModeLabels[access.result.mode as keyof typeof addressModeLabels]}, {label: "Индекс", value: String(access.result.index ?? "Недостаточно данных")}] : []}
          messages={lifeMessages.map((message, index) => <div key={index}>
            <div className="msg u">{message.question}</div>
            <div className="msg a"><small>{message.generated ? "Объяснение модели" : "Шаблонное объяснение"}</small><p>{message.text}</p></div>
          </div>)}
          hasMessages={lifeMessages.length > 0 || lifeChatBusy} suggestions={["Какой общий индекс?", "Какие объекты доступны рядом?"]}
          onSuggestion={setQuestion} draft={question} onDraftChange={setQuestion}
          onSend={() => void sendLifeQuestion()} disabled={false} busy={lifeChatBusy} note="Ответ использует выбранный результат." /> :
        <AgentChatView open={!addressMode && chatOpen} onClose={() => { setChatOpen(false); if (window.innerWidth < 1520) setFiltersCollapsed(false); }} onNewDialog={() => { setInterfaceMessages([]); session.clearExplanation(); }}
          subtitle={agentResult ? "Задайте Алисе вопрос о результате" : "Для вопросов к Алисе сначала выполните расчёт"}
          error={session.explanationError ? <div role="alert">
            <p>{errorText(session.explanationError)}</p>
            {agentResult && lastQuestion && <button className="iconbtn" type="button" disabled={session.chatBusy}
              onClick={() => void session.explain(lastQuestion, selection)}>Повторить вопрос</button>}
          </div> : undefined}
          contextLabel={agentResult ? `${requestTitle} · сохранённый результат` : draftTerritory || "Текущие фильтры"}
          contextRows={[{ label: "Контекст", value: agentResult ? requestTitle : draftTerritory || "Территория не выбрана" },
            { label: "Условия", value: agentResult ? timestampText(agentResult.request.departure_at, savedOrigin?.timezone) : `${draft.date} · ${trafficText[draft.traffic]}` },
            { label: "Результат", value: agentResult ? statusText[agentResult.status] : running ? runningTitle : "Для выбранных условий ещё не получен" }]}
          messages={<>{!agentResult && interfaceMessages.map((message, index) => <div key={`help-${index}`}>
            <div className="msg u">{message.question}</div>
            <div className="msg a"><small>Помощь по интерфейсу · локально</small><p>{message.text}</p>
              <p>Эта справка не запускает расчёт. Объяснение показателей и проверка сценария доступны после получения результата.</p>
            </div>
          </div>)}{agentResult && session.messages.map((m, i) => (
              <div key={i}>
                <div className="msg u">{m.question}</div>
                <div className="msg a">
                  <small>
                    {explanationLabel(m.answer)}
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
                  {noticeTexts(m.answer.limitations).map((text, j) => (
                    <p key={j}>{text}</p>
                  ))}
                  <Sources refs={m.answer.retrieved_evidence || []} resolveSources={result ? () => explanationSources(result, m.answer) : undefined} />
                </div>
              </div>
            ))}
            {agentResult && session.chatBusy && <>
              <div className="msg u">{lastQuestion}</div>
              <p role="status">Получаю ответ…</p>
            </>}
            {savedWarning && <p role="alert">{savedWarning}</p>}
            {session.warning && <p role="alert">{session.warning}</p>}
            {agentResult && session.messages.length > 0 && !session.proposals.length && !session.warning && <p className="fhint">Для этого результата сервер не предложил сценарий.</p>}
            {agentResult && session.proposals.map((p) => (
              <div className="proposal" key={p.scenario_id}>
                <b>
                  {p.operation_type === "reduce_handling_wait" ? "Сократить обработку груза" : p.operation_type === "coordinate_transfer"
                    ? "Согласовать пересадку"
                    : "Дополнительное отправление"}
                </b>
                {p.operation_type === "reduce_handling_wait"
                  ? <CargoHandlingChanges changes={p.fixed_parameters.handling_changes || []} />
                  : <p>{proposalShiftText(p.fixed_parameters.shift_seconds)}</p>}
                <p>
                  {p.feasibility === "unverified"
                    ? "Модельный эксперимент, реализуемость не подтверждена"
                    : "Реализуемость подтверждена"}
                </p>
                {noticeTexts(p.assumptions.filter((text) => !/^(scenario-document:|scenario-plan:)/i.test(text))).map((text, i) => (
                  <p key={i}>{text}</p>
                ))}
                {p.assumptions.some((text) => /^(scenario-document:|scenario-plan:)/i.test(text)) && <details className="more"><summary>Источники сценария</summary>
                  {p.assumptions.filter((text) => /^(scenario-document:|scenario-plan:)/i.test(text)).map((text, index) => <p key={index}>{text}</p>)}
                </details>}
                {noticeTexts(p.limitations).map((text, i) => (
                  <p key={i}>{text}</p>
                ))}
                <button
                  className="iconbtn primary"
                  disabled={!p.editable_local_graph || dirty || running}
                  onClick={() => void session.runScenario(p, savedPointerWriter((hash) => { handledPointer.current = hash; }))}
                >
                  Проверить сценарий
                </button>
                {!p.editable_local_graph && (
                  <p>Нет изменяемого локального графа.</p>
                )}
              </div>
            ))}
</>} hasMessages={agentResult ? session.messages.length > 0 || session.chatBusy || session.proposals.length > 0 : interfaceMessages.length > 0}
          suggestions={agentResult ? ["Объясни выбранную точку и ограничения результата", "Какая часть пути занимает больше всего времени?", "Какие изменения можно проверить?"] : ["Как запустить расчёт?", "Какие условия сейчас доступны?", "Когда появится объяснение результата?"]}
          onSuggestion={(value) => { setQuestion(value); chatInput.current?.focus(); }} draft={question} onDraftChange={setQuestion}
          onSend={sendQuestion} disabled={false} busy={session.chatBusy}
          inputRef={chatInput} maxLength={2000} notice={mobile && demoModel ? demoModelWarning : undefined} note={agentResult ? "Числа берутся только из сохранённого расчёта. Сценарий запускается отдельно по кнопке." : "До расчёта доступна локальная помощь по интерфейсу. Вопросы к модели доступны после получения результата."} />}
    </AtlasShell>
  );
}
