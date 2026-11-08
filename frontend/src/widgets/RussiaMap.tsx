import { useEffect, useRef, useState } from "react";
import * as d3 from "d3";
import { feature } from "topojson-client";
import type { Topology, GeometryCollection } from "topojson-specification";
import type { Feature, Geometry, MultiPolygon, Position } from "geojson";
import type { CityId } from "../mocks/model";
import { catalog as fixtures } from "../mocks/catalog";
import { useAtlas } from "../features/state";
import { useReducedMotion } from "../features/motion";
let landPromise: Promise<Feature<MultiPolygon>> | undefined;
function loadLand() {
  return (landPromise ||= fetch("/data/land-50m.json")
    .then((r) => {
      if (!r.ok) throw new Error("Не удалось загрузить геометрию");
      return r.json() as Promise<Topology<{ land: GeometryCollection }>>;
    })
    .then((t) => {
      const f = feature(t, t.objects.land);
      const all = f.features.flatMap((x) =>
        x.geometry.type === "MultiPolygon"
          ? x.geometry.coordinates
          : x.geometry.type === "Polygon"
            ? [x.geometry.coordinates]
            : [],
      );
      return {
        type: "Feature",
        properties: {},
        geometry: {
          type: "MultiPolygon",
          coordinates: all.filter((p) => {
            const f: Feature<Geometry> = {
              type: "Feature",
              properties: {},
              geometry: { type: "Polygon", coordinates: p },
            };
            const c = d3.geoCentroid(f),
              b = d3.geoBounds(f);
            return c[1] > 15 && b[1][1] > 20 && (c[0] > -15 || c[0] < -150);
          }),
        },
      } as Feature<MultiPolygon>;
    }));
}
const fit: d3.GeoPermissibleObjects = {
  type: "MultiPoint",
  coordinates: [
    [19.8, 54.4],
    [33, 69.5],
    [60, 76.5],
    [105, 77.8],
    [140, 74],
    [178, 70],
    [-172, 66],
    [160, 51],
    [132, 42.6],
    [100, 49.5],
    [60, 50.5],
    [47, 41.6],
    [37.5, 44.2],
  ],
};
const projection = (
  w: number,
  h: number,
  p: { l: number; r: number; t: number; b: number },
) =>
  d3
    .geoAzimuthalEqualArea()
    .rotate([-100, -62])
    .clipAngle(78)
    .fitExtent(
      [
        [p.l, p.t],
        [w - p.r, h - p.b],
      ],
      fit,
    );
function arc(a: Position, b: Position, bend = 0.18) {
  return `M${a[0]},${a[1]} Q${(a[0] + b[0]) / 2 - (b[1] - a[1]) * bend},${(a[1] + b[1]) / 2 - (b[0] - a[0]) * bend} ${b[0]},${b[1]}`;
}
export function PreviewMap() {
  const ref = useRef<SVGSVGElement>(null);
  useEffect(() => {
    let alive = true;
    loadLand()
      .then((land) => {
        if (!alive || !ref.current) return;
        const svg = d3.select(ref.current),
          proj = projection(800, 460, { l: 20, r: 20, t: 30, b: 30 }),
          path = d3.geoPath(proj);
        svg.selectAll("*").remove();
        svg
          .append("path")
          .attr("d", path(d3.geoGraticule10()))
          .attr("fill", "none")
          .attr("stroke", "rgba(47,91,255,.10)");
        svg
          .append("path")
          .attr("d", path(land))
          .attr("fill", "#fff")
          .attr("stroke", "#C7CCEA")
          .attr("stroke-width", 0.7);
        fixtures.referenceCities.forEach(([, lon, lat]) => {
          const p = proj([lon, lat])!;
          svg
            .append("circle")
            .attr("cx", p[0])
            .attr("cy", p[1])
            .attr("r", 2.2)
            .attr("fill", "#9AA0C6");
        });
        const a = proj(fixtures.places.msk.ll)!,
          b = proj(fixtures.places.kgd.ll)!;
        [
          [9, 0.15],
          [3, 1],
        ].forEach(([weight, opacity]) =>
          svg
            .append("path")
            .attr("d", arc(a, b, 0.28))
            .attr("fill", "none")
            .attr("stroke", "#2F5BFF")
            .attr("stroke-width", weight)
            .attr("opacity", opacity)
            .attr("stroke-linecap", "round"),
        );
        [
          [a, "Москва"],
          [b, "Калининград"],
        ].forEach(([point, n]) => {
          const p = point as number[];
          svg
            .append("circle")
            .attr("cx", p[0])
            .attr("cy", p[1])
            .attr("r", 7)
            .attr("fill", "#fff")
            .attr("stroke", "#2F5BFF")
            .attr("stroke-width", 3);
          svg
            .append("text")
            .attr("x", p[0] + 10)
            .attr("y", p[1] - 10)
            .text(n as string)
            .attr("font-size", 13)
            .attr("font-weight", 800)
            .attr("fill", "#11152B")
            .attr("font-family", "Manrope")
            .attr("paint-order", "stroke")
            .attr("stroke", "#fff")
            .attr("stroke-width", 4);
        });
        const n = proj(fixtures.places.nvr.ll)!;
        svg
          .append("circle")
          .attr("cx", n[0])
          .attr("cy", n[1])
          .attr("r", 5)
          .attr("fill", "#7A52FF");
      })
      .catch(() => {});
    return () => {
      alive = false;
    };
  }, []);
  return (
    <svg
      ref={ref}
      id="pvMap"
      viewBox="0 0 800 460"
      preserveAspectRatio="xMidYMid meet"
    />
  );
}
export function RussiaMap({
  zoomAction,
}: {
  zoomAction: { kind: string; id: number };
}) {
  const host = useRef<HTMLDivElement>(null),
    svgRef = useRef<SVGSVGElement>(null),
    zoomRef = useRef<d3.ZoomBehavior<SVGSVGElement, unknown> | null>(null),
    view = useRef<{ key: string; transform: d3.ZoomTransform; w: number; h: number } | null>(null);
  const [land, setLand] = useState<Feature<MultiPolygon> | null>(null),
    [failed, setFailed] = useState(false),
    [size, setSize] = useState({ w: 1000, h: 700 });
  const { state: s, dispatch } = useAtlas();
  const reduced = useReducedMotion();
  useEffect(() => {
    let alive = true;
    loadLand()
      .then((f) => {
        if (alive) setLand(f);
      })
      .catch(() => {
        if (alive) setFailed(true);
      });
    return () => {
      alive = false;
    };
  }, []);
  useEffect(() => {
    if (!host.current) return;
    const ro = new ResizeObserver((entries) => {
      const r = entries[0].contentRect;
      setSize({ w: r.width, h: r.height });
    });
    ro.observe(host.current);
    return () => ro.disconnect();
  }, []);
  const p = s.p;
  useEffect(() => {
    const node = svgRef.current;
    if (!node || !land || !size.w || !size.h) return;
    const { w, h } = size;
    const narrow = w < 700;
    const pad = {
      l: narrow ? 8 : 40,
      r: narrow ? 8 : window.innerWidth < 768 ? 40 : 390,
      t: narrow ? 90 : 96,
      b: narrow ? 200 : 120,
    };
    if (w - pad.l - pad.r < 320) pad.r = 20;
    const proj = projection(w, h, pad),
      path = d3.geoPath(proj),
      svg = d3.select(node);
    svg.selectAll("*").remove();
    let pair: [number[], number[]] | null = null;
    const g = svg.append("g").attr("class", "zr");
    g.append("path")
      .attr("class", "grat")
      .attr("d", path(d3.geoGraticule().step([10, 10])()));
    g.append("path").attr("class", "land").attr("d", path(land));
    fixtures.seas.forEach(([name, lon, lat]) => {
      const point = proj([lon, lat]);
      if (point)
        g.append("text")
          .attr("class", "sea-label")
          .attr("x", point[0])
          .attr("y", point[1])
          .attr("text-anchor", "middle")
          .text(name);
    });
    fixtures.referenceCities.forEach(([name, lon, lat]) => {
      const point = proj([lon, lat]);
      if (point)
        g.append("circle")
          .attr("class", "cdot")
          .attr("cx", point[0])
          .attr("cy", point[1])
          .attr("r", 2.6)
          .append("title")
          .text(name + " — в справочнике; городской расчёт не выполнялся");
    });
    const addNode = (
      coords: [number, number],
      title: string,
      sub: string,
      city?: CityId,
    ) => {
      const point = proj(coords);
      if (!point) return;
      const n = g
        .append("g")
        .attr("class", "hub")
        .attr("transform", `translate(${point})`)
        .attr("role", city ? "button" : "img")
        .attr("tabindex", city ? 0 : -1)
        .attr("aria-label", title + (sub ? " · " + sub : ""))
        .datum({ point, rot: 0 });
      n.append("circle").attr("r", 20).attr("class", "halo");
      n.append("circle").attr("r", 8).attr("class", "core");
      n.append("text").attr("x", 15).attr("y", -4).text(title);
      n.append("text")
        .attr("x", 15)
        .attr("y", 12)
        .attr("class", "sub")
        .text(sub);
      if (city) {
        const select = () => dispatch({ type: "city", city });
        n.on("click", select).on("keydown", (event) => {
          if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            select();
          }
        });
      }
    };
    if (p.mode === "city") {
      (["msk", "kgd", "nvr"] as const).forEach((key) =>
        addNode(
          fixtures.places[key].ll,
          fixtures.places[key].n,
          key === "msk"
            ? "демо-сетка 1 км"
            : key === "kgd"
              ? "демо-сетка 1 км"
              : "нет данных в демо",
          key,
        ),
      );
    } else {
      const ak = p.mode === "pax" ? p.paxA : p.cargoA,
        bk = p.mode === "pax" ? p.paxB : p.cargoB;
      const A = fixtures.places[ak],
        B = fixtures.places[bk];
      const aa =
          p.mode === "pax" && p.paxScale === "subj" && A.capll ? A.capll : A.ll,
        bb =
          p.mode === "pax" && p.paxScale === "subj" && B.capll ? B.capll : B.ll;
      const a = proj(aa)!,
        b = proj(bb)!;
      pair = [a, b];
      g.append("path")
        .attr("class", "arc-glow")
        .attr("d", arc(a, b, 0.25));
      g.append("path")
        .attr("class", "arc")
        .attr("d", arc(a, b, 0.25));
      const temp = g
        .append("path")
        .attr("d", arc(a, b, 0.25))
        .attr("fill", "none")
        .attr("stroke", "none");
      const node = temp.node()!,
        len = node.getTotalLength(),
        m0 = node.getPointAtLength(len * 0.52),
        m1 = node.getPointAtLength(len * 0.56),
        rot = (Math.atan2(m1.y - m0.y, m1.x - m0.x) * 180) / Math.PI;
      g.append("g")
        .attr("class", "node-g")
        .datum({ point: [m1.x, m1.y], rot })
        .attr("transform", `translate(${m1.x},${m1.y}) rotate(${rot})`)
        .append("path")
        .attr("d", "M-8,-7 L8,0 L-8,7 Z")
        .style("fill", "var(--accent)")
        .style("stroke", "var(--surface)")
        .style("stroke-width", 2);
      temp.remove();
      addNode(aa, "А · " + (p.paxScale === "subj" ? A.cap : A.n), A.tz);
      addNode(bb, "Б · " + (p.paxScale === "subj" ? B.cap : B.n), B.tz);
      if (
        p.mode === "cargo" &&
        p.cargoModes.pipe &&
        p.profile.handling === "liquid"
      )
        g.append("path")
          .attr("class", "arc pipe")
          .attr("d", arc(a, b, -0.12));
    }
    const key = JSON.stringify([p.mode, p.paxA, p.paxB, p.paxScale, p.cargoA, p.cargoB]);
    const previous = view.current;
    const zoom = d3
      .zoom<SVGSVGElement, unknown>()
      .scaleExtent([1, 8])
      .translateExtent([
        [-w * 0.2, -h * 0.2],
        [w * 1.2, h * 1.2],
      ])
      .on("zoom", (event) => {
        view.current = { key, transform: event.transform, w, h };
        g.attr("transform", event.transform);
        const k = event.transform.k;
        g.selectAll<SVGGElement, { point: number[]; rot: number }>(
          ".hub,.node-g",
        ).attr("transform", function () {
          const d = d3.select(this).datum() as { point: number[]; rot: number };
          return `translate(${d.point}) scale(${1 / k}) rotate(${d.rot})`;
        });
        g.selectAll(".sea-label").style("font-size", `${12 / k}px`);
        g.selectAll(".cdot").attr("r", 2.6 / Math.sqrt(k));
      });
    zoomRef.current = zoom;
    svg.call(zoom).on("dblclick.zoom", null);
    if (previous?.key === key) {
      svg.call(zoom.transform, d3.zoomIdentity
        .translate(previous.transform.x * w / previous.w, previous.transform.y * h / previous.h)
        .scale(previous.transform.k));
    } else if (pair) {
      const [a, b] = pair as [number[], number[]];
      const x0 = Math.min(a[0], b[0]),
        x1 = Math.max(a[0], b[0]),
        y0 = Math.min(a[1], b[1]),
        y1 = Math.max(a[1], b[1]),
        vw = w - pad.l - pad.r,
        vh = h - pad.t - pad.b;
      const k = Math.max(
        1,
        Math.min(
          5,
          Math.min(
            vw / Math.max(60, (x1 - x0) * 2.2),
            vh / Math.max(60, (y1 - y0) * 2.6),
          ),
        ),
      );
      svg.call(
        zoom.transform,
        d3.zoomIdentity
          .translate(
            pad.l + vw / 2 - ((x0 + x1) / 2) * k,
            pad.t + vh / 2 - ((y0 + y1) / 2) * k,
          )
          .scale(k),
      );
    }
    if (!pair && previous?.key !== key) svg.call(zoom.transform, d3.zoomIdentity);
    return () => {
      svg.on(".zoom", null);
      svg.interrupt();
    };
  }, [
    land,
    size,
    p.mode,
    p.paxA,
    p.paxB,
    p.paxScale,
    p.cargoA,
    p.cargoB,
    p.cargoModes.pipe,
    p.profile.handling,
    dispatch,
  ]);
  useEffect(() => {
    if (!svgRef.current || !zoomRef.current) return;
    const svg = d3.select(svgRef.current),
      z = zoomRef.current;
    if (zoomAction.kind === "home")
      svg
        .transition()
        .duration(reduced ? 0 : 350)
        .call(z.transform, d3.zoomIdentity);
    else
      svg
        .transition()
        .duration(reduced ? 0 : 250)
        .call(z.scaleBy, zoomAction.kind === "in" ? 1.5 : 1 / 1.5);
  }, [zoomAction, reduced]);
  return (
    <div
      ref={host}
      id="mapRussia"
      className="map-layer"
      role="application"
      aria-label="Карта России"
    >
      {failed ? (
        <div className="map-fallback">Не удалось загрузить контур карты</div>
      ) : (
        <svg
          ref={svgRef}
          viewBox={`0 0 ${size.w} ${size.h}`}
          role="img"
          aria-label="Карта России: суша, города-узлы и выбранные связи"
        />
      )}
    </div>
  );
}
