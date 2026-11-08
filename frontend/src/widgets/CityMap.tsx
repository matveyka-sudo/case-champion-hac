import { mapColor, mapPalette } from "../shared/mapColor";
import { useEffect, useRef, useState } from "react";
import L from "leaflet";
import { renderToStaticMarkup } from "react-dom/server";
import { useAtlas, currentStatus } from "../features/state";
import {
  fixtures,
  cellScore,
  categoryById,
  type Cell,
  type CityId,
  type CatId,
  type LL,
} from "../mocks/model";
import { Icon } from "../shared/ui";
import { f1 } from "../shared/format";
const iconMarkup = new Map<string, string>();
const mapLevel = (zoom: number) => zoom <= 7 ? 0 : zoom <= 10.5 ? 1 : zoom < 13.5 ? 2 : 3;
function focusCity(map: L.Map, city: CityId, cargo: boolean) {
  map.stop();
  const mobile = window.innerWidth < 768;
  if (cargo) map.setView([44.733, 37.79], 13, { animate: false });
  else if (city === "msk") map.setView(mobile ? [55.703, 37.752] : [55.706, 37.795], mobile ? 13 : 12.5, { animate: false });
  else map.setView(fixtures.city[city].c, 12, { animate: false });
}
export function CityMap({
  zoomAction,
}: {
  zoomAction: { kind: string; id: number };
}) {
  const lastZoomAction = useRef(zoomAction.id);
  const host = useRef<HTMLDivElement>(null),
    mapRef = useRef<L.Map | null>(null),
    groupRef = useRef<L.LayerGroup | null>(null);
  const [level, setLevel] = useState(2),
    [tileFailed, setTileFailed] = useState(false);
  const { state: s, dispatch } = useAtlas();
  const p = s.applied;
  const city = s.p.mode === "cargo" ? "nvr" : s.p.city || "msk";
  const { layers, cLayers, metric, selection, scenario, scnView } = s;
  const { mode, cargoDir, profile } = s.p;
  const handling = profile.handling;
  const status = currentStatus(s);
  const renderStatus = ["nd", "idle", "srcerr", "baddate", "pending", "partial", "noroute"].includes(status) ? status : "ok";
  const night = s.themeMode === "dark" || (s.themeMode === "auto" && (s.p.t >= 80 || s.p.t <= 23));
  useEffect(() => {
    if (!host.current) return;
    const map = L.map(host.current, {
      zoomControl: false,
      attributionControl: true,
      minZoom: 1,
      preferCanvas: true,
      inertia: true,
      maxZoom: 16,
      zoomSnap: 0.5,
      wheelPxPerZoomLevel: 90,
    });
    mapRef.current = map;
    map.attributionControl.setPrefix(false);
    let errs = 0;
    L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 19,
      attribution:
        '© <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">участники OpenStreetMap</a>',
      className: "tl-osm",
      crossOrigin: true,
    })
      .on("tileerror", () => {
        if (++errs === 6) setTileFailed(true);
      })
      .addTo(map);
    groupRef.current = L.layerGroup().addTo(map);
    map.on("zoomend", () => setLevel(mapLevel(map.getZoom())));
    const ro = new ResizeObserver(() => map.invalidateSize({ pan: false }));
    ro.observe(host.current);
    return () => {
      ro.disconnect();
      map.remove();
      mapRef.current = null;
      groupRef.current = null;
    };
  }, []);
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    focusCity(map, city, mode === "cargo");
  }, [city, mode]);
  useEffect(() => {
    const map = mapRef.current,
      g = groupRef.current;
    if (!map || !g) return;
    g.clearLayers();
    const style = getComputedStyle(document.documentElement);
    const palette = mapPalette();
    const color = (name: string) => style.getPropertyValue(name).trim();
    if (level === 0) {
      (Object.keys(fixtures.city) as CityId[]).filter((id) => mode !== "cargo" || id === "nvr").forEach((id) => {
        const entry = fixtures.city[id];
        const name = id === "msk" ? "Москва" : id === "kgd" ? "Калининград" : "Новороссийск";
        const label = document.createElement("span");
        label.textContent = name + " · демо-данные";
        L.marker(entry.c, { title: name, icon: L.divIcon({ className: "stop-dot xfer", iconSize: [16, 16], iconAnchor: [8, 8] }) })
          .bindTooltip(label, { permanent: true, direction: "top", className: "wx-tip" })
          .on("click", () => {
            if (mode !== "cargo") dispatch({ type: "city", city: id });
            focusCity(map, id, mode === "cargo");
          }).addTo(g);
      });
      return;
    }
    const select = (
      type: "cell" | "obj" | "wh" | "node",
      id: string,
      cat?: CatId,
    ) =>
      dispatch({
        type: "ui",
        patch: { selection: { type, id, cat }, cardHidden: false },
      });
    const marker = (
      ll: LL,
      icon: string,
      title: string,
      click: () => void,
      selected = false,
      extra = "",
    ) => {
      const content = document.createElement("div");
      content.className = `obj-pin ${selected ? "sel" : ""} ${extra}`;
      if (!iconMarkup.has(icon)) iconMarkup.set(icon, renderToStaticMarkup(<Icon name={icon} />));
      content.innerHTML = iconMarkup.get(icon)!;
      return L.marker(ll, {
        icon: L.divIcon({
          className: "",
          html: content,
          iconSize: [selected ? 40 : 34, selected ? 40 : 34],
          iconAnchor: [selected ? 20 : 17, selected ? 20 : 17],
        }),
        title,
        keyboard: true,
        alt: title,
      })
        .on("click", click)
        .addTo(g);
    };
    if (mode === "cargo") {
      const warehouse = fixtures.cargo.objs[0];
      const showingWarehouse = !selection || selection.type === "wh";
      fixtures.cargo.nodes
        .filter((n) => cLayers[n.type])
        .forEach((n) => {
          const ok = n.h.includes(handling);
          const showTime = ok && showingWarehouse && cLayers.wh;
          marker(
            n.ll,
            n.icon,
            n.name,
            () => {},
            false,
            `${n.type === "port" ? "port" : ""} ${ok ? "" : "dim"}`,
          ).bindTooltip(
            showTime
              ? `${warehouse.t[n.id] + (cargoDir === "in" ? 2 : 0)} мин`
              : `${n.name}${ok ? "" : " · не подходит профилю"}`,
            {
              className: "wx-tip",
              direction: "right",
              offset: [16, 0],
              permanent: showTime,
            },
          );
          if (showTime)
            L.polyline([warehouse.ll, n.ll], {
              color: color("--accent"),
              weight: 3,
              opacity: 0.85,
              dashArray: "1 8",
              lineCap: "round",
              interactive: false,
            }).addTo(g);
        });
      fixtures.cargo.objs
        .filter((o) => cLayers[o.type])
        .forEach((o) =>
          marker(
            o.ll,
            o.type === "wh" ? "warehouse" : "factory",
            o.name,
            () => {
              if (o.id === "w1") select("wh", o.id);
            },
            o.id === "w1",
            `sq ${o.name.includes("неизвестен") ? "dim" : ""}`,
          ).bindTooltip(o.name + (o.id === "w1" ? " · выбран" : ""), {
            className: "wx-tip",
            direction: "top",
            offset: [0, -16],
          }),
        );
      return;
    }
    const M = fixtures.cities[city];
    if (!M) return;
    const scn = scenario === "done" && scnView === "scn",
      st = renderStatus;
    const empty = ["nd", "idle", "srcerr", "baddate", "pending"].includes(st);
    const partial = city === "kgd" || st === "partial";
    const selected =
      selection?.type === "cell" ? selection.id : M.canonCell?.id;
    const value = (c: Cell) =>
      layers.pop
        ? Math.max(0, Math.min(100, ((c.pop - 120) / 1900) * 100))
        : cellScore(c, metric, p.travel, p.cats, scn);
    const unavailable = (c: Cell) =>
      empty || (partial && (c.ndPartial || (st === "partial" && c.j <= -6)));
    const draw = (cells: Cell[], bounds: L.LatLngBoundsExpression) => {
      const valid = cells.filter((c) => !unavailable(c));
      const pop = valid.reduce((a, c) => a + c.pop, 0);
      const v = pop
        ? valid.reduce((a, c) => a + (value(c) || 0) * c.pop, 0) / pop
        : null;
      const c = cells[0];
      const isPoint = cells.length === 1 && level === 3;
      const options: L.PathOptions = {
        weight: cells.length === 1 ? 1 : 1.2,
        color: "#fff",
        opacity: 0.55,
        fillOpacity:
          layers.index || layers.pop ? (isPoint ? 0.92 : 0.58) : 0,
        fillColor: mapColor(st === "noroute" && c.zone ? 0 : v, palette),
        ...(scn && c.eff === "up" ? { color: color("--ok"), weight: 2 } : {}),
        ...(scn && c.eff === "dn" ? { color: color("--err"), weight: 2 } : {}),
        ...(cells.some((cell) => cell.id === selected) ? { color: color("--sel"), weight: 3, opacity: 1 } : {}),
        ...(!valid.length ? { fillColor: "#999", fillOpacity: 0.35, dashArray: "3 4" } : {}),
        className: `cell ${!valid.length ? "cell-nd" : ""} ${cells.some((c) => c.id === selected) ? "cell-sel" : ""} ${scn && c.eff === "up" ? "cell-up" : ""} ${scn && c.eff === "dn" ? "cell-dn" : ""}`,
      };
      const shape = isPoint
        ? L.circleMarker(c.ll, { ...options, radius: 7 })
        : L.rectangle(bounds, options);
      const tip = document.createElement("span");
      tip.textContent =
        cells.length > 1
          ? `Агрегат 4×4 км · ${cells.length} ячеек · ${f1(v)} (демо)`
          : unavailable(c)
            ? "Нет расписания — результата нет"
            : layers.pop
              ? `≈ ${c.pop} жит. · модель`
              : `${metric === "overall" ? "Общий индекс" : `${c.values[p.travel][metric as CatId]?.time} мин · индекс`} ${f1(v)} · демо`;
      shape
        .bindTooltip(tip, { className: "wx-tip", sticky: true, opacity: 1 })
        .on("click", () => {
          if (cells.length === 1) select("cell", c.id);
          else
            map.flyTo(
              (bounds instanceof L.LatLngBounds
                ? bounds
                : new L.LatLngBounds(bounds)
              ).getCenter(),
              12,
              {
                duration: matchMedia("(prefers-reduced-motion: reduce)").matches
                  ? 0
                  : 0.6,
              },
            );
        })
        .addTo(g);
    };
    if (level === 1) {
      const blocks: Record<string, Cell[]> = {};
      M.cells.forEach((c) => {
        const key = `${Math.floor(c.i / 4)}:${Math.floor(c.j / 4)}`;
        (blocks[key] ||= []).push(c);
      });
      Object.values(blocks).forEach((cells) => {
        const lat = cells.map((c) => c.ll[0]),
          lon = cells.map((c) => c.ll[1]);
        draw(cells, [
          [Math.min(...lat) - M.dLat / 2, Math.min(...lon) - M.dLon / 2],
          [Math.max(...lat) + M.dLat / 2, Math.max(...lon) + M.dLon / 2],
        ]);
      });
    } else
      M.cells.forEach((c) =>
        draw(
          [c],
          [
            [c.ll[0] - M.dLat / 2, c.ll[1] - M.dLon / 2],
            [c.ll[0] + M.dLat / 2, c.ll[1] + M.dLon / 2],
          ],
        ),
      );
    const focus =
      metric === "overall"
        ? (Object.keys(p.cats).sort(
            (a, b) => (p.cats[b as CatId] || 0) - (p.cats[a as CatId] || 0),
          )[0] as CatId)
        : (metric as CatId);
    const selectedCell = M.cells.find((c) => c.id === selected) || M.canonCell;
    const nearest = selectedCell?.near[focus]?.obj;
    if (layers.objects && focus)
      M.objs[focus]?.forEach((o) =>
        marker(
          o.ll,
          categoryById[focus].icon,
          categoryById[focus].name + " (пример)",
          () => select("obj", o.id, focus),
          o.id === nearest?.id,
        ),
      );
    if (
      city === "msk" &&
      selectedCell?.canon &&
      p.travel === "pt" &&
      p.cats.c01
    ) {
      const r = fixtures.canon.route;
      const colors = [
        color("--text-2"),
        color("--accent"),
        color("--text-2"),
        color("--accent-2"),
        color("--text-2"),
      ];
      for (let i = 0; i < 5; i++)
        L.polyline([r[i], r[i + 1]], {
          color: colors[i],
          weight: i === 1 || i === 3 ? 5 : 3,
          dashArray: i % 2 === 0 ? "2 7" : undefined,
        }).addTo(g);
      [1, 2, 4].forEach((i) => {
        const el = document.createElement("div");
        el.className = `stop-dot ${i === 2 ? "xfer" : ""}`;
        L.marker(r[i], {
          icon: L.divIcon({
            className: "",
            html: el,
            iconSize: [16, 16],
            iconAnchor: [8, 8],
          }),
          keyboard: false,
        })
          .bindTooltip(
            i === 2 ? `Пересадка: ожидание ${scn ? 3 : 11} мин` : "Остановка",
            {
              className: "wx-tip",
              permanent: i === 2,
              direction: "top",
              offset: [0, -10],
            },
          )
          .addTo(g);
      });
    }
  }, [city, dispatch, p.travel, p.cats, mode, handling, cargoDir, layers,
    cLayers, metric, selection, scenario, scnView, renderStatus, level, night]);
  useEffect(() => {
    if (lastZoomAction.current === zoomAction.id) return;
    lastZoomAction.current = zoomAction.id;
    const map = mapRef.current;
    if (!map) return;
    map.stop();
    if (zoomAction.kind === "home")
      map.fitBounds([[40, 19], [82, 190]], { padding: [35, 60], animate: false });
    else if (zoomAction.kind === "city") focusCity(map, city, mode === "cargo");
    else if (zoomAction.kind === "in") map.zoomIn();
    else map.zoomOut();
  }, [zoomAction, city, mode]);
  return (
    <>
      <div
        ref={host}
        id="mapCity"
        className="map-layer"
        aria-label="Карта России и городов"
        data-map-level={level}
      />
      {tileFailed && (
        <div className="panelc stmsg unk tile-note" role="status">
          Подложка OpenStreetMap не загрузилась. Слои демонстрационных данных
          остаются доступны.
        </div>
      )}
    </>
  );
}
