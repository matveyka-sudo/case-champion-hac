import type { CSSProperties } from "react";
export const css = (value: Record<string, string | number>): CSSProperties =>
  value as CSSProperties;
export const f1 = (value: number | null | undefined) =>
  value == null
    ? "—"
    : new Intl.NumberFormat("ru-RU", {
        minimumFractionDigits: 1,
        maximumFractionDigits: 1,
      }).format(value);
export const f0 = (value: number) =>
  new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 0 }).format(value);
export const roundPop = (v: number) => Math.round(v / 100) * 100;
export const timeLabel = (t: number) =>
  `${String(Math.floor(t / 4)).padStart(2, "0")}:${String((t % 4) * 15).padStart(2, "0")}`;
