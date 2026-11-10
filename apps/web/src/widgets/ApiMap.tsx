import { useEffect, useRef, useState } from "react";
import L from "leaflet";
import type { GeoJsonObject } from "geojson";
import type { AnalysisResult, Schemas, Selection } from "../api/types";
import { subjectMatches, selectionText } from "../api/adapters/result";
import { journeyLines, segmentGeometryNotice } from "../api/adapters/journeyGeometry";
import { isDemoModelSnapshot } from "../api/adapters/demoModel";

// Presentation camera for the explicitly labelled generated demo, not data scope.
const showDemoCenter = (map: L.Map) => map.setView([55.75, 37.62], 10, { animate: false });
export function ApiMap({
  result,
  scores,
  selection,
  onSelect,
  zoomAction,
  scenarioView = false,
  journeys,
  viewId,
}: {
  result: AnalysisResult | null;
  scores: Schemas["Score"][];
  selection: Selection | null;
  onSelect: (s: Selection) => void;
  zoomAction: { id: number; kind: string };
  scenarioView?: boolean;
  journeys?: Schemas["JourneyResult"][];
  viewId?: string;
}) {
  const host = useRef<HTMLDivElement>(null),
    map = useRef<L.Map | null>(null),
    layers = useRef<L.LayerGroup | null>(null),
    last = useRef(""),
    lastAnalysis = useRef(""),
    lastZoom = useRef(zoomAction.id);
  const [tileError, setTileError] = useState(false);
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
    const ro = new ResizeObserver(() => m.invalidateSize({ pan: false }));
    ro.observe(host.current);
    return () => {
      ro.disconnect();
      m.remove();
      map.current = null;
      layers.current = null;
    };
  }, []);
  useEffect(() => {
    const m = map.current,
      g = layers.current;
    if (!m || !g) return;
    g.clearLayers();
    const bounds = L.latLngBounds([]);
    const features = result?.map_data?.features || [];
    features.forEach((f) => {
      if (!f.geometry) return;
      const ids = [
        f.entity_id,
        f.point_id,
        ...features
          .filter((p) => p.cell_id === f.entity_id)
          .map((p) => p.entity_id),
      ].filter((id): id is string => !!id);
      const totalKind = f.entity_kind === "territory" ? "territory"
        : f.entity_kind === "sample_point" || f.entity_kind === "cell" ? "sample_point" : null;
      const candidates = scores.filter(
        (s) =>
          !(totalKind === "territory" && s.subject.kind === "sample_point") &&
          !(totalKind === "sample_point" && s.subject.kind === "territory") &&
          ids.some((id) =>
            subjectMatches(s.subject, {
              entity_kind: f.entity_kind,
              entity_id: id,
            }),
          ),
      );
      const totals = candidates.filter((s) =>
        s.calculation_kind === "aggregate" ||
        (s.subject.kind === totalKind && s.subject.category == null),
      );
      // An unavailable total cannot be replaced by a known category index.
      const score = totals.length === 1 ? totals[0]
        : !totals.length && candidates.length === 1 ? candidates[0] : undefined;
      const value = score?.status === "available" ? score.value : null;
      const partial = score?.completeness === "partial";
      const dashArray = partial ? "1 4" : f.geometry_kind === "schematic" ? "5 5" : undefined;
      const color =
        value == null
          ? "#8990a7"
          : value < 50
            ? "#c72d44"
            : value < 75
              ? "#d49520"
              : "#0b8a5c";
      const selected =
        selection?.entity_id === f.entity_id &&
        selection.entity_kind === f.entity_kind;
      const layer = L.geoJSON(
        {
          type: "Feature",
          properties: {},
          geometry: f.geometry,
        } as GeoJsonObject,
        {
          style: {
            color: selected ? "#2f5bff" : partial ? "#59647e" : "white",
            weight: selected ? 3 : partial ? 2 : 1,
            fillColor: color,
            fillOpacity: partial ? 0.3 : 0.62,
            dashArray,
            lineCap: partial ? "round" : "butt",
          },
          pointToLayer: (_, latlng) =>
            L.circleMarker(latlng, {
              radius: selected ? 10 : 7,
              color: selected ? "#2f5bff" : partial ? "#59647e" : "#fff",
              weight: 2,
              fillColor: color,
              fillOpacity: partial ? 0.45 : 0.9,
              dashArray,
              lineCap: partial ? "round" : "butt",
            }),
        },
      );
      const tip = document.createElement("span");
      tip.textContent =
        (f.name || selectionText[f.entity_kind]) +
        (f.geometry_kind === "schematic" ? " · схема" : "") +
        (value == null ? " · нет индекса" : ` · ${value.toFixed(1)}`) +
        (partial ? " · частичная оценка" : "");
      layer
        .bindTooltip(tip)
        .on("click", () =>
          onSelect({ entity_kind: f.entity_kind, entity_id: f.entity_id }),
        )
        .addTo(g);
      bounds.extend(layer.getBounds());
    });
    const paths = journeys ?? (scenarioView ? [] : result?.journeys || []);
    paths.forEach((j) => {
      const point = features.find((f) => f.entity_kind === "sample_point" && (f.entity_id === j.point_id || f.point_id === j.point_id));
      const destination = features.find((f) => f.entity_id === j.destination_object_id || f.entity_id === j.destination_node_id);
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
    const shownView = `${result?.analysis_id}:${scenarioView ? viewId || "scenario" : "baseline"}`;
    if (result && last.current !== shownView) {
      if (isDemoModelSnapshot(result.snapshot_id)) {
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
  }, [result, scores, selection, onSelect, scenarioView, journeys, viewId]);
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
    else if (zoomAction.kind === "home" && isDemoModelSnapshot(result?.snapshot_id)) showDemoCenter(m);
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
      const status = host.current?.parentElement?.querySelector(".api-status")?.getBoundingClientRect();
      const dock = host.current?.parentElement?.querySelector(".api-bottom")?.getBoundingClientRect();
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
  }, [zoomAction, result, selection]);
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
