import { useEffect, useRef, useState } from "react";
import L from "leaflet";
import type { GeoJsonObject } from "geojson";
import type { AnalysisResult, Schemas, Selection } from "../api/types";
import { subjectMatches, selectionText } from "../api/adapters/result";
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
      const candidates = scores.filter(
        (s) =>
          s.status === "available" &&
          ids.some((id) =>
            subjectMatches(s.subject, {
              entity_kind: f.entity_kind,
              entity_id: id,
            }),
          ),
      );
      const score = (
        candidates.find((s) => s.calculation_kind === "aggregate") ||
        (candidates.length === 1 ? candidates[0] : undefined)
      );
      const value = score?.value;
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
        if (!j.geometry) return;
        const tip = document.createElement("span");
        tip.textContent =
          (j.geometry_kind === "schematic" ? "Схема пути" : "Маршрут") +
          (scenarioView ? " сценария" : "");
        const route = L.geoJSON(j.geometry as GeoJsonObject, {
          style: {
            color: "#7a52ff",
            weight: 4,
            dashArray: j.geometry_kind === "schematic" ? "6 8" : undefined,
          },
        })
          .bindTooltip(tip)
          .addTo(g);
        bounds.extend(route.getBounds());
      });
    const shownView = `${result?.analysis_id}:${scenarioView ? viewId || "scenario" : "baseline"}`;
    if (result && last.current !== shownView && bounds.isValid()) {
      m.fitBounds(bounds, {
        paddingTopLeft: [30, 130],
        paddingBottomRight: [window.innerWidth < 768 ? 30 : 360, 130],
        maxZoom: 14,
        animate: false,
      });
      last.current = shownView;
    }
  }, [result, scores, selection, onSelect, scenarioView, journeys, viewId]);
  useEffect(() => {
    if (lastZoom.current === zoomAction.id || !map.current) return;
    lastZoom.current = zoomAction.id;
    const m = map.current;
    if (zoomAction.kind === "in") m.zoomIn();
    else if (zoomAction.kind === "out") m.zoomOut();
    else
      m.fitBounds(
        [
          [40, 19],
          [82, 190],
        ],
        { animate: false },
      );
  }, [zoomAction]);
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
