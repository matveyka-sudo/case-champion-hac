import { test, expect, type Page } from "@playwright/test";
import type { AnalysisResult, Schemas, Territory } from "../src/api/types";
import capsRaw from "../tests/fixtures/api/capabilities.json" with { type: "json" };
import catalogRaw from "../tests/fixtures/api/territories.json" with { type: "json" };
import baseRaw from "../tests/fixtures/api/urban_passenger.json" with { type: "json" };
import scenarioRaw from "../tests/fixtures/api/scenario.json" with { type: "json" };
import proposalRaw from "../tests/fixtures/api/proposal.json" with { type: "json" };

// Offline browser regression fixtures. No live routing/provider requests.
const catalog: Territory[] = [...catalogRaw as Territory[], { territory_id: "novorossiysk", name: "Новороссийск", level: "city", timezone: "Europe/Moscow", parent_region_id: null, capital_city_id: null }];
const cities = [["city-1", "Москва", "+03:00"], ["city-2", "Калининград", "+02:00"], ["novorossiysk", "Новороссийск", "+03:00"]] as const;
function saved(id: string, city = "city-1", offset = "+03:00"): AnalysisResult {
  const value = structuredClone(baseRaw) as AnalysisResult;
  value.analysis_id = id;
  value.request = { ...value.request, analysis_kind: "urban_passenger", city_id: city, departure_at: `2026-11-09T08:15:00${offset}`,
    passenger_mode: "walk", categories: ["school"], priorities: { school: "high" }, snapshot_id: value.snapshot_id };
  return value;
}
async function fixture(page: Page, options: { denied?: number; delayFirst?: boolean; running?: boolean; scenarioJob?: boolean; retired?: boolean; scenario?: boolean; badHash?: boolean; scenarioDenied?: number } = {}) {
  const caps = structuredClone(capsRaw) as Schemas["Capabilities"];
  caps.snapshots![0].territory_ids.push("novorossiysk");
  if (options.retired) caps.snapshots![0].valid_from = "2099-01-01";
  const analyses = new Map(cities.map(([id, , offset]) => [id, saved(id, id, offset)]));
  let posts = 0, getCount = 0, jobCount = 0, cancelCount = 0, cancelled = false, scenarioPosts = 0, scenarioGets = 0;
  let deny = options.denied || 0;
  let after: AnalysisResult | null = null;
  const firstReply = Promise.withResolvers<void>();
  await page.route("https://tile.openstreetmap.org/**", (route) => route.fulfill({ status: 204 }));
  await page.route("**/api/v1/**", async (route) => {
    const request = route.request(), path = new URL(request.url()).pathname;
    let status = 200;
    let body: unknown;
    const error = (code: string) => ({ error: { code, message: "Synthetic private error", field_errors: [], request_id: "trace", retryable: false } });
    if (path.endsWith("/capabilities")) body = caps;
    else if (path.endsWith("/territories")) body = catalog.filter((value) => value.level === new URL(request.url()).searchParams.get("level"));
    else if (path === "/api/v1/analyses") {
      posts++;
      after = saved(`new-${posts}`);
      after.request = request.postDataJSON();
      analyses.set(after.analysis_id, after);
      status = 202;
      body = { job_id: "analysis-job", status: "queued", request_id: "trace", poll_after_seconds: 1 };
    } else if (path.endsWith("/cancel")) {
      cancelled = true; cancelCount++;
      body = job("cancelled", path.split("/").at(-2));
    } else if (path.includes("/jobs/")) {
      jobCount++;
      const phase = options.running && jobCount === 1 ? "queued" : options.running && jobCount === 2 ? "running" : "succeeded";
      body = job(cancelled ? "cancelled" : phase, path.split("/").at(-1));
    } else if (path.includes("/scenario-runs/")) {
      scenarioGets++;
      if (options.scenarioDenied) { status = options.scenarioDenied; body = error("unknown_id"); }
      else body = { ...scenarioRaw, run_id: "pending:scenario:old", baseline_analysis_id: "city-1", baseline_result_hash: options.badHash ? "wrong-baseline" : baseRaw.result_hash };
    } else if (path.endsWith("/scenarios")) body = options.scenario ? [{ ...proposalRaw, baseline_analysis_id: "city-1", baseline_result_hash: baseRaw.result_hash }] : [];
    else if (path.includes("/scenarios/") && path.endsWith("/runs")) {
      scenarioPosts++;
      status = 202;
      body = { job_id: "scenario-job", status: "queued", request_id: "trace", poll_after_seconds: 1 };
    }
    else if (path.includes("/analyses/")) {
      getCount++;
      const id = decodeURIComponent(path.split("/").at(-1)!);
      if (options.delayFirst && id === "city-1") await firstReply.promise;
      if (deny) { status = deny; body = error(deny === 410 ? "private_source_expired" : "unknown_id"); }
      else if (analyses.has(id)) body = analyses.get(id);
      else { status = 404; body = error("unknown_id"); }
    } else { status = 404; body = error("unknown_id"); }
    await route.fulfill({ status, json: body });
  });
  function job(state: string, id = "analysis-job") {
    const isScenario = options.scenarioJob || id === "scenario-job";
    return { job_id: id, status: state, request_id: "trace", created_at: "2026-11-09T00:00:00Z",
      started_at: state === "queued" ? null : "2026-11-09T00:00:01Z", finished_at: ["queued", "running"].includes(state) ? null : "2026-11-09T00:00:02Z",
      analysis_id: state === "succeeded" && !isScenario ? after?.analysis_id || "city-1" : null,
      scenario_run_id: state === "succeeded" && isScenario ? "pending:scenario:old" : null, error: null, poll_after_seconds: 1,
      ...(state === "running" && !isScenario ? { progress: { completed_batches: 88, total_batches: 88, phase: "finalizing" } } : {}) };
  }
  return { get posts() { return posts; }, get gets() { return getCount; }, get jobs() { return jobCount; }, get cancels() { return cancelCount; }, get scenarioPosts() { return scenarioPosts; }, get scenarioGets() { return scenarioGets; }, allow: () => { deny = 0; }, release: () => firstReply.resolve() };
}
async function resultVisible(page: Page, city: string) {
  await expect(page.locator(".status")).toContainText("Результат получен");
  await expect(page.locator(".status")).toContainText(city);
}
test("saved analysis survives refresh and landing Back/Forward with no new POST", async ({ page }) => {
  const mock = await fixture(page);
  await page.goto("/?data=api#atlas?analysis=city-1");
  await resultVisible(page, "Москва");
  await page.reload();
  await resultVisible(page, "Москва");
  await page.getByRole("link", { name: "Вернуться на главную", exact: true }).click();
  await expect(page.locator("#landing")).toBeVisible();
  await page.goBack();
  await resultVisible(page, "Москва");
  await page.goForward();
  await expect(page.locator("#landing")).toBeVisible();
  expect(mock.posts).toBe(0);
  expect(mock.gets).toBe(3);
  expect(await page.evaluate(() => [...Object.keys(localStorage), ...Object.keys(sessionStorage)].filter((key) => key !== "waydex:theme"))).toEqual([]);
});
for (const [id, name] of cities) test(`saved ${name} restores date/mode/categories on desktop and mobile`, async ({ page }) => {
  const mock = await fixture(page);
  await page.goto(`/?data=api#atlas?analysis=${id}`);
  await resultVisible(page, name);
  await expect(page.getByRole("group", { name: "Город", exact: true }).getByRole("button", { name, exact: true })).toHaveAttribute("aria-pressed", "true");
  await expect(page.locator("#apiDate")).toContainText("09.11.2026");
  await expect(page.locator("#apiTime")).toHaveValue("33");
  await expect(page.getByRole("group", { name: "Передвижение", exact: true }).getByRole("button", { name: "Пешком", exact: true })).toHaveAttribute("aria-pressed", "true");
  await expect(page.locator("#apiFilters").getByRole("button", { name: "Школы", exact: true })).toHaveAttribute("aria-pressed", "true");
  await expect(page.locator("#apiFilters").getByRole("button", { name: "ТЦ", exact: true })).toHaveAttribute("aria-pressed", "false");
  await page.setViewportSize({ width: 390, height: 844 });
  await page.reload();
  await resultVisible(page, name);
  await expect(page.locator("#mTime .wx-date-trigger")).toContainText("09.11.2026");
  await expect(page.locator("#mTime input[type=range]")).toHaveValue("33");
  expect(mock.posts).toBe(0);
});
test("only an explicit run pushes history; refresh discards dirty edits and Back restores baseline", async ({ page }) => {
  const mock = await fixture(page);
  await page.goto("/?data=api#atlas?analysis=city-1");
  await resultVisible(page, "Москва");
  await page.getByRole("group", { name: "Передвижение", exact: true }).getByRole("button", { name: "Авто", exact: true }).click();
  await expect(page.locator(".status")).toContainText("Параметры изменены");
  await expect(page).toHaveURL(/analysis=city-1$/);
  await page.reload();
  await resultVisible(page, "Москва");
  await expect(page.getByRole("group", { name: "Передвижение", exact: true }).getByRole("button", { name: "Пешком", exact: true })).toHaveAttribute("aria-pressed", "true");
  expect(mock.posts).toBe(0);
  await page.locator("#apiFilters .runbtn").click();
  await expect(page).toHaveURL(/analysis=new-1$/);
  await page.goBack();
  await expect(page).toHaveURL(/analysis=city-1$/);
  await resultVisible(page, "Москва");
  await page.goForward();
  await expect(page).toHaveURL(/analysis=new-1$/);
  await resultVisible(page, "Москва");
  expect(mock.posts).toBe(1);
});
test("queued job resumes polling through 88/88 finalizing and replaces URL only after result GET", async ({ page }) => {
  const mock = await fixture(page, { running: true });
  await page.goto("/?data=api#atlas?job=analysis-job");
  await expect(page.locator(".status")).toContainText("Сохранение результата");
  await expect(page).toHaveURL(/job=analysis-job$/);
  await expect(page.locator("#apiResultCard")).toHaveCount(0);
  await resultVisible(page, "Москва");
  await expect(page).toHaveURL(/analysis=city-1$/);
  expect(mock.posts).toBe(0);
  expect(mock.jobs).toBe(3);
});
for (const denied of [404, 410]) test(`session/expiry ${denied} is revalidated by GET on retry without exposing old data`, async ({ page }) => {
  const mock = await fixture(page, { denied });
  await page.goto("/?data=api#atlas?analysis=city-1");
  await expect(page.locator(".statecard[role=alert]")).toContainText(denied === 404 ? "недоступен в этой сессии" : "доступа к сохранённому расчёту истёк");
  await expect(page.locator("#apiResultCard")).toHaveCount(0);
  mock.allow();
  await page.locator(".statecard[role=alert]").getByRole("button", { name: "Повторить" }).click();
  await resultVisible(page, "Москва");
  expect(mock.posts).toBe(0);
  expect(mock.gets).toBe(2);
});
test("malformed/unsupported pointers do not fetch or recalculate", async ({ page }) => {
  const mock = await fixture(page);
  await page.goto("/?data=api#atlas?analysis=a&job=b");
  await expect(page.locator(".statecard[role=alert]")).toContainText("некорректна или не поддерживается");
  await page.locator(".statecard[role=alert]").getByRole("button", { name: "Повторить" }).click();
  await expect(page.locator(".statecard[role=alert]")).toBeVisible();
  expect(mock.gets).toBe(0);
  expect(mock.jobs).toBe(0);
  expect(mock.posts).toBe(0);
});
test("navigation ignores late replies and does not borrow a previous session snapshot", async ({ page }) => {
  const mock = await fixture(page, { delayFirst: true });
  await page.goto("/?data=api#atlas?analysis=city-1");
  await expect.poll(() => mock.gets).toBe(1);
  await page.evaluate(() => { location.hash = "#atlas?analysis=city-2"; });
  await resultVisible(page, "Калининград");
  mock.release();
  await expect(page.locator(".status")).toContainText("Калининград");
  await page.evaluate(() => { location.hash = "#atlas?analysis=unavailable"; });
  await expect(page.locator(".statecard[role=alert]")).toContainText("недоступен в этой сессии");
  await expect(page.locator("#apiResultCard")).toHaveCount(0);
  expect(mock.posts).toBe(0);
});
test("a scenario job is never opened as an analysis job", async ({ page }) => {
  const mock = await fixture(page, { scenarioJob: true });
  await page.goto("/?data=api#atlas?job=analysis-job");
  await expect(page.locator(".statecard[role=alert]")).toContainText("не относится к заданию анализа");
  expect(mock.gets).toBe(0);
  expect(mock.posts).toBe(0);
});
test("retired source availability does not change the saved request or force a new calculation", async ({ page }) => {
  const mock = await fixture(page, { retired: true });
  await page.goto("/?data=api#atlas?analysis=city-1");
  await resultVisible(page, "Москва");
  await expect(page.locator("#apiDate")).toContainText("09.11.2026");
  await expect(page.locator("#apiResultCard")).toBeVisible();
  await page.locator("#apiFilters .runbtn").click();
  await expect(page.locator(".statecard[role=alert]")).toContainText("Нет подтверждённого набора данных");
  await expect(page.locator("#apiDate")).toContainText("09.11.2026");
  await expect(page.locator("#apiResultCard")).toBeVisible();
  expect(mock.posts).toBe(0);
});
test("recovered job cancellation is explicit and refresh never recreates the cancelled job", async ({ page }) => {
  const mock = await fixture(page, { running: true });
  await page.goto("/?data=api#atlas?job=analysis-job");
  await page.locator(".status").getByRole("button", { name: "Отменить", exact: true }).click();
  await expect(page.locator(".status")).toContainText("Задание отменено");
  expect(mock.cancels).toBe(1);
  await page.reload();
  await expect(page.locator(".status")).toContainText("Задание отменено");
  expect(mock.cancels).toBe(1);
  expect(mock.posts).toBe(0);
});
test("historical scenario opens by GET, survives refresh, and binds its baseline request", async ({ page }) => {
  const mock = await fixture(page);
  await page.goto("/?data=api#atlas?scenario=pending%3Ascenario%3Aold");
  await resultVisible(page, "Москва");
  await expect(page.locator("#apiResultCard")).toContainText("Сравнение до / после");
  await expect(page.getByRole("button", { name: "Сценарий", exact: true })).toHaveAttribute("aria-pressed", "true");
  await expect(page.locator("#apiDate")).toContainText("09.11.2026");
  await page.reload();
  await resultVisible(page, "Москва");
  await expect(page.locator("#apiResultCard")).toContainText("Сравнение до / после");
  expect(mock.scenarioGets).toBe(2);
  expect(mock.gets).toBe(2);
  expect(mock.posts + mock.scenarioPosts).toBe(0);
});
test("an explicit scenario run has its own job pointer and Back restores baseline without recomputation", async ({ page }) => {
  const mock = await fixture(page, { scenario: true });
  await page.goto("/?data=api#atlas?analysis=city-1");
  await resultVisible(page, "Москва");
  await page.getByRole("button", { name: "Агент", exact: true }).first().click();
  await page.getByRole("button", { name: "Проверить сценарий", exact: true }).click();
  await expect(page).toHaveURL(/scenario=pending%3Ascenario%3Aold$/);
  await expect(page.locator("#apiResultCard")).toContainText("Сравнение до / после");
  await page.reload();
  await resultVisible(page, "Москва");
  await page.goBack();
  await expect(page).toHaveURL(/analysis=city-1$/);
  await resultVisible(page, "Москва");
  await expect(page.locator("#apiResultCard")).not.toContainText("Сравнение до / после");
  expect(mock.posts).toBe(0);
  expect(mock.scenarioPosts).toBe(1);
});
test("scenario job resumes GET polling and requires both scenario and baseline before showing after", async ({ page }) => {
  const mock = await fixture(page, { running: true });
  await page.goto("/?data=api#atlas?scenario_job=scenario-job");
  await expect.poll(() => mock.jobs).toBeGreaterThanOrEqual(2);
  await expect(page.locator(".status")).toContainText("Вычисляется");
  await expect(page.locator(".status")).not.toContainText("пакет");
  await expect(page).toHaveURL(/scenario_job=scenario-job$/);
  await expect(page.locator("#apiResultCard")).toHaveCount(0);
  await resultVisible(page, "Москва");
  await expect(page).toHaveURL(/scenario=pending%3Ascenario%3Aold$/);
  await expect(page.getByRole("button", { name: "Сценарий", exact: true })).toHaveAttribute("aria-pressed", "true");
  expect(mock.posts + mock.scenarioPosts).toBe(0);
});
for (const options of [{ badHash: true }, { scenarioDenied: 404 }, { denied: 404 }]) test(`scenario revalidation rejects unavailable or foreign baseline ${JSON.stringify(options)}`, async ({ page }) => {
  const mock = await fixture(page, options);
  await page.goto("/?data=api#atlas?scenario=pending%3Ascenario%3Aold");
  await expect(page.locator(".statecard[role=alert]")).toBeVisible();
  await expect(page.locator("#apiResultCard")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Сценарий", exact: true })).toHaveCount(0);
  expect(mock.posts + mock.scenarioPosts).toBe(0);
});
