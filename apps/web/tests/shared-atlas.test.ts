import { createElement } from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AtlasHeader } from "../src/widgets/AtlasHeader";
import { AtlasShell } from "../src/widgets/AtlasShell";
import { TimeDockView } from "../src/widgets/TimeDockView";
import { CategoryChoices } from "../src/widgets/CategoryChoices";
import { FiltersPanelView } from "../src/widgets/FiltersPanelView";
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
  it("shares the original category buttons and priorities while honoring canonical disabled reasons", () => {
    const onToggle = vi.fn(), onPriority = vi.fn();
    render(createElement(CategoryChoices, { items: [{ id: "real-category", name: "Поликлиники" }, { id: "unsupported", name: "Аэропорты", disabled: true, reason: "Расчёт не зарегистрирован" }],
      selected: { "real-category": 3 }, onToggle, onPriority }));
    expect(screen.getByRole("button", { name: "Поликлиники" }).getAttribute("aria-pressed")).toBe("true");
    fireEvent.click(screen.getByRole("button", { name: "Поликлиники" }));
    expect(onToggle).toHaveBeenCalledWith("real-category");
    expect(screen.getByRole("button", { name: "Аэропорты" })).toHaveProperty("disabled", true);
    fireEvent.click(screen.getAllByRole("button", { name: "Средний приоритет" })[0]);
    expect(onPriority).toHaveBeenCalledWith("real-category", 2);
  });
  it("keeps the incumbent filter header scroll and footer structure", () => {
    const view = render(createElement(FiltersPanelView, { collapsed: false, mobileOpen: false, onClose: vi.fn(), onToggle: vi.fn(), onExpand: vi.fn(), railIcons: ["map-pin"],
      footer: "Run", children: "Fields" }));
    expect(view.container.querySelector(".at-filters>.f-head")).not.toBeNull();
    expect(view.container.querySelector(".at-filters>.f-scroll")?.textContent).toBe("Fields");
    expect(view.container.querySelector(".at-filters>.f-foot")?.textContent).toBe("Run");
  });
});
