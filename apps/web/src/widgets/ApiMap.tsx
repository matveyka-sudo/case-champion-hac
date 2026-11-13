import { useEffect, useMemo, useRef, useState } from "react";
import L from "leaflet";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { Icon } from "../shared/ui";
import { accessIcons, accessAssessment, accessNumber, accessCategoryColors } from "../shared/accessibilityPresentation";
import { feature } from "topojson-client";
import type { GeometryCollection, Topology } from "topojson-specification";
import type { GeoJsonObject } from "geojson";
import type { AnalysisResult, Schemas, Selection } from "../api/types";
import { indexColor } from "../api/adapters/mapIndexColor";
import { sampledPointNames } from "../api/adapters/pointOverview";
import { destinationCategoryLabels } from "../api/adapters/request";
import { selectionText } from "../api/adapters/result";
import { journeyLines, segmentGeometryNotice } from "../api/adapters/journeyGeometry";
import { displayedCategory, pointScorePresentation, observedScoreLabel } from "../api/adapters/scorePresentation";
import { labels as addressCategoryLabels, type AddressPlace } from "../features/useAddressAccess";
import type { Category } from "../api/types";
const moscowCamera = (result: AnalysisResult | null) => !result?.limitations.includes("BOUNDED_ANALYSIS_SCOPE") && (result?.request.analysis_kind === "urban_passenger" || result?.request.analysis_kind === "urban_cargo") && result.request.city_id === "moscow";

// Map camera anchors for the three pilot cities, independent of routing inputs.
const pilotCenters: Record<string, L.LatLngTuple> = {
  moscow: [55.75, 37.62],
  kaliningrad: [54.71, 20.51],
  novorossiysk: [44.72, 37.77],
};
const showMoscowCenter = (map: L.Map) => map.setView(pilotCenters.moscow, 10, { animate: false });
type FeatureLayer = { layer: L.GeoJSON; style: L.PathOptions; markers: L.CircleMarker[]; compact: boolean };
const featureKey = (kind: string, id: string) => `${kind}:${id}`;
const pointRadius = 7;
function selectLayer(item: FeatureLayer, selected: boolean) {
  if (!item.compact || !item.markers.length)
    item.layer.setStyle(selected ? { ...item.style, color: "#2f5bff", weight: 3 } : item.style);
  for (const marker of item.markers) {
    marker.setRadius(item.compact
      ? selected ? 11 : marker.options.className === "api-map-point" ? pointRadius : 5
      : selected ? 10 : 7);
    if (!item.compact) continue;
    marker.getElement()?.classList.toggle("api-map-point-selected", selected);
    marker.getElement()?.setAttribute("aria-pressed", String(selected));
  }
}
export function ApiMap({
  result,
  scores,
  selection,
  onSelect,
  zoomAction,
  scenarioView = false,
  journeys,
  viewId,
  cityId,
  category = null,
  addressMode = false,
  district,
  districts,
  districtMetric = "overall",
  onDistrictSelect,
  addressPlace,
  addressResult,
  onAddressPick,
  layers: visibleLayers = { index: true, objects: true, pop: false },
}: {
  result: AnalysisResult | null;
  scores: Schemas["Score"][];
  selection: Selection | null;
  onSelect: (s: Selection) => void;
  zoomAction: { id: number; kind: string };
  scenarioView?: boolean;
  journeys?: Schemas["JourneyResult"][];
  viewId?: string;
  cityId?: string;
  category?: Category | null;
  addressMode?: boolean;
  district?: Schemas["DistrictResult"] | null;
  districts?: Schemas["DistrictResult"][];
  districtMetric?: string;
  onDistrictSelect?: (district: Schemas["DistrictResult"]) => void;
  addressPlace?: AddressPlace | null;
  addressResult?: Schemas["AddressResult"] | null;
  onAddressPick?: (point: { lat: number; lon: number }) => void;
  layers?: { index: boolean; objects: boolean; pop: boolean };
}) {
  const host = useRef<HTMLDivElement>(null),
    map = useRef<L.Map | null>(null),
    layers = useRef<L.LayerGroup | null>(null),
    routeLayers = useRef<L.LayerGroup | null>(null),
    overviewLayers = useRef<L.LayerGroup | null>(null),
    pointRenderer = useRef<L.SVG | null>(null),
    sampleBounds = useRef<L.LatLngBounds | null>(null),
    featureBounds = useRef<L.LatLngBounds | null>(null),
    last = useRef(""),
    lastAnalysis = useRef(""),
    lastZoom = useRef(zoomAction.id),
    lastCity = useRef(""),
    featureLayers = useRef(new Map<string, FeatureLayer[]>()),
    selectedLayers = useRef<FeatureLayer[]>([]);
  const [tileError, setTileError] = useState(false);
  const selectedKind = selection?.entity_kind, selectedId = selection?.entity_id;
  const compactResult = result?.limitations.includes("BOUNDED_ANALYSIS_SCOPE") ?? false;
  const compactSample = result?.limitations.includes("BOUNDED_URBAN_SAMPLE") ?? false;
  const pointNames = useMemo(() => sampledPointNames(result), [result]);
  const activeCategory = result ? displayedCategory(result, category) : null;
  const inventory = useMemo(() => {
    const features = result?.map_data?.features || [];
    const children = new Map<string, string[]>(), byId = new Map<string, { feature: Schemas["MapFeature"]; index: number }>();
    const points = new Map<string, Schemas["MapFeature"]>(), bySubject = new Map<string, number[]>();
    const journeyCategories = new Map<string, Set<Category>>();
    let maxPopulation = 1;
    features.forEach((feature, index) => {
      if (!byId.has(feature.entity_id)) byId.set(feature.entity_id, { feature, index });
      if (feature.cell_id) {
        const ids = children.get(feature.cell_id) || [];
        ids.push(feature.entity_id);
        children.set(feature.cell_id, ids);
      }
      if (feature.entity_kind === "sample_point")
        for (const id of [feature.entity_id, feature.point_id]) if (id && !points.has(id)) points.set(id, feature);
      if (feature.population != null) maxPopulation = Math.max(maxPopulation, feature.population);
    });
    scores.forEach((score, index) => {
      const subject = score.subject;
      for (const id of new Set([subject.point_id, subject.site_id, subject.node_id, subject.territory_id, subject.destination_id, subject.origin_id])) {
        if (!id) continue;
        const indices = bySubject.get(id) || [];
        indices.push(index);
        bySubject.set(id, indices);
      }
    });
    for (const metric of result?.metrics || []) if (metric.journey_id && metric.subject.category) {
      const categories = journeyCategories.get(metric.journey_id) || new Set<Category>();
      categories.add(metric.subject.category);
      journeyCategories.set(metric.journey_id, categories);
    }
    const items = features.map((feature) => {
      const indices = new Set<number>();
      for (const id of [feature.entity_id, feature.point_id, ...(children.get(feature.entity_id) || [])])
        if (id) for (const index of bySubject.get(id) || []) indices.add(index);
      // Preserve the wire score order, including ambiguous-candidate behavior.
      const candidates = [...indices].sort((a, b) => a - b).map((index) => scores[index]);
      return { feature, candidates };
    });
    return { features, items, points, byId, children, journeyCategories, maxPopulation };
  }, [result, scores]);
  useEffect(() => {
    if (!host.current) return;
    const m = L.map(host.current, {
      minZoom: 1,
      maxZoom: 18,
      zoomControl: false,
      attributionControl: false,
      preferCanvas: true,
    }).fitBounds([
      [40, 19],
      [82, 190],
    ]);
    L.control.attribution({ position: "topright", prefix: false }).addTo(m);
    map.current = m;
    // Keep a neutral land outline visible when remote street tiles are slow or unavailable.
    const landRequest = new AbortController();
    const landPane = m.createPane("localLand");
    landPane.style.zIndex = "150";
    void fetch("/data/land-50m.json", { signal: landRequest.signal })
      .then(async (response) => {
        if (!response.ok) return;
        const topology = await response.json() as Topology<{ land: GeometryCollection }>;
        if (landRequest.signal.aborted) return;
        const landOptions: L.GeoJSONOptions & L.PathOptions = {
          pane: "localLand",
          renderer: L.svg({ pane: "localLand" }),
          interactive: false,
          className: "api-land-outline",
          weight: 1,
          fillOpacity: 1,
        };
        L.geoJSON(feature(topology, topology.objects.land), landOptions).addTo(m);
      })
      .catch(() => { /* Street tiles and result geometry are independent of this outline. */ });
    L.tileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/World_Street_Map/MapServer/tile/{z}/{y}/{x}", {
      className: "tl-osm",
      attribution: 'Tiles © Esri — Esri, HERE, Garmin, USGS, NGA, EPA, NPS',
      maxZoom: 19,
    })
      .on("tileerror", () => setTileError(true))
      .addTo(m);
    const overviewPane = m.createPane("resultOverview");
    overviewPane.style.zIndex = "390";
    const pointPane = m.createPane("resultPoints");
    pointPane.style.zIndex = "415";
    layers.current = L.layerGroup().addTo(m);
    routeLayers.current = L.layerGroup().addTo(m);
    const ro = new ResizeObserver(() => m.invalidateSize({ pan: false }));
    ro.observe(host.current);
    return () => {
      landRequest.abort();
      ro.disconnect();
      m.remove();
      map.current = null;
      layers.current = null;
      routeLayers.current = null;
      overviewLayers.current = null;
      pointRenderer.current = null;
    };
  }, []);
  useEffect(() => {
    if (result || cityId === lastCity.current || !map.current) return;
    lastCity.current = cityId || "";
    const center = cityId ? pilotCenters[cityId] : undefined;
    if (center) map.current.setView(center, 10, { animate: false });
  }, [cityId, result]);
  useEffect(() => {
    const m = map.current,
      g = layers.current;
    if (!m || !g) return;
    if (compactResult && !pointRenderer.current) pointRenderer.current = L.svg({ pane: "resultPoints" });
    if (compactSample && !overviewLayers.current) overviewLayers.current = L.layerGroup().addTo(m);
    const overview = overviewLayers.current;
    g.clearLayers();
    overview?.clearLayers();
    featureLayers.current.clear();
    selectedLayers.current = [];
    const bounds = L.latLngBounds([]);
    const foreground: L.GeoJSON[] = [];
    const surveyed = L.latLngBounds([]);
    const halos: L.CircleMarker[] = [];
    inventory.items.forEach(({ feature: f, candidates: linkedScores }) => {
      if (!f.geometry) return;
      const indexEntity = ["sample_point", "cell", "territory"].includes(f.entity_kind);
      if (indexEntity ? !visibleLayers.index && !visibleLayers.pop : !visibleLayers.objects) return;
      const totalKind = f.entity_kind === "territory" ? "territory"
        : f.entity_kind === "sample_point" || f.entity_kind === "cell" ? "sample_point" : null;
      const candidates = linkedScores.filter(
        (s) =>
          !(totalKind === "territory" && s.subject.kind === "sample_point") &&
          !(totalKind === "sample_point" && s.subject.kind === "territory"),
      );
      const totals = candidates.filter((s) =>
        s.calculation_kind === "aggregate" ||
        (s.subject.kind === totalKind && s.subject.category == null),
      );
      // An unavailable total cannot be replaced by a known category index.
      const canonical = totals.length === 1 ? totals[0]
        : !totals.length && candidates.length === 1 ? candidates[0] : undefined;
      const presentation = result && totalKind === "sample_point" ? pointScorePresentation(result, candidates, canonical, category) : { score: canonical, observed: false };
      const score = presentation.score;
      const value = score?.status === "available" ? score.value : null;
      const partial = score?.completeness === "partial";
      const dashArray = partial ? "1 4" : f.geometry_kind === "schematic" ? "5 5" : undefined;
      const color = visibleLayers.pop && indexEntity && f.population != null
        ? `hsl(250 45% ${90 - 55 * f.population / inventory.maxPopulation}%)`
        : compactResult ? indexColor(visibleLayers.pop && indexEntity ? null : value)
          : value == null || (visibleLayers.pop && indexEntity) ? "#8990a7"
            : value < 50 ? "#c72d44" : value < 75 ? "#d49520" : "#0b8a5c";
      const sample = f.entity_kind === "sample_point";
      const destination = compactSample && f.entity_kind === "destination";
      const name = pointNames.get(f.point_id || f.entity_id) || f.name ||
        (destination && f.categories?.length ? f.categories.map((category) => destinationCategoryLabels[category]).join(" · ") : selectionText[f.entity_kind]);
      const activate = () => onSelect({ entity_kind: f.entity_kind, entity_id: f.entity_id });
      const tip = document.createElement("span");
      tip.textContent =
        name +
        (f.geometry_kind === "schematic" ? " · схема" : "") +
        (visibleLayers.pop && indexEntity
          ? ` · ${f.population == null ? "Население неизвестно" : f.population.toLocaleString("ru") + " жит."}`
          : compactResult ? indexEntity ? value == null ? " · нет индекса" : ` · ${value.toLocaleString("ru", { maximumFractionDigits: 1 })} из 100` : ""
            : value == null ? " · нет индекса" : ` · ${value.toFixed(1)}`) +
        (presentation.observed ? ` · ${observedScoreLabel}` : "");
      const pointStyle: L.PathOptions = {
        color: destination ? "#59647e" : "#fff",
        weight: destination ? 2 : 2.5,
        fillColor: destination ? "#fff" : color,
        fillOpacity: sample || destination ? 0.98 : 0.8,
        dashArray: f.geometry_kind === "schematic" ? "5 5" : undefined,
      };
      const style: L.PathOptions = compactResult && f.geometry.type === "Point" ? pointStyle : {
        color: partial ? "#59647e" : "white", weight: partial ? 2 : 1,
        fillColor: color, fillOpacity: partial ? 0.3 : 0.62, dashArray,
        lineCap: partial ? "round" : "butt",
      };
      const markers: L.CircleMarker[] = [];
      const layer = L.geoJSON(
        {
          type: "Feature",
          properties: {},
          geometry: f.geometry,
        } as GeoJsonObject,
        {
          style,
          pointToLayer: (_, latlng) => {
            const marker = L.circleMarker(latlng, compactResult ? {
              pane: "resultPoints",
              renderer: pointRenderer.current ?? undefined,
              className: sample ? "api-map-point" : "api-map-object",
              radius: sample ? pointRadius : 5,
              ...pointStyle,
            } : {
              radius: 7, color: partial ? "#59647e" : "#fff", weight: 2,
              fillColor: color, fillOpacity: partial ? 0.45 : 0.9,
              dashArray, lineCap: partial ? "round" : "butt",
            });
            if (compactResult) marker.on("add", () => {
              const element = marker.getElement();
              if (!element) return;
              element.setAttribute("tabindex", "0");
              element.setAttribute("role", "button");
              element.setAttribute("aria-label", tip.textContent || selectionText[f.entity_kind]);
              element.setAttribute("aria-pressed", "false");
              element.addEventListener("focus", () => marker.openTooltip());
              element.addEventListener("blur", () => marker.closeTooltip());
              element.addEventListener("keydown", (event) => {
                if (event instanceof KeyboardEvent && (event.key === "Enter" || event.key === " ")) {
                  event.preventDefault();
                  event.stopPropagation();
                  activate();
                }
              });
            });
            markers.push(marker);
            if (sample) {
              surveyed.extend(latlng);
              if (compactSample && visibleLayers.index && !visibleLayers.pop && value !== null)
                halos.push(L.circleMarker(latlng, {
                  pane: "resultOverview", radius: 24, stroke: false,
                  fillColor: color, fillOpacity: 0.22, interactive: false,
                }));
            }
            return marker;
          },
        },
      );
      const key = featureKey(f.entity_kind, f.entity_id), entries = featureLayers.current.get(key) || [];
      entries.push({ layer, style, markers, compact: compactResult });
      featureLayers.current.set(key, entries);
      layer.bindTooltip(tip).on("click", activate)
        .addTo(g);
      if (f.entity_kind === "territory") layer.bringToBack();
      if (f.entity_kind === "sample_point") foreground.push(layer);
      bounds.extend(layer.getBounds());
    });
    for (const layer of foreground) layer.bringToFront();
    featureBounds.current = bounds;
    sampleBounds.current = surveyed;
    if (!overview || !compactSample) return;
    const totals = scores.filter((score) => score.name === "territory_index" && score.status === "available" && score.value !== null);
    const aggregate = totals.length === 1 && !category ? totals[0].value : null;
    const zoneLabel = compactSample && surveyed.isValid() && aggregate !== null && visibleLayers.index && !visibleLayers.pop
      ? (() => {
        const label = document.createElement("span");
        label.className = "api-map-zone-label";
        label.style.setProperty("--zone-color", indexColor(aggregate));
        label.textContent = `Зона расчёта · ${aggregate.toLocaleString("ru", { maximumFractionDigits: 1 })}`;
        return L.marker(surveyed.getCenter(), {
          icon: L.divIcon({ html: label, className: "api-map-zone-anchor", iconSize: [0, 0] }),
          title: "Приблизить зону расчёта",
        }).on("click", () => m.fitBounds(surveyed, { padding: [64, 64], maxZoom: 14 }));
      })() : null;
    const showOverview = () => {
      const zoomedOut = m.getZoom() <= 12;
      if (!zoomedOut) {
        overview.clearLayers();
        return;
      }
      for (const halo of halos) if (!overview.hasLayer(halo)) halo.addTo(overview);
      if (zoneLabel && !overview.hasLayer(zoneLabel)) zoneLabel.addTo(overview);
    };
    showOverview();
    m.on("zoomend", showOverview);
    return () => { m.off("zoomend", showOverview); };
  }, [result, scores, inventory, onSelect, category, compactResult, compactSample, pointNames, visibleLayers.index, visibleLayers.objects, visibleLayers.pop]);
  useEffect(() => {
    const m = map.current, g = routeLayers.current;
    if (!m || !g) return;
    g.clearLayers();
    const bounds = L.latLngBounds([]);
    if (featureBounds.current?.isValid()) bounds.extend(featureBounds.current);
    const paths = journeys ?? (scenarioView ? [] : result?.journeys || []);
    const selectedFeature = selectedId ? inventory.byId.get(selectedId)?.feature : undefined;
    const selectedIds = new Set([selectedId, selectedFeature?.point_id, ...(selectedId ? inventory.children.get(selectedId) || [] : [])].filter((id): id is string => !!id));
    paths.forEach((j) => {
      const point = j.point_id ? inventory.points.get(j.point_id) : undefined;
      const destinations = [j.destination_object_id, j.destination_node_id].flatMap((id) => id && inventory.byId.has(id) ? [inventory.byId.get(id)!] : []);
      const destination = destinations.sort((a, b) => a.index - b.index)[0]?.feature;
      if (result?.analysis_kind === "urban_passenger") {
        if (!selectedKind || !selectedIds.size || ![j.point_id, point?.entity_id, j.origin_node_id, j.destination_node_id, j.destination_object_id].some((id) => id && selectedIds.has(id))) return;
        if (activeCategory) {
          const categories = inventory.journeyCategories.get(j.journey_id);
          const matches = categories?.size ? categories.has(activeCategory) : destination?.categories?.length
            ? destination.categories.includes(activeCategory) : result.request.analysis_kind === "urban_passenger" && result.request.categories.length === 1 && result.request.categories[0] === activeCategory;
          if (!matches) return;
        }
      }
      const parentSelection: Selection | undefined = j.point_id
        ? { entity_kind: "sample_point", entity_id: point?.entity_id || j.point_id }
        : destination ? { entity_kind: destination.entity_kind, entity_id: destination.entity_id } : undefined;
      journeyLines(j).forEach((part) => {
        const tip = document.createElement("span");
        tip.textContent =
          (part.segment ? "Участок маршрута" : part.schematic ? "Схема пути" : "Маршрут") +
          (scenarioView ? " сценария" : "") +
          (part.segment ? ` · ${segmentGeometryNotice}` : "");
        const route = L.geoJSON(part.geometry as GeoJsonObject, {
          style: {
            color: "#7a52ff",
            weight: 4,
            dashArray: part.schematic ? "6 8" : undefined,
          },
        })
          .bindTooltip(tip)
          .on("click", () => parentSelection && onSelect(parentSelection))
          .addTo(g);
        bounds.extend(route.getBounds());
      });
    });
    if (selectedKind && selectedId)
      for (const item of featureLayers.current.get(featureKey(selectedKind, selectedId)) || []) item.layer.bringToFront();
    const shownView = `${result?.analysis_id}:${scenarioView ? viewId || "scenario" : "baseline"}`;
    if (result && last.current !== shownView) {
      if (compactSample && sampleBounds.current?.isValid()) {
        m.fitBounds(sampleBounds.current, {
          paddingTopLeft: [48, 120],
          paddingBottomRight: [window.innerWidth < 768 ? 48 : 330, window.innerWidth < 768 ? 260 : 122],
          maxZoom: 14, animate: false,
        });
        last.current = shownView;
        lastAnalysis.current = result.analysis_id;
      } else if (moscowCamera(result)) {
        if (lastAnalysis.current !== result.analysis_id) showMoscowCenter(m);
        last.current = shownView;
        lastAnalysis.current = result.analysis_id;
      } else if (bounds.isValid()) {
        m.fitBounds(bounds, {
        paddingTopLeft: [30, 130],
        paddingBottomRight: [window.innerWidth < 768 ? 30 : 360, 130],
        maxZoom: 14,
        animate: false,
      });
        last.current = shownView;
        lastAnalysis.current = result.analysis_id;
      }
    }
  }, [result, inventory, onSelect, scenarioView, journeys, viewId, activeCategory, selectedKind, selectedId, compactSample]);
  useEffect(() => {
    for (const item of selectedLayers.current) selectLayer(item, false);
    const next = selection ? featureLayers.current.get(featureKey(selection.entity_kind, selection.entity_id)) || [] : [];
    for (const item of next) selectLayer(item, true);
    selectedLayers.current = next;
  }, [selection, result, inventory, onSelect, scenarioView, journeys, viewId, category, visibleLayers.index, visibleLayers.objects, visibleLayers.pop]);
  useEffect(() => {
    if (lastZoom.current === zoomAction.id || !map.current) return;
    lastZoom.current = zoomAction.id;
    const m = map.current;
    if (zoomAction.kind === "in") m.zoomIn();
    else if (zoomAction.kind === "out") m.zoomOut();
    else if (zoomAction.kind === "all_points") {
      const positions = (result?.map_data?.features || []).flatMap((feature): [number, number][] =>
        feature.entity_kind === "sample_point" && feature.geometry_kind === "actual" && feature.geometry?.type === "Point"
          ? [[feature.geometry.coordinates[1], feature.geometry.coordinates[0]]] : []);
      const bounds = L.latLngBounds(positions);
      if (bounds.isValid()) m.fitBounds(bounds, { padding: [30, 30], maxZoom: 14, animate: false });
    }
    else if (zoomAction.kind === "home" && moscowCamera(result)) showMoscowCenter(m);
    else if (zoomAction.kind === "home" && !result && cityId && pilotCenters[cityId]) {
      m.setView(pilotCenters[cityId], 10, { animate: false });
    }
    else if (zoomAction.kind === "selection") {
      const target = result?.map_data?.features?.find((f) => f.entity_kind === selection?.entity_kind &&
        (f.entity_id === selection.entity_id || f.point_id === selection.entity_id));
      if (target?.geometry?.type !== "Point") return;
      const [longitude, latitude] = target.geometry.coordinates;
      const location: [number, number] = [latitude, longitude];
      if (m.getZoom() < 12) m.setZoomAround(location, 12, { animate: false });
      let top = 110, right = window.innerWidth < 768 ? 24 : 360, bottom = 120;
      const rect = host.current?.getBoundingClientRect();
      const card = document.getElementById("apiResultCard")?.getBoundingClientRect();
      const status = host.current?.closest(".at-map")?.querySelector(".ov-tl")?.getBoundingClientRect();
      const dock = host.current?.closest(".at-map")?.querySelector(".ov-bottom")?.getBoundingClientRect();
      if (rect && card && rect.height > 0) {
        top = Math.max(24, (status?.bottom || rect.top) - rect.top + 12);
        bottom = Math.max(24, rect.bottom - (dock?.top || rect.bottom) + 12);
        if (window.innerWidth < 768) {
          const above = card.top - rect.top - top;
          const below = rect.bottom - bottom - card.bottom;
          if (above >= below) bottom = rect.bottom - card.top + 12;
          else top = card.bottom - rect.top + 12;
        } else right = Math.max(24, rect.right - card.left + 12);
      }
      m.panInside(location, { paddingTopLeft: [24, top], paddingBottomRight: [right, bottom], animate: false });
    }
    else
      m.fitBounds(
        [
          [40, 19],
          [82, 190],
        ],
        { animate: false },
      );
  }, [zoomAction, result, selection, cityId]);
  useEffect(() => {
    const instance = map.current;
    host.current?.classList.toggle("is-picking-address", !!onAddressPick);
    if (!instance || !onAddressPick) return;
    const click = ({ latlng }: L.LeafletMouseEvent) => onAddressPick({ lat: latlng.lat, lon: latlng.wrap().lng });
    instance.on("click", click);
    return () => { instance.off("click", click); };
  }, [onAddressPick]);
  useEffect(() => {
    const instance = map.current;
    const reports = districts ?? (district ? [district] : []);
    if (!instance || !reports.length) return;
    const renderer = L.svg();
    const group = L.featureGroup().addTo(instance);
    const bounds = L.latLngBounds([]);
    const targets = new Set<string>();
    for (const report of reports) {
      const value = districtMetric === "overall" ? report.index : report.categories[districtMetric] ?? null;
      if (report.boundary) {
        const boundary = L.geoJSON(report.boundary, {
          renderer, style: { color: report.district.id === district?.district.id ? "#2f5bff" : "#67728f", weight: report.district.id === district?.district.id ? 3 : 1.5, fillColor: indexColor(value), fillOpacity: value == null ? 0.16 : 0.3, dashArray: value == null ? "5 5" : undefined },
        } as L.GeoJSONOptions & L.PathOptions).addTo(group);
        const caption = document.createElement("span");
        caption.textContent = `${report.district.name} · ${value == null ? "Недостаточно данных" : accessNumber.format(value) + " из 100"}`;
        boundary.bindTooltip(caption).on("click", () => onDistrictSelect?.(report));
        bounds.extend(boundary.getBounds());
        if (value != null) {
          const label = document.createElement("span");
          label.className = "district-index-label";
          label.style.color = accessAssessment(value).color;
          label.textContent = accessNumber.format(value);
          L.marker(boundary.getBounds().getCenter(), { interactive: false, icon: L.divIcon({ className: "district-label-host", html: label, iconSize: [56, 28], iconAnchor: [28, 14] }) }).addTo(group);
        }
      }
      for (const point of report.sample_points || []) {
        L.circleMarker([point.lat, point.lon], {
          renderer, radius: 3, color: "#fff", fillColor: "#59668b", fillOpacity: 0.85, weight: 1, className: "api-district-origin",
        }).bindTooltip("Исходная точка: отсюда считали путь до объектов").addTo(group);
      }
      for (const target of report.destinations || []) {
        if (districtMetric !== "overall" && target.category !== districtMetric) continue;
        const key = `${target.category}:${target.lat}:${target.lon}`;
        if (targets.has(key)) continue;
        targets.add(key);
        const label = document.createElement("span");
        label.textContent = `${addressCategoryLabels[target.category]} · ${target.name}`;
        L.marker([target.lat, target.lon], { title: label.textContent || target.name, alt: label.textContent || target.name, icon: L.divIcon({ className: `district-place-icon category-icon-host category-${target.category}`, html: renderToStaticMarkup(createElement("span", { className: "category-map-symbol", style: { "--category-color": accessCategoryColors[target.category] } as import("react").CSSProperties }, createElement(Icon, { name: accessIcons[target.category] }))), iconSize: [28, 28], iconAnchor: [14, 14] }) })
          .bindTooltip(label).bindPopup(label.cloneNode(true) as HTMLElement).addTo(group);
      }
    }
    if (bounds.isValid()) instance.fitBounds(bounds, { paddingTopLeft: [32, 96], paddingBottomRight: [window.innerWidth < 768 ? 32 : 340, window.innerWidth < 768 ? 250 : 130], maxZoom: 14, animate: false });
    return () => { group.remove(); renderer.remove(); };
  }, [district, districts, districtMetric, onDistrictSelect]);
  useEffect(() => {
    const instance = map.current;
    if (!instance || !addressMode) return;
    const frame = requestAnimationFrame(() => instance.invalidateSize({ pan: false }));
    return () => cancelAnimationFrame(frame);
  }, [addressMode]);
  useEffect(() => {
    const instance = map.current;
    if (!addressMode || !instance) return;
    if (!addressPlace) {
      showMoscowCenter(instance);
      return;
    }
    const group = L.featureGroup().addTo(instance);
    const renderer = L.svg();
    const marker = L.circleMarker([addressPlace.lat, addressPlace.lon], {
      renderer, radius: 8, color: "#ffffff", fillColor: "#2856e4", fillOpacity: 1, weight: 2, className: "api-address-marker",
    });
    const caption = document.createElement("span");
    caption.textContent = addressPlace.label;
    marker.bindTooltip(caption).addTo(group);
    addressResult?.categories.forEach((item) => {
      const color = accessCategoryColors[item.category];
      const label = document.createElement("span");
      label.textContent = `${addressCategoryLabels[item.category]} · ${item.destination?.name || "Маршрут"}`;
      if (item.path && item.path.length > 1) L.polyline(item.path.map((point) => [point.lat, point.lon]), {
        renderer, color, weight: 4, className: "api-address-route",
      }).bindTooltip(label).addTo(group);
      if (item.destination) {
        const icon = document.createElement("span");
        icon.className = "category-map-symbol";
        icon.style.setProperty("--category-color", color);
        icon.innerHTML = renderToStaticMarkup(createElement(Icon, { name: accessIcons[item.category] }));
        L.marker([item.destination.lat, item.destination.lon], {
          title: label.textContent || item.destination.name, alt: label.textContent || item.destination.name,
          icon: L.divIcon({ className: "api-address-destination category-icon-host", html: icon, iconSize: [32, 32], iconAnchor: [16, 16] }),
        }).bindTooltip(label.cloneNode(true) as HTMLElement).bindPopup(label.cloneNode(true) as HTMLElement).addTo(group);
      }
    });
    marker.bringToFront();
    if (addressResult) {
      instance.fitBounds(group.getBounds(), { paddingTopLeft: [32, 76], paddingBottomRight: [window.innerWidth < 768 ? 32 : 340, 100], maxZoom: 16, animate: false });
    } else if (addressPlace.addressId) {
      instance.setView([addressPlace.lat, addressPlace.lon], Math.max(instance.getZoom(), 14), { animate: false });
    }
    return () => { group.remove(); renderer.remove(); };
  }, [addressMode, addressPlace, addressResult]);
  return (
    <>
      <div
        ref={host}
        className="map-layer api-map-layer"
        aria-label="Карта результатов API"
      />
      {tileError && (
        <div className="api-map-note">
          Подложка недоступна. Полученные геометрии остаются видимыми.
        </div>
      )}
    </>
  );
}
