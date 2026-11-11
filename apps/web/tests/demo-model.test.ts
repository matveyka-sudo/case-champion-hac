import { createElement } from "react";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { demoModelWarning, isDemoModelSnapshot } from "../src/api/adapters/demoModel";
import { DemoModelBanner } from "../src/widgets/DemoModelBanner";
afterEach(cleanup);
describe("explicit generated transport demo identity", () => {
  it.each([undefined, null, "", "moscow-urban-2026-11-10.3", "kaliningrad-urban-2026-11-10.5", "other-demo-moscow-a", "demo-moscow-"])("does not classify ordinary/unknown snapshot %s as generated demo", (id) => {
    expect(isDemoModelSnapshot(id)).toBe(false);
  });
  it("keeps the warning for a saved demo after draft selection changes and distinguishes a pending demo selection", () => {
    const view = render(createElement(DemoModelBanner, { selectedSnapshotId: "real-moscow", savedSnapshotId: "demo-moscow-synthetic", hasSavedResult: true }));
    expect(screen.getByText(demoModelWarning)).toBeTruthy();
    expect(screen.getByText(/Показатели сохранённого анализа относятся к демонстрационным/)).toBeTruthy();
    view.rerender(createElement(DemoModelBanner, { selectedSnapshotId: "demo-moscow-synthetic", savedSnapshotId: "real-moscow", hasSavedResult: true }));
    expect(screen.getByText(/На карте сохранён прежний результат/)).toBeTruthy();
    view.rerender(createElement(DemoModelBanner, { selectedSnapshotId: "real-moscow", savedSnapshotId: "real-moscow", hasSavedResult: true }));
    expect(screen.queryByRole("note")).toBeNull();
  });
});
