import { createElement } from "react";
import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { IndexLegend } from "../src/widgets/IndexLegend";
afterEach(cleanup);

describe("index legend", () => {
  it("explains every color threshold accessibly and identifies missing indices", () => {
    render(createElement(IndexLegend));
    const legend = screen.getByRole("group", { name: "Шкала индекса от 0 до 100" });
    const ranges = within(legend).getByRole("list", { name: "Цветовые диапазоны индекса" });
    expect(within(ranges).getAllByRole("listitem")).toHaveLength(4);
    expect(within(ranges).getByRole("listitem", { name: "Красный: индекс меньше 50" }).textContent).toBe("<50");
    expect(within(ranges).getByRole("listitem", { name: "Янтарный: индекс от 50 до менее 75" }).textContent).toBe("50–<75");
    expect(within(ranges).getByRole("listitem", { name: "Зелёный: индекс от 75 до 100" }).textContent).toBe("75–100");
    expect(within(ranges).getByRole("listitem", { name: "Серый: индекс отсутствует" }).textContent).toBe("Нет индекса");
    expect(legend.querySelectorAll('[aria-hidden="true"]')).toHaveLength(4);
  });
});
