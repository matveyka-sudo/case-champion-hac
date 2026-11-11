import type { RefObject } from "react";
import { demoModelWarning, isDemoModel } from "../api/adapters/demoModel";

export function DemoModelBanner({ selectedLimitations, savedLimitations, hasSavedResult, bannerRef }: {
  selectedLimitations?: readonly string[];
  savedLimitations?: readonly string[];
  hasSavedResult: boolean;
  bannerRef?: RefObject<HTMLElement | null>;
}) {
  const selected = isDemoModel({ limitations: selectedLimitations }), saved = isDemoModel({ limitations: savedLimitations });
  if (!selected && !saved) return null;
  return (
    <section className="api-demo-banner" role="note" aria-label="Демонстрационная модель" ref={bannerRef}>
      <strong>{demoModelWarning}</strong>
      <details><summary>Подробнее</summary><span>{saved ? "Показатели сохранённого анализа относятся к демонстрационным входным данным."
        : `Демонстрационный набор выбран для следующего расчёта.${hasSavedResult ? " На карте сохранён прежний результат." : ""}`}</span></details>
    </section>
  );
}
