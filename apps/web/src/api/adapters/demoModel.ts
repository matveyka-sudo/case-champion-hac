// Explicit server snapshot namespace approved for the generated Moscow demo.
const demoPrefix = "demo-moscow-";
export const demoModelWarning = "Демонстрационная транспортная модель — не оценка фактической доступности Москвы";
export function isDemoModelSnapshot(snapshotId?: string | null) {
  return !!snapshotId && snapshotId.startsWith(demoPrefix) && snapshotId.length > demoPrefix.length;
}
