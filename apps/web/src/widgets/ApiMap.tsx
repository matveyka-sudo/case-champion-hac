import { useEffect, useMemo, useRef, useState } from "react";
import L from "leaflet";
import type { GeoJsonObject } from "geojson";
import type { AnalysisResult, Schemas, Selection } from "../api/types";
import { selectionText } from "../api/adapters/result";
import { journeyLines, segmentGeometryNotice } from "../api/adapters/journeyGeometry";
import { isDemoModelSnapshot } from "../api/adapters/demoModel";
import { displayedCategory, pointScorePresentation, observedScoreLabel } from "../api/adapters/scorePresentation";
import type { Category } from "../api/types";
const moscowCamera = (result: AnalysisResult | null) => isDemoModelSnapshot(result?.snapshot_id) || (result?.request.analysis_kind === "urban_passenger" && result.request.city_id === "moscow");

// City presentation camera; accepted source points and calculation scope are unchanged.
const showDemoCenter = (map: L.Map) => map.setView([55.75, 37.62], 10, { animate: false });
type FeatureLayer = { layer: L.GeoJSON; style: L.PathOptions; markers: L.CircleMarker[] };
const featureKey = (kind: string, id: string) => `${kind}:${id}`;
function selectLayer(item: FeatureLayer, selected: boolean) {
  item.layer.setStyle(selected ? { ...item.style, color: "#2f5bff", weight: 3 } : item.style);
  for (const marker of item.markers) marker.setRadius(selected ? 10 : 7);
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
  layers?: { index: boolean; objects: boolean; pop: boolean };
}) {
  const host = useRef<HTMLDivElement>(null),
    map = useRef<L.Map | null>(null),
    layers = useRef<L.LayerGroup | null>(null),
    routeLayers = useRef<L.LayerGroup | null>(null),
    featureBounds = useRef<L.LatLngBounds | null>(null),
    last = useRef(""),
    lastAnalysis = useRef(""),
    lastZoom = useRef(zoomAction.id),
    lastCity = useRef(""),
    featureLayers = useRef(new Map<string, FeatureLayer[]>()),
    selectedLayers = useRef<FeatureLayer[]>([]);
  const [tileError, setTileError] = useState(false);
  const selectedKind = selection?.entity_kind, selectedId = selection?.entity_id;
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
      preferCanvas: true,
    }).fitBounds([
      [40, 19],
      [82, 190],
    ]);
    map.current = m;
    L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
      className: "tl-osm",
      attribution: "© OpenStreetMap",
      maxZoom: 19,
    })
      .on("tileerror", () => setTileError(true))
      .addTo(m);
    layers.current = L.layerGroup().addTo(m);
    routeLayers.current = L.layerGroup().addTo(m);
    const ro = new ResizeObserver(() => m.invalidateSize({ pan: false }));
    ro.observe(host.current);
    return () => {
      ro.disconnect();
      m.remove();
      map.current = null;
      layers.current = null;
      routeLayers.current = null;
    };
  }, []);
  useEffect(() => {
    if (result || cityId === lastCity.current || !map.current) return;
    lastCity.current = cityId || "";
    if (cityId === "moscow") showDemoCenter(map.current);
  }, [cityId, result]);
  useEffect(() => {
    const m = map.current,
      g = layers.current;
    if (!m || !g) return;
    g.clearLayers();
    featureLayers.current.clear();
    selectedLayers.current = [];
    const bounds = L.latLngBounds([]);
    const foreground: L.GeoJSON[] = [];
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
      const color = visibleLayers.pop && indexEntity ? f.population == null ? "#8990a7" : `hsl(250 45% ${90 - 55 * f.population / inventory.maxPopulation}%)` :
        value == null
          ? "#8990a7"
          : value < 50
            ? "#c72d44"
            : value < 75
              ? "#d49520"
              : "#0b8a5c";
      const style: L.PathOptions = {
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
            const marker = L.circleMarker(latlng, {
              radius: 7,
              color: partial ? "#59647e" : "#fff",
              weight: 2,
              fillColor: color,
              fillOpacity: partial ? 0.45 : 0.9,
              dashArray,
              lineCap: partial ? "round" : "butt",
            });
            markers.push(marker);
            return marker;
          },
        },
      );
      const key = featureKey(f.entity_kind, f.entity_id), entries = featureLayers.current.get(key) || [];
      entries.push({ layer, style, markers });
      featureLayers.current.set(key, entries);
      const tip = document.createElement("span");
      tip.textContent =
        (f.name || selectionText[f.entity_kind]) +
        (f.geometry_kind === "schematic" ? " · схема" : "") +
        (value == null ? " · нет индекса" : ` · ${value.toFixed(1)}`) +
        (presentation.observed ? ` · ${observedScoreLabel}` : "") +
        (visibleLayers.pop ? ` · население: ${f.population == null ? "неизвестно" : f.population.toLocaleString("ru")}` : "") +
        (partial ? " · частичная оценка" : "");
      layer
        .bindTooltip(tip)
        .on("click", () =>
          onSelect({ entity_kind: f.entity_kind, entity_id: f.entity_id }),
        )
        .addTo(g);
      if (f.entity_kind === "territory") layer.bringToBack();
      if (f.entity_kind === "sample_point") foreground.push(layer);
      bounds.extend(layer.getBounds());
    });
    for (const layer of foreground) layer.bringToFront();
    featureBounds.current = bounds;
  }, [result, inventory, onSelect, category, visibleLayers.index, visibleLayers.objects, visibleLayers.pop]);
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
      if (moscowCamera(result)) {
        if (lastAnalysis.current !== result.analysis_id) showDemoCenter(m);
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
  }, [result, inventory, onSelect, scenarioView, journeys, viewId, activeCategory, selectedKind, selectedId]);
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
    else if (zoomAction.kind === "home" && (moscowCamera(result) || (!result && cityId === "moscow"))) showDemoCenter(m);
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
  return (
    <>
      <div
        ref={host}
        className="map-layer"
        aria-label="Карта результатов API"
      />
      {scores.some((s) => s.status === "available" && s.completeness === "partial") && (
        <div className="api-map-key" aria-label="Условные обозначения карты">
          <span className="api-partial-symbol" aria-hidden="true" />
          Точечный контур — частичная оценка
        </div>
      )}
      {tileError && (
        <div className="api-map-note">
          Подложка недоступна. Полученные геометрии остаются видимыми.
        </div>
      )}
    </>
  );
}
