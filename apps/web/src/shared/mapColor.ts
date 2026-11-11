export function mapPalette() {
  const style = getComputedStyle(document.documentElement);
  return Array.from({ length: 6 }, (_, i) =>
    style.getPropertyValue("--scale-" + i).trim(),
  );
}
export function mapColor(v: number | null, scale = mapPalette()) {
  if (v == null) return "#999";
  const t = Math.max(0, Math.min(100, v)) / 20,
    i = Math.min(4, Math.floor(t)),
    f = t - i;
  const rgb = (s: string) =>
    [1, 3, 5].map((k) => parseInt(s.slice(k, k + 2), 16));
  const a = rgb(scale[i]),
    b = rgb(scale[i + 1]);
  return `rgb(${a.map((n, k) => Math.round(n + (b[k] - n) * f)).join(",")})`;
}
