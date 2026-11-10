import type { RefObject } from "react";
import { demoModelWarning, isDemoModelSnapshot } from "../api/adapters/demoModel";

export function DemoModelBanner({ selectedSnapshotId, savedSnapshotId, hasSavedResult, bannerRef }: {
  selectedSnapshotId?: string;
  savedSnapshotId?: string;
  hasSavedResult: boolean;
  bannerRef?: RefObject<HTMLElement | null>;
}) {
  const selected = isDemoModelSnapshot(selectedSnapshotId), saved = isDemoModelSnapshot(savedSnapshotId);
  if (!selected && !saved) return null;
  return (
    <section className="api-demo-banner" role="note" aria-label="Демонстрационная модель" ref={bannerRef}>
      <strong>{demoModelWarning}</strong>
      <span>{saved ? "Показатели сохранённого анализа относятся к демонстрационным входным данным."
        : `Демонстрационный набор выбран для следующего расчёта.${hasSavedResult ? " На карте сохранён прежний результат." : ""}`}</span>
    </section>
  );
}
