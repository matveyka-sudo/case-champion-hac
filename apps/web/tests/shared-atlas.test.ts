import { createElement } from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AtlasHeader } from "../src/widgets/AtlasHeader";
import { AtlasShell } from "../src/widgets/AtlasShell";
import { TimeDockView } from "../src/widgets/TimeDockView";
import { CategoryChoices } from "../src/widgets/CategoryChoices";
import { FiltersPanelView } from "../src/widgets/FiltersPanelView";
import { AgentChatView } from "../src/widgets/AgentChatView";
afterEach(cleanup);
describe("shared atlas presentation", () => {
  it("makes closed mobile filters inert without hiding the desktop controller", () => {
    const props = { collapsed: false, mobileOpen: false, mobile: true, onClose: vi.fn(), onToggle: vi.fn(), onExpand: vi.fn(), railIcons: [], footer: "Run", children: "Fields" };
    const view = render(createElement(FiltersPanelView, props));
    expect(view.container.querySelector("aside")?.hasAttribute("inert")).toBe(true);
    view.rerender(createElement(FiltersPanelView, { ...props, mobile: false }));
    expect(view.container.querySelector("aside")?.hasAttribute("inert")).toBe(false);
  });

  it("separates life tools and the transport navigation", () => {
    const onModeChange = vi.fn(), onLife = vi.fn();
    render(createElement(AtlasHeader, { mode: "city", title: "Москва", theme: "light", onModeChange, onLife,
      onThemeChange: vi.fn(), onFilters: vi.fn(), chatOpen: false, onChat: vi.fn() }));
    fireEvent.click(screen.getByRole("button", { name: "Для жизни" }));
    expect(onLife).toHaveBeenCalledOnce();
    expect(screen.getByRole("button", { name: "Транспортные связи" }).getAttribute("aria-expanded")).toBe("false");
    expect(screen.queryByRole("tablist")).toBeNull();
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
    fireEvent.click(screen.getAllByRole("button", { name: "Высокий приоритет" })[1]);
    expect(onPriority).not.toHaveBeenCalled();
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
  it("shares the original chat shell while suggestions only populate input and Enter sends explicitly", () => {
    const onSuggestion = vi.fn(), onSend = vi.fn();
    const view = render(createElement(AgentChatView, { open: true, onClose: vi.fn(), onNewDialog: vi.fn(), subtitle: "Saved result",
      contextLabel: "Москва", contextRows: [{ label: "Результат", value: "Частичный" }], messages: null, hasMessages: false,
      suggestions: ["Объясни результат"], onSuggestion, draft: "Вопрос", onDraftChange: vi.fn(), onSend, disabled: false, busy: false, note: "Saved facts only" }));
    expect(view.container.querySelector(".at-chat>.ch-in>.ch-head")).not.toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Объясни результат" }));
    expect(onSuggestion).toHaveBeenCalledWith("Объясни результат");
    expect(onSend).not.toHaveBeenCalled();
    fireEvent.keyDown(screen.getByRole("textbox", { name: "Вопрос агенту" }), { key: "Enter" });
    expect(onSend).toHaveBeenCalledOnce();
  });
  it("cannot send through Enter while disabled, busy, empty, or beyond the API length limit", () => {
    const onSend = vi.fn();
    const props = { open: true, onClose: vi.fn(), onNewDialog: vi.fn(), subtitle: "", contextLabel: "Москва", contextRows: [], messages: null,
      hasMessages: false, suggestions: [], onSuggestion: vi.fn(), onDraftChange: vi.fn(), onSend, note: "", maxLength: 2000 };
    const view = render(createElement(AgentChatView, { ...props, draft: "Вопрос", disabled: true, busy: false }));
    for (const patch of [{ draft: "Вопрос", disabled: true, busy: false }, { draft: "Вопрос", disabled: false, busy: true },
      { draft: " ", disabled: false, busy: false }, { draft: "я".repeat(2001), disabled: false, busy: false }]) {
      view.rerender(createElement(AgentChatView, { ...props, ...patch }));
      fireEvent.keyDown(screen.getByRole("textbox", { name: "Вопрос агенту" }), { key: "Enter" });
    }
    expect(onSend).not.toHaveBeenCalled();
    expect(screen.getByRole("textbox", { name: "Вопрос агенту" }).getAttribute("maxlength")).toBe("2000");
  });
});
