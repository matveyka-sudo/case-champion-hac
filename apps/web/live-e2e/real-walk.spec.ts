import { expect, test, type Page } from "@playwright/test";
import { writeFile } from "node:fs/promises";
import type { AnalysisResult, Schemas } from "../src/api/types";

async function choose(page: Page, label: string, option: string) {
  await page.getByRole("combobox", { name: label, exact: true }).click();
  await page.getByRole("option", { name: option, exact: true }).click();
}

async function chooseAcceptedDate(page: Page) {
  const trigger = page.getByRole("button", { name: "Выбрать дату", exact: true });
  const current = (await trigger.innerText()).match(/(\d{2})\.(\d{2})\.(\d{4})/);
  expect(current, "The date picker must expose its current date").not.toBeNull();
  const months = (2026 - Number(current![3])) * 12 + 11 - Number(current![2]);
  expect(Math.abs(months), "The pinned pilot date must be reachable through the calendar").toBeLessThan(120);
  await trigger.click();
  for (let step = 0; step < Math.abs(months); step++) {
    await page.getByRole("button", { name: months > 0 ? "Следующий месяц" : "Предыдущий месяц", exact: true }).click();
  }
  await page.locator('[data-date="2026-11-10"]').click();
}

test("real walk: browser → scoped PostgreSQL job → worker → partial saved result", async ({
  page, context, browser,
}, testInfo) => {
  // Playwright supplies a fresh browser context. No HTTP routes, fixture responses,
  // cookies, stored job IDs or preloaded analysis results are injected here.
  const apiRequests: { method: string; path: string }[] = [];
  page.on("request", (request) => {
    const url = new URL(request.url());
    if (url.pathname.startsWith("/api/v1/")) {
      apiRequests.push({ method: request.method(), path: url.pathname });
    }
  });
  await page.goto("/?data=api#atlas");
  await choose(page, "Город", "Калининград");
  await choose(page, "Передвижение", "Пешком");
  for (const checkbox of await page.getByRole("checkbox").all()) {
    await checkbox.uncheck();
  }
  await page.getByRole("checkbox", { name: "Школы", exact: true }).check();
  await chooseAcceptedDate(page);

  const submittedResponse = page.waitForResponse((response) =>
    new URL(response.url()).pathname === "/api/v1/analyses" && response.request().method() === "POST",
  );
  await page.getByRole("button", { name: "Рассчитать", exact: true }).first().click();
  const submitted = await submittedResponse;
  expect(submitted.status()).toBe(202);
  const accepted = await submitted.json() as Schemas["JobAccepted"];
  expect(accepted.status).toBe("queued");
  const sent = submitted.request().postDataJSON();
  expect(sent).toMatchObject({
    city_id: "kaliningrad", passenger_mode: "walk", traffic_mode: "static", categories: ["school"],
  });
  expect(submitted.request().headers()["idempotency-key"]).toBeTruthy();

  await expect(page.getByRole("status")).toContainText("Результат получен", { timeout: 40_000 });
  const card = page.locator(".api-card");
  await expect(card).toContainText("Время найденного пути");
  await expect(card).toContainText("9,1 мин");
  await expect(card).toContainText("Минимальное время не подтверждено");
  await expect(card).toContainText("модельная скорость 5 км/ч");
  await expect(card).toContainText("не участвует в полном рейтинге");
  await expect(card).not.toContainText("Для результата указано дополнительное ограничение");
  await expect(card).not.toContainText("urban_h_seconds");
  await expect(card).not.toContainText("best_observed_journey_seconds");
  await expect(card).not.toContainText("urban_accessibility_index");

  const jobResponse = await context.request.get(`/api/v1/jobs/${accepted.job_id}`);
  expect(jobResponse.status()).toBe(200);
  const job = await jobResponse.json() as Schemas["JobView"];
  expect(job.status).toBe("succeeded");
  expect(job.analysis_id).toBeTruthy();
  const savedResponse = await context.request.get(`/api/v1/analyses/${job.analysis_id}`);
  expect(savedResponse.status()).toBe(200);
  const result = await savedResponse.json() as AnalysisResult;
  expect(result.status).toBe("partial");
  expect(result.request).toMatchObject(sent);
  expect(result.journeys).toHaveLength(1);
  expect(result.journeys[0].total_seconds).toBeCloseTo(547.404, 6);
  expect(result.journeys[0].completeness).toBe("partial");
  expect(result.journeys[0].reason_code).toBe("MINIMUM_TIME_UNPROVEN");
  expect(result.scores.length).toBeGreaterThan(0);
  expect(result.scores.every((score) => score.value === null && !score.ranking_eligible)).toBe(true);
  expect(result.limitations).toEqual(expect.arrayContaining([
    "PILOT_SAMPLE_ONLY", "PARTIAL_SCHOOL_CATALOG", "MINIMUM_TIME_UNPROVEN", "INCOMPLETE_POPULATION_COVERAGE",
  ]));
  expect(result.result_hash).toMatch(/^[a-f0-9]{64}$/);
  const stranger = await browser.newContext({ baseURL: testInfo.project.use.baseURL });
  try {
    const foreignRead = await stranger.request.get(`/api/v1/analyses/${job.analysis_id}`);
    expect(foreignRead.status()).toBe(404);
  } finally {
    await stranger.close();
  }
  expect(apiRequests.some((request) => request.path.includes("/explanation"))).toBe(false);
  expect(apiRequests.some((request) => /departures|rasp/.test(request.path))).toBe(false);
  expect(apiRequests.some((request) => request.method === "POST" && request.path.endsWith("/runs"))).toBe(false);

  const evidence = { accepted, job, result, submitted_request: sent, api_requests: apiRequests };
  await writeFile(testInfo.outputPath("real-walk-evidence.json"), JSON.stringify(evidence, null, 2));
  await testInfo.attach("real-walk-evidence", { body: JSON.stringify(evidence), contentType: "application/json" });
  await page.screenshot({ path: testInfo.outputPath("real-walk-desktop.png"), fullPage: true });
  await testInfo.attach("real-walk-desktop", { path: testInfo.outputPath("real-walk-desktop.png"), contentType: "image/png" });
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(card).toBeVisible();
  await expect(card).toContainText("Время найденного пути");
  await expect(page.locator("body")).toHaveJSProperty("scrollWidth", 390);
  await page.screenshot({ path: testInfo.outputPath("real-walk-mobile.png"), fullPage: true });
  await testInfo.attach("real-walk-mobile", { path: testInfo.outputPath("real-walk-mobile.png"), contentType: "image/png" });
});
