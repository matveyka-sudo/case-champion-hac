const stops = [
  [0, [181, 42, 70]],
  [28, [225, 99, 64]],
  [50, [231, 169, 57]],
  [73, [136, 165, 62]],
  [100, [9, 126, 97]],
] as const;

export const unknownIndexColor = "#8990a7";
export const indexGradient = `linear-gradient(90deg in srgb, ${stops.map(([stop, rgb]) => `rgb(${rgb.join(" ")}) ${stop}%`).join(", ")})`;

export function indexColor(value: number | null) {
  if (value === null || !Number.isFinite(value)) return unknownIndexColor;
  const index = Math.min(100, Math.max(0, value));
  const next = Math.max(1, stops.findIndex(([stop]) => index <= stop));
  const [start, left] = stops[next - 1];
  const [end, right] = stops[next];
  const fraction = (index - start) / (end - start);
  return `rgb(${left.map((channel, i) => Math.round(channel + (right[i] - channel) * fraction)).join(" ")})`;
}
