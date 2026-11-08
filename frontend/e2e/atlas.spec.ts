import { test, expect } from "@playwright/test";
// Stable network fixtures: analytic layers do not depend on a public tile service.
test.beforeEach(async ({ page }) => {
  await page.route("https://tile.openstreetmap.org/**", (route) =>
    route.fulfill({
      status: 200,
      contentType: "image/png",
      body: Buffer.from(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aCMsAAAAASUVORK5CYII=",
        "base64",
      ),
    }),
  );
});
test("landing, transport tabs, anchors and browser navigation", async ({
  page,
}) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { level: 1 })).toContainText(
    "Связи территорий.",
  );
  await page.getByRole("tab", { name: "Грузы", exact: true }).click();
  await expect(page.getByRole("tab", { name: /Трубопроводный/ })).toBeVisible();
  await page
    .locator("#lpHeader")
    .getByRole("link", { name: /Открыть атлас/ })
    .click();
  await expect(page.locator("#atlas")).toBeVisible();
  await page.goBack();
  await expect(page.locator("#landing")).toBeVisible();
});
test("city selection, states and time never invent fresh values", async ({
  page,
}) => {
  await page.goto("/#atlas");
  await page
    .locator("#filters")
    .getByRole("button", { name: "Москва", exact: true })
    .click();
  await expect(page.locator(".ctx .bignum")).toContainText("44");
  await page.locator("#timedock input[type=range]").fill("80");
  await expect(page.locator(".status")).toContainText("Параметры изменены");
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await expect(page.locator(".ctx .bignum")).toContainText("44");
  await page.locator("#timedock .td-run").click();
  await expect(page.locator(".status")).toContainText(
    "Демо-результат только для одного времени",
  );
  await page.locator(".status").getByRole("button", { name: "08:30" }).click();
  await expect(page.locator(".status")).toContainText("Результат получен");
});
test("cancels a pending response after parameters change", async ({ page }) => {
  await page.emulateMedia({ reducedMotion: "no-preference" });
  await page.goto("/#atlas");
  await page
    .locator("#filters")
    .getByRole("button", { name: "Москва", exact: true })
    .click();
  await page.locator("#timedock .td-run").click();
  await page.locator("#timedock input[type=range]").fill("81");
  await page.waitForTimeout(1700);
  await expect(page.locator(".status")).toContainText("Параметры изменены");
  await expect(page.locator("#timedock .td-now")).toHaveText("20:15");
});
test("chat suggestion and scenario use the same results as the map", async ({
  page,
}) => {
  await page.goto("/#atlas");
  await page
    .locator("#filters")
    .getByRole("button", { name: "Москва", exact: true })
    .click();
  await page
    .locator(".ctx-actions")
    .getByRole("button", { name: "Проанализировать", exact: true })
    .click();
  await expect(page.locator(".proposal")).toBeVisible();
  await page
    .getByRole("button", { name: "Проверить сценарий", exact: true })
    .click();
  await expect(page.locator(".msg.a").last()).toContainText("с 44 до 36 минут");
  await expect(page.locator(".basecmp")).toBeVisible();
  await expect(page.locator(".ctx .bignum")).toContainText("36");
  await page
    .locator(".basecmp")
    .getByRole("button", { name: "База", exact: true })
    .click();
  await expect(page.locator(".ctx .bignum")).toContainText("44");
});
test("free question is safe text and does not pretend to call AI", async ({
  page,
}) => {
  await page.goto("/#atlas");
  await page.getByRole("button", { name: "Агент", exact: true }).click();
  await page.getByLabel("Вопрос агенту").fill("<img src=x onerror=alert(1)>");
  await page.getByRole("button", { name: "Отправить", exact: true }).click();
  await expect(page.locator(".msg.u")).toHaveText(
    "<img src=x onerror=alert(1)>",
  );
  await expect(page.locator(".msg.a").last()).toContainText(
    "агент не подключён",
  );
  await expect(page.locator(".msg.u img")).toHaveCount(0);
});
test("passenger direction and freight scopes are separate", async ({
  page,
}) => {
  await page.goto("/#atlas");
  await page.locator("#modeTabs button").nth(1).click();
  await expect(page.locator(".ctx")).toContainText("Москва → Калининград");
  await page.getByRole("button", { name: "Поменять направление" }).click();
  await expect(page.locator(".ctx")).toContainText("Калининград → Москва");
  await page.locator("#modeTabs button").nth(2).click();
  await expect(page.locator(".ctx")).toContainText("Склад");
  await page
    .getByRole("button", { name: "Между территориями", exact: true })
    .click();
  await expect(page.locator(".ctx")).toContainText("Москва → Новороссийск");
  await page.getByRole("button", { name: "Наливной", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "Газ", exact: true }),
  ).toBeEnabled();
});
test("all eleven demo states can be selected without crashing", async ({
  page,
}) => {
  await page.goto("/#atlas");
  await page
    .locator("#filters")
    .getByRole("button", { name: "Москва", exact: true })
    .click();
  for (const name of [
    "До расчёта",
    "Параметры изменены",
    "Расчёт выполняется",
    "Результат получен",
    "Частичный результат",
    "Нет подтверждённых данных",
    "Подтверждённо нет маршрута",
    "Источник недоступен",
    "Дата не поддерживается",
    "Формула ожидает утверждения",
    "Агент недоступен",
  ]) {
    await page.getByRole("button", { name: "Состояния", exact: true }).click();
    await page
      .locator("#statesMenu")
      .getByRole("button", { name, exact: true })
      .click();
    await expect(page.locator("#atlas")).toBeVisible();
  }
  await expect(page.getByRole("alert")).toContainText(
    "Агент временно недоступен",
  );
});
test("mobile panels, range keyboard and absence of overflow", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/#atlas");
  await page
    .getByRole("button", { name: "Фильтры", exact: true })
    .first()
    .click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await page
    .locator("#filters")
    .getByRole("button", { name: "Москва", exact: true })
    .click();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog")).toHaveCount(0);
  const range = page.locator("#mTime input[type=range]");
  await range.focus();
  await page.keyboard.press("PageUp");
  await expect(range).toHaveValue("38");
  await expect(page.locator("#mTime .td-now")).toHaveText("09:30");
  await page.locator(".m-btns").getByRole("button", { name: "Агент" }).click();
  await expect(page.getByRole("dialog")).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
});

test("scroll motion and reduced motion preserve the content", async ({
  page,
}) => {
  await page.emulateMedia({ reducedMotion: "no-preference" });
  await page.goto("/");
  await expect(page.locator("#landing")).toBeVisible();
  await page.evaluate(() => window.scrollTo(0, 600));
  await expect
    .poll(() =>
      page
        .locator("#top")
        .evaluate((el) =>
          parseFloat((el as HTMLElement).style.getPropertyValue("--p")),
        ),
    )
    .toBeGreaterThan(0.5);
  await page.locator("#featTitle").scrollIntoViewIfNeeded();
  await expect(page.locator("#featTitle")).toHaveClass(/is-in/);
  await page.emulateMedia({ reducedMotion: "reduce" });
  await expect(page.locator("#featTitle")).toHaveCSS("opacity", "1");
});
