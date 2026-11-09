import { useState, type KeyboardEvent } from "react";
import { catalog as fixtures } from "../mocks/catalog";
import { Icon } from "../shared/ui";
import { css } from "../shared/format";
const interpolate = (value: number) => {
  const colors = [
    "#EAE4FF",
    "#C6B6FF",
    "#9A86F7",
    "#6A5AE8",
    "#3D43CC",
    "#1E2A8E",
  ];
  const t = Math.max(0, Math.min(99.9, value)) / 20,
    i = Math.floor(t),
    f = t - i;
  const rgb = (h: string) =>
      [1, 3, 5].map((k) => parseInt(h.slice(k, k + 2), 16)),
    a = rgb(colors[i]),
    b = rgb(colors[i + 1]);
  return `rgb(${a.map((n, k) => Math.round(n + (b[k] - n) * f)).join(",")})`;
};
export function MiniGrid({
  cols,
  rows,
  detailed = false,
  small = false,
}: {
  cols: number;
  rows: number;
  detailed?: boolean;
  small?: boolean;
}) {
  return (
    <div
      className={`minigrid ${small ? "sm" : ""}`}
      id={small ? "minigrid2" : "minigrid"}
      aria-hidden="true"
      style={
        small
          ? {
              gridTemplateColumns: "repeat(8,minmax(0,1fr))",
              gap: 3,
              margin: 0,
              width: "60%",
            }
          : undefined
      }
    >
      {Array.from({ length: cols * rows }, (_, i) => {
        const x = i % cols,
          y = Math.floor(i / cols),
          v =
            92 -
            Math.hypot(x - cols * 0.36, y - rows * 0.45) * 9 +
            10 * Math.sin(x * 0.9) * Math.cos(y * 1.1) -
            (Math.hypot(x - cols * 0.78, y - rows * 0.7) < 1.8 ? 30 : 0);
        const k = (x * 7 + y * 3) % 23;
        return (
          <i
            key={i}
            className={
              detailed && (k === 5 || (x === cols - 1 && y < 2))
                ? "nd"
                : detailed &&
                    x === Math.round(cols * 0.36) &&
                    y === Math.round(rows * 0.45)
                  ? "obj"
                  : ""
            }
            style={css({ "--c": interpolate(v), "--d": x + y * 2 })}
          />
        );
      })}
    </div>
  );
}
export function TransportShowcase({ group }: { group: string }) {
  const items = fixtures.transport[group];
  const [choice, setChoice] = useState("pt");
  const selected = items.find((x) => x.k === choice)?.k || items[0].k;
  const keyboard = (event: KeyboardEvent<HTMLDivElement>) => {
    if (!["ArrowUp", "ArrowDown", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    const i = items.findIndex((x) => x.k === selected);
    const next =
      event.key === "Home"
        ? 0
        : event.key === "End"
          ? items.length - 1
          : (i + (event.key === "ArrowDown" ? 1 : -1) + items.length) %
            items.length;
    setChoice(items[next].k);
    document.getElementById(`tr-${items[next].k}`)?.focus();
  };
  return (
    <div className={`tr-stage${group === "cargo" ? " tr-stage-cargo" : ""}`}>
      <div
        className="tr-photo"
        id="trPhoto"
        role="tabpanel"
        aria-labelledby={`tr-${selected}`}
      >
        {items.map((x) => (
          <figure
            key={x.k}
            className={x.k === selected ? "on" : ""}
            aria-hidden={x.k !== selected}
          >
            {x.img ? (
              <img
                src={`/${x.img}`}
                width={x.w}
                height={x.h}
                alt={x.alt}
                loading="lazy"
                decoding="async"
                style={{ objectPosition: x.pos || "center" }}
              />
            ) : (
              <div className="ph-slot">
                <Icon name={x.icon} />
                <small>Место для фото</small>
                <span className="t">{x.ph}</span>
              </div>
            )}
            <figcaption>
              <b>{x.t}</b>
              <span>{x.s}</span>
            </figcaption>
          </figure>
        ))}
      </div>
      <div
        className="tr-list"
        id="trList"
        role="tablist"
        aria-orientation="vertical"
        onKeyDown={keyboard}
      >
        {items.map((x) => (
          <button
            key={x.k}
            className="tr-item"
            role="tab"
            id={`tr-${x.k}`}
            aria-selected={x.k === selected}
            tabIndex={x.k === selected ? 0 : -1}
            onClick={() => setChoice(x.k)}
          >
            <span className="ic">
              <Icon name={x.icon} />
            </span>
            <span>
              <b>{x.t}</b>
              <small>{x.s}</small>
            </span>
            <span className="go">
              <Icon name="arrow-right" />
            </span>
          </button>
        ))}
        <p className="tr-note">
          Фото показывают вид транспорта, а не подключённые данные.
        </p>
      </div>
    </div>
  );
}
