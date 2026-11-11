import { expect, test } from "@playwright/test";

test("training atlas displays only the selected model output and keeps comparisons reversible", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.route("**/api/v1/**", (route) => route.abort());
  await page.goto("/?data=training#atlas");
  await expect(page.getByRole("heading", { name: "Учебный пример" })).toBeVisible();
  await page.getByRole("button", { name: "Показать учебный расчёт" }).click();
  await page.getByRole("button", { name: "Сравнить согласование пересадки" }).click();
  await expect(page.getByRole("status")).toContainText("45 мин → 37 мин");
  await page.getByRole("button", { name: "Вернуться к базе" }).click();
  await expect(page.getByRole("status")).toHaveCount(0);

  const result = page.getByRole("region", { name: "Результат учебного примера" });
  const filter = page.getByRole("combobox", { name: "Город или пара" });
  await page.getByRole("combobox", { name: "Направление" }).selectOption("passenger");
  await expect(result).toHaveCount(0);
  await expect(filter.locator("option")).toHaveCount(6);
  await filter.selectOption("passenger:kaliningrad:novorossiysk");
  await page.getByRole("button", { name: "Показать учебный расчёт" }).click();
  await expect(result.getByRole("heading")).toHaveText("Калининград → Новороссийск");
  await expect(result).toContainText("Ожидание пересадки");
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});

test("training preserves compatible real filters and changes outputs when category or mode changes", async ({ page }) => {
  await page.goto("/?data=training&kind=urban_passenger&city=novorossiysk&travel=car&category=school#atlas");
  await expect(page.getByRole("combobox", { name: "Город или пара" })).toHaveValue("urban:novorossiysk");
  await expect(page.getByRole("combobox", { name: "Передвижение" })).toHaveValue("private_car");
  await expect(page.getByRole("combobox", { name: "Категория" })).toHaveValue("school");
  const result = page.getByRole("region", { name: "Результат учебного примера" });
  const show = page.getByRole("button", { name: "Показать учебный расчёт" });
  await show.click();
  const before = await result.locator("dd").first().innerText();
  await page.getByRole("combobox", { name: "Передвижение" }).selectOption("walk");
  await expect(result).toHaveCount(0);
  await show.click();
  await expect(result.locator("dd").first()).not.toHaveText(before);
  await page.getByRole("combobox", { name: "Направление" }).selectOption("delivery");
  await expect(page.getByRole("combobox", { name: "Город или пара" }).locator("option")).toHaveCount(6);
});
