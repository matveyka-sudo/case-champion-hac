import { createElement } from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AtlasHeader } from "../src/widgets/AtlasHeader";
import { AtlasShell } from "../src/widgets/AtlasShell";
import { TimeDockView } from "../src/widgets/TimeDockView";
afterEach(cleanup);
describe("shared atlas presentation", () => {
  it("uses the incumbent three tabs and keyboard navigation without a data source dependency", () => {
    const onModeChange = vi.fn();
    render(createElement(AtlasHeader, { mode: "city", title: "Москва", theme: "light", onModeChange,
      onThemeChange: vi.fn(), onFilters: vi.fn(), chatOpen: false, onChat: vi.fn() }));
    expect(screen.getAllByRole("tab")).toHaveLength(3);
    fireEvent.keyDown(screen.getByRole("tablist"), { key: "ArrowRight" });
    expect(onModeChange).toHaveBeenCalledWith("pax");
  });
  it("keeps the same shell slots and original time dock with controller-owned values", () => {
    const onSlotChange = vi.fn(), run = vi.fn();
    const dock = createElement(TimeDockView, { date: "2027-02-03", slot: 36, timezoneLabel: "Europe/Moscow",
      min: "2027-02-01", max: "2027-02-28", onDateChange: vi.fn(), onSlotChange, run, disabled: true });
    const view = render(createElement(AtlasShell, { header: createElement("header", null, "Header"), children: dock }));
    expect(view.container.querySelector(".atlas>.at-body>.timedock")).not.toBeNull();
    expect(screen.getByRole("slider").getAttribute("aria-valuetext")).toBe("09:00, Europe/Moscow");
    fireEvent.keyDown(screen.getByRole("slider"), { key: "PageUp" });
    expect(onSlotChange).toHaveBeenCalledWith(40);
    expect(screen.getByRole("button", { name: "Рассчитать" })).toHaveProperty("disabled", true);
    expect(run).not.toHaveBeenCalled();
  });
});
