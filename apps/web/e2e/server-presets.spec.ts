import { test, expect } from "@playwright/test";
import urban from "../tests/fixtures/api/urban_passenger.json" with { type: "json" };
import territories from "../tests/fixtures/api/territories.json" with { type: "json" };
import capabilities from "../tests/fixtures/api/capabilities.json" with { type: "json" };
import payloads from "../tests/fixtures/api/backend-cargo-handling.json" with { type: "json" };

// Captured TEST_ONLY PostgreSQL → HTTP payloads. This browser test mocks transport;
// live API/worker integration is a separate gate.
test("server preset keeps exact request and runs cargo handling through saved proposal", async ({ page }) => {
  const requests: { path: string; body: unknown }[] = [];
  const preset = { preset_id: "TEST_ONLY-cargo", label: "Контроль межгородского груза", request: payloads.analysis.request, scenario_operations: ["reduce_handling_wait"] };
  await page.route("https://tile.openstreetmap.org/**", (route) => route.abort());
  await page.route("**/api/v1/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    const sent = request.postDataJSON();
    if (request.method() === "POST") requests.push({ path, body: sent });
    let body: unknown;
    let status = 200;
    if (path.endsWith("/capabilities")) body = { ...capabilities, analysis_presets: [preset] };
    else if (path.endsWith("/territories")) body = new URL(request.url()).searchParams.get("level") === "city" ? [
      { territory_id: "territory-A", name: "Москва", level: "city", timezone: "Europe/Moscow", parent_region_id: null, capital_city_id: null },
      { territory_id: "territory-B", name: "Калининград", level: "city", timezone: "Europe/Kaliningrad", parent_region_id: null, capital_city_id: null },
    ] : [];
    else if (path === "/api/v1/analyses" || path.endsWith("/runs")) {
      status = 202;
      body = { job_id: path.endsWith("/runs") ? "scenario-job" : "baseline-job", status: "queued", request_id: "TEST_ONLY", poll_after_seconds: 1 };
    } else if (path.includes("/jobs/")) body = {
      job_id: path.split("/").at(-1), status: "succeeded", request_id: "TEST_ONLY", created_at: "2026-11-09T00:00:00Z", started_at: "2026-11-09T00:00:01Z", finished_at: "2026-11-09T00:00:02Z",
      analysis_id: path.endsWith("baseline-job") ? payloads.analysis.analysis_id : null,
      scenario_run_id: path.endsWith("scenario-job") ? payloads.scenario.run_id : null, error: null, poll_after_seconds: 1,
    };
    else if (path.endsWith("/scenarios")) body = [payloads.proposal];
    else if (path.includes("/scenario-runs/")) body = payloads.scenario;
    else if (path.includes("/analyses/")) body = payloads.analysis;
    else { status = 404; body = {}; }
    await route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
  });
  await page.goto("/?data=api#atlas");
  await page.getByRole("combobox", { name: "Готовые серверные условия", exact: true }).click();
  await page.getByRole("option", { name: preset.label, exact: true }).click();
  await expect(page.getByLabel("Выбранные серверные условия")).toContainText("без изменений");
  await expect(page.getByRole("button", { name: "Рассчитать категории по очереди" })).toHaveCount(0);
  await page.getByRole("button", { name: "Проанализировать", exact: true }).first().click();
  await expect(page.locator(".status")).toContainText("Результат получен");
  expect(requests.find((request) => request.path === "/api/v1/analyses")?.body).toEqual(preset.request);
  expect(requests.some((request) => request.path.includes("analysis-groups"))).toBe(false);
  await page.getByRole("button", { name: "Агент", exact: true }).first().click();
  await expect(page.locator(".proposal")).toContainText("Сократить обработку груза");
  await expect(page.locator(".proposal")).toContainText("120 мин");
  await expect(page.locator(".proposal")).toContainText("60 мин");
  await expect(page.locator(".proposal")).not.toContainText("Дополнительное отправление");
  await page.getByRole("button", { name: "Проверить сценарий", exact: true }).click();
  await expect(page.getByRole("region", { name: "Сравнение до / после", exact: true })).toContainText("Ресурс обработки: 1 → 2 ед.");
  expect(requests.find((request) => request.path.endsWith("/runs"))?.body).toEqual({ schema_version: "2" });
  await page.getByRole("button", { name: "Свернуть чат", exact: true }).click();
  await page.setViewportSize({ width: 390, height: 844 });
  const mobileSheet = page.locator(".m-sheet");
  await expect(mobileSheet).toBeVisible();
  await expect(mobileSheet.locator(".m-sum")).toContainText("Откройте показатели сохранённого результата");
  await expect(mobileSheet.locator(".m-sum")).not.toContainText("точек");
  await expect(mobileSheet.locator(".m-sum")).not.toContainText("выберите точку на карте");
  await mobileSheet.locator("summary", { hasText: "Результат и рассчитанные точки" }).click();
  const mobileComparison = mobileSheet.getByRole("region", { name: "Сравнение до / после", exact: true });
  await expect(mobileComparison).toBeVisible();
  await expect(mobileComparison).toContainText("Ресурс обработки: 1 → 2 ед.");
  await expect(mobileComparison.getByText("120 мин", { exact: true })).toBeVisible();
  await expect(mobileComparison.getByText("60 мин", { exact: true })).toBeVisible();
});


test("multicategory preset cannot be decomposed or overwritten by freeform defaults", async ({ page }) => {
  const request = { ...urban.request, city_id: "city-1", departure_at: "2026-11-09T10:17:31+03:00", categories: ["school", "airport"], priorities: { school: "low", airport: "high" } };
  const preset = { preset_id: "TEST_ONLY-categories", label: "Точный набор категорий", request };
  const posted: unknown[] = [];
  await page.route("https://tile.openstreetmap.org/**", (route) => route.abort());
  await page.route("**/api/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    let body: unknown;
    let status = 200;
    if (path.endsWith("/capabilities")) body = { ...capabilities, analysis_presets: [preset] };
    else if (path.endsWith("/territories")) body = territories.filter((place) => place.level === new URL(route.request().url()).searchParams.get("level"));
    else if (path === "/api/v1/analyses") {
      posted.push(route.request().postDataJSON());
      status = 202;
      body = { job_id: "TEST_ONLY-pending", status: "queued", request_id: "r", poll_after_seconds: 1 };
    } else if (path.endsWith("/jobs/TEST_ONLY-pending")) body = { job_id: "TEST_ONLY-pending", status: "running", request_id: "r", created_at: "2026-11-09T00:00:00Z", started_at: "2026-11-09T00:00:01Z", finished_at: null, analysis_id: null, scenario_run_id: null, error: null, poll_after_seconds: 1 };
    else { status = 404; body = {}; }
    await route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
  });
  await page.goto("/?data=api#atlas");
  await page.getByRole("combobox", { name: "Готовые серверные условия", exact: true }).click();
  await page.getByRole("option", { name: preset.label, exact: true }).click();
  await expect(page.getByRole("button", { name: "Рассчитать категории по очереди" })).toHaveCount(0);
  await expect(page.locator("#apiDate")).toHaveCount(0);
  await page.getByRole("button", { name: "Рассчитать", exact: true }).first().click();
  await expect.poll(() => posted.length).toBe(1);
  expect(posted[0]).toEqual(request);
});
