import { test, expect, type Page } from "@playwright/test";
import type { AnalysisResult, Schemas, Territory } from "../src/api/types";
import capabilities from "../tests/fixtures/api/capabilities.json" with { type: "json" };
import territories from "../tests/fixtures/api/territories.json" with { type: "json" };
import urban from "../tests/fixtures/api/urban_passenger.json" with { type: "json" };
import proposal from "../tests/fixtures/api/proposal.json" with { type: "json" };
import scenario from "../tests/fixtures/api/scenario.json" with { type: "json" };
import pax from "../tests/fixtures/api/intercity_passenger.json" with { type: "json" };
import cargo from "../tests/fixtures/api/urban_cargo.json" with { type: "json" };
import interCargo from "../tests/fixtures/api/intercity_cargo.json" with { type: "json" };
import { scenarioRouteFixture } from "../tests/fixtures/scenario-routes";
import { returnContextFixture } from "../tests/fixtures/return-context";
import { airportScope, scopedCapabilities } from "../tests/fixtures/urban-scopes";
const fixtures = {
  urban_passenger: urban,
  intercity_passenger: pax,
  urban_cargo: cargo,
  intercity_cargo: interCargo,
};
const blankTile = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aCMsAAAAASUVORK5CYII=",
  "base64",
);
async function choose(page: Page, label: string, option: string) {
  await page.getByRole("combobox", { name: label, exact: true }).click();
  await page.getByRole("option", { name: option, exact: true }).click();
}
async function mockApi(
  page: Page,
  options: {
    errorStatus?: number;
    running?: boolean;
    emptyProposals?: boolean;
    samplePopulation?: number | null;
    capabilities?: Schemas["Capabilities"];
    territories?: Territory[];
    result?: Partial<AnalysisResult>;
    scenarioResult?: Partial<Schemas["ScenarioResult"]>;
  } = {},
) {
  let request: unknown = null,
    posts = 0,
    cancelled = false,
    scenarioStarted = false;
  const requests: {
    url: string;
    method: string;
    body: unknown;
    key: string | undefined;
  }[] = [];
  const base = structuredClone(urban) as AnalysisResult;
  base.map_data = {
    status: "available",
    completeness: "partial",
    reason_code: "SYNTHETIC_ONLY",
    features: [
      {
        entity_kind: "sample_point",
        entity_id: "demo-point",
        territory_id: "city-1",
        geometry: { type: "Point", coordinates: [37, 55] },
        geometry_kind: "actual",
        source_refs: ["demo-evidence"],
        population: options.samplePopulation ?? null,
        population_year: 2025,
        population_method: "synthetic-test-only",
      },
    ],
  };
  let result = base;
  await page.route("https://tile.openstreetmap.org/**", (route) =>
    route.fulfill({ contentType: "image/png", body: blankTile }),
  );
  await page.route("**/api/v1/**", async (route) => {
    const r = route.request(),
      url = new URL(r.url()),
      path = url.pathname;
    let status = 200;
    let body: unknown;
    const sent = r.postDataJSON();
    requests.push({
      url: path,
      method: r.method(),
      body: sent,
      key: r.headers()["idempotency-key"],
    });
    if (path.endsWith("/capabilities")) body = options.capabilities || capabilities;
    else if (path.endsWith("/territories"))
      body = (options.territories || territories).filter(
        (t) => t.level === url.searchParams.get("level"),
      );
    else if (path === "/api/v1/analyses") {
      posts++;
      request = sent;
      if (options.errorStatus) {
        status = options.errorStatus;
        body = {
          error: {
            code:
              status === 422
                ? "invalid_request"
                : status === 409
                  ? "stored_result_incompatible"
                  : "engine_unavailable",
            message: "Synthetic service error",
            field_errors:
              status === 422
                ? [
                    {
                      location: ["body", "city_id"],
                      code: "invalid",
                      message: "City rejected",
                    },
                  ]
                : [],
            request_id: "test-error",
            retryable: false,
          },
        };
      } else {
        const kind = sent.analysis_kind as keyof typeof fixtures;
        result = {
          ...structuredClone(fixtures[kind]),
          ...(kind === "urban_passenger" ? { map_data: base.map_data } : {}),
          ...options.result,
          request: { ...structuredClone(fixtures[kind].request), ...sent },
        } as AnalysisResult;
        status = 202;
        body = {
          job_id: "analysis-job",
          status: "queued",
          request_id: "r",
          poll_after_seconds: 1,
        };
      }
    } else if (path.endsWith("/cancel")) {
      cancelled = true;
      body = job("cancelled");
    } else if (path.includes("/jobs/"))
      body = job(
        cancelled ? "cancelled" : options.running ? "running" : "succeeded",
      );
    else if (path.endsWith("/explanation"))
      body = {
        analysis_id: result.analysis_id,
        analysis_result_hash: result.result_hash,
        request_id: "explain-r",
        status: "template",
        model_id: null,
        reason_code: "DISABLED",
        findings: [
          {
            text: "Подтверждённое время из сохранённого расчёта.",
            metric_ids: ["demo-time"],
            claims: [{ metric_id: "demo-time", value: 1800, unit: "seconds" }],
            evidence_refs: ["demo-evidence"],
          },
        ],
        proposals: [],
        evidence_refs: ["demo-evidence"],
        limitations: ["synthetic_only"],
        retrieved_evidence: result.evidence_refs,
      };
    else if (path.endsWith("/scenarios"))
      body = options.emptyProposals
        ? []
        : [
            {
              ...proposal,
              baseline_analysis_id: result.analysis_id,
              baseline_result_hash: result.result_hash,
            },
          ];
    else if (path.includes("/scenarios/") && path.endsWith("/runs")) {
      scenarioStarted = true;
      status = 202;
      body = {
        job_id: "scenario-job",
        status: "queued",
        request_id: "s-r",
        poll_after_seconds: 1,
      };
    } else if (path.includes("/scenario-runs/"))
      body = {
        ...scenario,
        ...options.scenarioResult,
        baseline_analysis_id: result.analysis_id,
        baseline_result_hash: result.result_hash,
      };
    else if (path.includes("/analyses/")) body = result;
    else {
      status = 404;
      body = {
        error: {
          code: "not_found",
          message: "Not found",
          field_errors: [],
          request_id: "r",
          retryable: false,
        },
      };
    }
    await route.fulfill({
      status,
      contentType: "application/json",
      body: JSON.stringify(body),
    });
  });
  function job(status: string) {
    return {
      job_id: scenarioStarted ? "scenario-job" : "analysis-job",
      status,
      request_id: "r",
      created_at: "2026-11-09T08:00:00Z",
      started_at: "2026-11-09T08:00:01Z",
      finished_at: status === "running" ? null : "2026-11-09T08:00:02Z",
      analysis_id:
        status === "succeeded" && !scenarioStarted ? result.analysis_id : null,
      scenario_run_id:
        status === "succeeded" && scenarioStarted
          ? "synthetic-scenario-run"
          : null,
      error: null,
      poll_after_seconds: 1,
    };
  }
  return {
    requests,
    get posts() {
      return posts;
    },
    get request() {
      return request;
    },
  };
}
async function cityRun(page: Page) {
  await choose(page, "Город", "Москва");
  await page
    .getByRole("button", { name: "Рассчитать", exact: true })
    .first()
    .click();
}
for (const population of [1234.5, 0, null]) {
  test(`API: synthetic sample population ${population} stays visible without a cell feature`, async ({ page }) => {
    await mockApi(page, { samplePopulation: population, emptyProposals: true });
    await page.goto("/?data=api#atlas");
    await cityRun(page);
    await expect(page.getByRole("status")).toContainText("Результат получен");
    await page.getByRole("button", { name: "Агент", exact: true }).click();
    await page.getByLabel("Вопрос агенту").fill("Выбрать точку из сохранённой метрики");
    await page.getByRole("button", { name: "Отправить", exact: true }).click();
    await page.getByRole("button", { name: "Synthetic elapsed travel time: 30 мин", exact: true }).click();
    await expect(page.locator(".api-card")).toContainText(
      `Население ячейки: ${population === null ? "неизвестно" : population} · 2025 · synthetic-test-only`,
    );
  });
}
test("API: complete analysis, actual map features, scoped explanation and separate scenario", async ({
  page,
}) => {
  const mock = await mockApi(page);
  await page.goto("/?data=api#atlas");
  await cityRun(page);
  await expect(page.getByRole("status")).toContainText("Результат получен");
  await expect(page.locator(".api-card")).toContainText("30 мин");
  await expect(page.locator(".api-map canvas")).toBeVisible();
  await page.getByRole("button", { name: "Агент", exact: true }).click();
  await page.getByLabel("Вопрос агенту").fill("Почему?");
  await page.getByRole("button", { name: "Отправить", exact: true }).click();
  await expect(page.locator(".api-chat-log")).toContainText(
    "Шаблонное объяснение",
  );
  await page
    .getByRole("button", { name: "Проверить сценарий", exact: true })
    .click();
  await expect(page.locator(".api-card")).toContainText("Сравнение до / после");
  await page.getByRole("button", { name: "Сценарий", exact: true }).click();
  await expect(page.locator(".api-card")).toContainText("28,3 мин");
  await page.getByRole("button", { name: "База", exact: true }).click();
  await expect(page.locator(".api-card")).toContainText("30 мин");
  expect(
    mock.requests.filter((r) => r.url.endsWith("/runs"))[0].key,
  ).toBeTruthy();
  await expect(page.locator(".api-card")).not.toContainText(urban.analysis_id);
  await expect(page.locator(".api-card")).not.toContainText(urban.result_hash);
  await expect(page.locator(".api-card")).not.toContainText(
    scenario.overlay_graph_id,
  );
  await expect(page.locator(".api-card")).not.toContainText(
    "TURNAROUND_UNVERIFIED",
  );
  await expect(page.locator(".api-card")).toContainText(
    "Оборот транспорта и ресурсы",
  );
  await expect(page.locator(".api-chat")).not.toContainText(urban.analysis_id);
  await expect(page.getByText("Версии расчёта", { exact: true })).toHaveCount(
    0,
  );
  await page.screenshot({ path: "test-results/api-desktop.png" });
});
test("API: errors never activate demo data and field errors stay near the filter", async ({
  page,
}) => {
  const mock = await mockApi(page, { errorStatus: 422 });
  await page.goto("/?data=api#atlas");
  await cityRun(page);
  await expect(page.locator("#apiFilters")).toContainText("City rejected");
  await expect(page.locator(".api-card")).toHaveCount(0);
  expect(mock.posts).toBe(1);
  await expect(page.locator("body")).not.toContainText("44 мин");
});
test("API: explicit cancel stops a durable job, parameter changes do not apply old results", async ({
  page,
}) => {
  const mock = await mockApi(page, { running: true });
  await page.goto("/?data=api#atlas");
  await cityRun(page);
  await page
    .getByRole("button", { name: "Отменить серверное задание" })
    .click();
  await expect(page.getByRole("status")).toContainText("Задание отменено");
  expect(mock.requests.some((r) => r.url.endsWith("/cancel"))).toBe(true);
  await expect(page.locator(".api-card")).toHaveCount(0);
});
test("API: four analysis kinds send canonical requests including independent pipeline", async ({
  page,
}) => {
  const mock = await mockApi(page, { emptyProposals: true });
  await page.goto("/?data=api#atlas");
  await cityRun(page);
  await expect(page.getByRole("status")).toContainText("Результат получен");
  expect(mock.request).toMatchObject({
    analysis_kind: "urban_passenger",
    city_id: "city-1",
  });
  await page
    .getByRole("button", { name: "Пассажирские связи", exact: true })
    .click();
  await choose(page, "Территория А", "Москва");
  await choose(page, "Территория Б", "Калининград");
  await page
    .getByRole("button", { name: "Рассчитать", exact: true })
    .first()
    .click();
  await expect(page.getByRole("status")).toContainText("Результат получен");
  expect(mock.request).toMatchObject({
    analysis_kind: "intercity_passenger",
    origin_id: "city-1",
    destination_id: "city-2",
  });
  await page
    .getByRole("button", { name: "Грузовой доступ", exact: true })
    .click();
  await choose(page, "Город", "Калининград");
  await choose(
    page,
    "Профиль груза",
    "Непродовольственный · Обычный · Контейнер",
  );
  await page
    .getByRole("button", { name: "Рассчитать", exact: true })
    .first()
    .click();
  await expect(page.getByRole("status")).toContainText("Результат получен");
  expect(mock.request).toMatchObject({
    analysis_kind: "urban_cargo",
    city_id: "city-2",
  });
  await page
    .getByRole("button", { name: "Грузовые связи", exact: true })
    .click();
  await choose(page, "Территория А", "Москва");
  await choose(page, "Территория Б", "Калининград");
  await choose(page, "Тип грузового анализа", "Трубопровод · отдельно");
  await choose(page, "Продукт трубопровода", "Нефть");
  await choose(page, "Профиль груза", "Нефть");
  await page
    .getByRole("button", { name: "Рассчитать", exact: true })
    .first()
    .click();
  await expect(page.getByRole("status")).toContainText("Результат получен");
  expect(mock.request).toMatchObject({
    analysis_kind: "intercity_cargo",
    cargo_modes: ["pipeline"],
    cargo_transport: "pipeline",
    pipeline_product: "oil",
  });
});
test("API: mobile filters and chat are keyboard accessible without overflow", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await mockApi(page, { emptyProposals: true });
  await page.goto("/?data=api#atlas");
  await page.getByRole("button", { name: "Фильтры", exact: true }).click();
  await cityRun(page);
  await expect(page.getByRole("status")).toContainText("Результат получен");
  await page.getByRole("button", { name: "Агент", exact: true }).click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog")).toHaveCount(0);
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({ path: "test-results/api-mobile.png" });
});

test("API: pending extras keep the index, absent geometry and a failed rerun preserve the saved result", async ({
  page,
}) => {
  await mockApi(page, { emptyProposals: true });
  let fail = false;
  await page.route("**/api/v1/**", async (route) => {
    const req = route.request(),
      path = new URL(req.url()).pathname;
    if (fail && req.method() === "POST" && path === "/api/v1/analyses")
      return route.fulfill({
        status: 503,
        json: {
          error: {
            code: "engine_unavailable",
            message: "Engine not ready",
            field_errors: [],
            request_id: "test-503",
            retryable: false,
          },
        },
      });
    if (
      req.method() === "GET" &&
      path === "/api/v1/analyses/" + urban.analysis_id
    ) {
      const data = structuredClone(urban) as AnalysisResult;
      delete data.map_data;
      data.counts = [
        {
          metric_id: "pending-count",
          status: "pending",
          value: null,
          threshold_seconds: null,
          reason_code: "PENDING",
          dependency_job_id: "count-job",
          evidence_refs: [],
        },
      ];
      return route.fulfill({ json: data });
    }
    return route.fallback();
  });
  await page.goto("/?data=api#atlas");
  await cityRun(page);
  await expect(page.getByRole("status")).toContainText("Результат получен");
  await expect(page.locator(".api-card")).toContainText("50 из 100");
  await expect(page.locator(".api-map-note")).toContainText(
    "Геометрия недоступна",
  );
  await page.getByText("Дополнительные показатели", { exact: true }).click();
  await expect(page.locator(".api-card")).toContainText("Вычисляется");
  await expect(page.locator(".api-card")).not.toContainText("count-job");
  fail = true;
  await choose(page, "Передвижение", "Пешком");
  await page
    .getByRole("button", { name: "Рассчитать", exact: true })
    .first()
    .click();
  await expect(
    page.getByRole("alert").filter({ hasText: "Engine not ready" }),
  ).toBeVisible();
  await expect(page.locator(".api-card")).toContainText("50 из 100");
  await expect(page.getByRole("status")).toContainText("прежний результат");
  await expect(page.locator("body")).not.toContainText("44 мин");
});

test("API: urban choices follow capabilities while intercity retains the national catalog", async ({ page }) => {
  const extra = { ...territories[0], territory_id: "city-extra", name: "Казань" } as Territory;
  const mock = await mockApi(page, { territories: [...territories, extra] as Territory[], emptyProposals: true });
  await page.goto("/?data=api#atlas");
  await page.getByRole("combobox", { name: "Город", exact: true }).click();
  await expect(page.getByRole("option", { name: "Казань", exact: true })).toHaveCount(0);
  await page.keyboard.press("Escape");
  await page.getByRole("button", { name: "Пассажирские связи", exact: true }).click();
  await page.getByRole("combobox", { name: "Территория А", exact: true }).click();
  await expect(page.getByRole("option", { name: "Казань", exact: true })).toBeVisible();
  expect(mock.posts).toBe(0);
});

test("API: overlapping snapshots require a choice, send its identity and clear stale choices", async ({ page }) => {
  const c = structuredClone(capabilities) as Schemas["Capabilities"];
  c.snapshots = ["version-A-same-tail", "version-B-same-tail"].map((id) => ({
    ...c.snapshots![0], snapshot_id: id,
  }));
  c.snapshots.push({ ...c.snapshots[0], snapshot_id: "unknown-dates", valid_from: null });
  c.snapshots.push({ ...c.snapshots[0], snapshot_id: "wrong-geography", territory_ids: ["city-2"] });
  const mock = await mockApi(page, { capabilities: c, emptyProposals: true });
  await page.goto("/?data=api#atlas");
  await choose(page, "Город", "Москва");
  await expect(page.getByRole("combobox", { name: "Набор данных", exact: true })).toContainText("Выберите набор данных");
  await page.getByRole("button", { name: "Рассчитать", exact: true }).first().click();
  await expect(page.locator("#apiFilters")).toContainText("Выберите один из доступных наборов");
  expect(mock.posts).toBe(0);
  await page.getByRole("combobox", { name: "Набор данных", exact: true }).click();
  await expect(page.getByRole("option")).toHaveCount(3);
  await page.getByRole("option").filter({ hasText: "версия A-same-tail" }).click();
  await page.getByRole("button", { name: "Рассчитать", exact: true }).first().click();
  await expect(page.getByRole("status")).toContainText("Результат получен");
  expect(mock.request).toMatchObject({ snapshot_id: "version-A-same-tail" });
  await choose(page, "Город", "Калининград");
  await expect(page.getByRole("combobox", { name: "Набор данных", exact: true })).toContainText("Выберите набор данных");
  await expect(page.getByRole("status")).toContainText("прежний результат");
  expect(mock.posts).toBe(1);
});

test("API: unknown snapshot dates cannot be submitted and sole coverage is visible without another step", async ({ page }) => {
  await mockApi(page, { emptyProposals: true });
  await page.goto("/?data=api#atlas");
  await choose(page, "Город", "Москва");
  await expect(page.locator(".api-snapshot-hint")).toContainText("выбран автоматически");
  await expect(page.locator(".api-snapshot-coverage")).toContainText("01.01.1900–31.12.2100");
  await cityRun(page);
  await expect(page.getByRole("status")).toContainText("Результат получен");
  const c = structuredClone(capabilities) as Schemas["Capabilities"];
  c.snapshots![0].valid_to = null;
  await page.route("**/api/v1/capabilities", (route) => route.fulfill({ json: c }));
  await page.reload();
  await choose(page, "Город", "Москва");
  await expect(page.locator(".api-snapshot-hint")).toContainText("Нет подтверждённых данных");
  await expect(page.getByRole("button", { name: "Рассчитать", exact: true }).first()).toBeDisabled();
});

test("API: pipeline profile choices track the chosen product and reset on product changes", async ({ page }) => {
  const c = structuredClone(capabilities) as Schemas["Capabilities"];
  c.cargo_profiles.push({ kind: "pipeline", product: "gas", profile_id: "pipe-gas" });
  const mock = await mockApi(page, { capabilities: c, emptyProposals: true });
  await page.goto("/?data=api#atlas");
  await page.getByRole("button", { name: "Грузовые связи", exact: true }).click();
  await choose(page, "Территория А", "Москва");
  await choose(page, "Территория Б", "Калининград");
  await choose(page, "Тип грузового анализа", "Трубопровод · отдельно");
  await choose(page, "Продукт трубопровода", "Нефть");
  await page.getByRole("combobox", { name: "Профиль груза", exact: true }).click();
  await expect(page.getByRole("option", { name: "Газ", exact: true })).toHaveCount(0);
  await page.getByRole("option", { name: "Нефть", exact: true }).click();
  await choose(page, "Продукт трубопровода", "Газ");
  await expect(page.getByRole("combobox", { name: "Профиль груза", exact: true })).toContainText("Выберите профиль груза");
  await choose(page, "Профиль груза", "Газ");
  await page.getByRole("button", { name: "Рассчитать", exact: true }).first().click();
  await expect(page.getByRole("status")).toContainText("Результат получен");
  expect(mock.request).toMatchObject({ cargo_profile_id: "pipe-gas", pipeline_product: "gas" });
});

test("API: selected point and destination scope return times and partial map assessments stay identified", async ({ page }) => {
  const data = returnContextFixture();
  await mockApi(page, { result: data, emptyProposals: true });
  await page.route("**/explanation", (route) => route.fulfill({ json: {
    analysis_id: data.analysis_id, analysis_result_hash: data.result_hash,
    status: "template", model_id: null, reason_code: null, request_id: "synthetic-explanation",
    findings: [{ text: "Синтетический контекст для проверки выбора.", metric_ids: ["demo-time", "destination-time"], claims: [], evidence_refs: [] }],
    evidence_refs: [], proposals: [], limitations: [],
  } }));
  await page.goto("/?data=api#atlas");
  await cityRun(page);
  await expect(page.getByRole("status")).toContainText("Результат получен");
  await expect(page.getByLabel("Условные обозначения карты")).toContainText("частичная оценка");
  await page.getByRole("button", { name: "Агент", exact: true }).click();
  await page.getByLabel("Вопрос агенту").fill("Покажи контекст");
  await page.getByRole("button", { name: "Отправить", exact: true }).click();
  await page.getByRole("button", { name: "Synthetic elapsed travel time: 30 мин", exact: true }).click();
  await page.getByText("Дополнительные показатели", { exact: true }).click();
  await expect(page.locator(".api-card")).toContainText("18:00");
  await expect(page.locator(".api-card")).toContainText("20:00");
  await expect(page.locator(".api-card")).not.toContainText("19:00");
  await expect(page.locator(".api-card")).toContainText("Частичная оценка");
  await page.getByRole("button", { name: "Synthetic destination time: 30 мин", exact: true }).click();
  await expect(page.locator(".api-card")).toContainText("18:00");
  await expect(page.locator(".api-card")).toContainText("19:00");
  await expect(page.locator(".api-card")).not.toContainText("20:00");
  await expect(page.locator(".api-card")).toContainText("Выбранная точка → Школа А");
});

test("API: score-only explanations use saved zero and partial labels, and null remains neutral", async ({ page }) => {
  const data = structuredClone(urban) as AnalysisResult;
  data.scores[0] = { ...data.scores[0], name: "urban_accessibility_index", value: 0, completeness: "partial", ranking_eligible: false };
  await mockApi(page, { result: data, emptyProposals: true });
  await page.route("**/explanation", (route) => route.fulfill({ json: {
    analysis_id: data.analysis_id, analysis_result_hash: data.result_hash,
    status: "template", model_id: null, reason_code: null, request_id: "synthetic-score-explanation",
    findings: [{ text: "Сохранённый частичный индекс.", metric_ids: [], claims: [], evidence_refs: [], score_ids: [data.scores[0].score_id], score_claims: [{ score_id: data.scores[0].score_id, value: 99 }] }],
    evidence_refs: [], proposals: [], limitations: [],
  } }));
  await page.goto("/?data=api#atlas");
  await cityRun(page);
  await expect(page.getByRole("status")).toContainText("Результат получен");
  await expect(page.locator('.api-measure b[data-score-band="low"]')).toHaveText("0 из 100");
  await page.getByRole("button", { name: "Агент", exact: true }).click();
  await page.getByLabel("Вопрос агенту").fill("Объясни индекс");
  await page.getByRole("button", { name: "Отправить", exact: true }).click();
  await expect(page.locator('.api-claim[data-score-band="low"]')).toHaveText("Индекс доступности: 0 из 100 · частичная оценка");
  await expect(page.locator(".api-chat-log")).not.toContainText("99 из 100");
  data.scores[0] = { ...data.scores[0], value: null, status: "unknown" };
  await page.route(`**/analyses/${data.analysis_id}`, (route) => route.fulfill({ json: data }));
  await page.getByRole("button", { name: "Рассчитать", exact: true }).first().click();
  await expect(page.locator('.api-measure b[data-score-band="unknown"]')).toHaveText("Нет данных");
  await expect(page.locator(".api-card")).not.toContainText("0 из 100");
});

test("API: landing identifies illustrative examples without labeling API results as demo", async ({ page }) => {
  await page.goto("/?data=api");
  await expect(page.locator(".method-note")).toContainText("Числа в примерах на этой странице демонстрационные");
  await expect(page.locator(".lp-footer")).toContainText("Атлас — результаты API");
  await expect(page.locator(".lp-footer")).not.toContainText("Данные — демонстрационные");
  await page.goto("/?data=demo");
  await expect(page.locator(".lp-footer")).toContainText("Данные — демонстрационные");
});

test("API: scenario displays the changed nonwinner route and worsening, then restores baseline", async ({ page }) => {
  const data = scenarioRouteFixture();
  await mockApi(page, { result: data.baseline, scenarioResult: data.scenario });
  await page.goto("/?data=api#atlas");
  await cityRun(page);
  await expect(page.getByRole("status")).toContainText("Результат получен");
  const otherRoute = page.locator(".api-card .api-journey").last();
  await otherRoute.locator("summary").click();
  await expect(otherRoute).toContainText("Другая школа · 40 мин");
  await page.getByRole("button", { name: "Агент", exact: true }).click();
  await page.getByRole("button", { name: "Проверить сценарий", exact: true }).click();
  await expect(page.locator(".api-card")).toContainText("Сравнение до / после");
  await page.getByRole("button", { name: "Сценарий", exact: true }).click();
  await expect(page.locator(".api-card .api-journey summary")).toHaveText([
    "Путь сценария · Данные доступны · геометрия источника", "Путь сценария · Данные доступны · геометрия источника",
  ]);
  await otherRoute.locator("summary").click();
  await expect(otherRoute).toContainText("Другая школа · 45 мин");
  await expect(page.locator(".api-card")).toContainText("Ухудшение · -5 мин");
  await expect(page.locator(".api-overlay-note")).toContainText("Показаны пути сценария");
  await page.getByRole("button", { name: "База", exact: true }).click();
  await otherRoute.locator("summary").click();
  await expect(otherRoute).toContainText("Другая школа · 40 мин");
  await expect(page.locator(".api-card")).not.toContainText("Другая школа · 45 мин");
  await expect(page.locator(".api-overlay-note")).toHaveCount(0);
});

test("API: scenario without route geometry preserves after time and explicitly labels missing geometry", async ({ page }) => {
  const data = scenarioRouteFixture();
  data.baseline.journeys[0].geometry = null;
  data.baseline.journeys[0].geometry_kind = "none";
  data.scenario.after_journeys![0].geometry = null;
  data.scenario.after_journeys![0].geometry_kind = "none";
  await mockApi(page, { result: data.baseline, scenarioResult: data.scenario });
  await page.goto("/?data=api#atlas");
  await cityRun(page);
  await expect(page.getByRole("status")).toContainText("Результат получен");
  await page.getByRole("button", { name: "Агент", exact: true }).click();
  await page.getByRole("button", { name: "Проверить сценарий", exact: true }).click();
  await expect(page.locator(".api-card")).toContainText("Сравнение до / после");
  await page.getByRole("button", { name: "Сценарий", exact: true }).click();
  await expect(page.locator(".api-overlay-note")).toContainText("Геометрия путей сценария не предоставлена");
  const route = page.locator(".api-card .api-journey").last();
  await route.locator("summary").click();
  await expect(route).toContainText("геометрия отсутствует");
  await expect(route).toContainText("Другая школа · 45 мин");
  await expect(route).toContainText("Движение · Общественный транспорт · 45 мин");
});

test("API: scenario connection events retain new and lost labels without invented numeric savings", async ({ page }) => {
  const data = scenarioRouteFixture();
  data.scenario.deltas.push(...(["new_connection", "lost_connection"] as const).map((outcome) => ({
    ...data.scenario.deltas[0], effect_id: outcome, outcome, delta: null,
  })));
  await mockApi(page, { result: data.baseline, scenarioResult: data.scenario });
  await page.goto("/?data=api#atlas");
  await cityRun(page);
  await expect(page.getByRole("status")).toContainText("Результат получен");
  await page.getByRole("button", { name: "Агент", exact: true }).click();
  await page.getByRole("button", { name: "Проверить сценарий", exact: true }).click();
  await expect(page.locator(".api-card")).toContainText("Связь появилась · Без численной разности");
  await expect(page.locator(".api-card")).toContainText("Связь потеряна · Без численной разности");
  await expect(page.locator(".api-card")).toContainText("Ухудшение · -5 мин");
});

test("API: partial point index stays numerical and scoped while the city total is unknown", async ({ page }) => {
  const data = structuredClone(urban) as AnalysisResult;
  data.status = "partial";
  data.scores[0] = { ...data.scores[0], name: "urban_accessibility_index", value: 72.5, completeness: "partial", ranking_eligible: false };
  data.scores.unshift({ ...data.scores[0], score_id: "city-total", value: null, status: "unknown", subject: { kind: "territory", territory_id: "city-1" } });
  const mock = await mockApi(page, { result: data, emptyProposals: true });
  await page.goto("/?data=api#atlas");
  await cityRun(page);
  await expect(page.getByRole("status")).toContainText("Результат получен");
  const point = page.locator(".api-measure").filter({ hasText: "Точка анализа" });
  await expect(point.locator('b[data-score-band="medium"]')).toHaveText("72,5 из 100");
  await expect(page.locator(".api-measure").filter({ hasText: "Территория" }).locator('b[data-score-band="unknown"]')).toHaveText("Нет данных");
  await expect(page.locator(".api-card")).toContainText("Частичная оценка");
  await expect(page.locator(".api-card")).toContainText("не участвует в полном рейтинге");
  await page.screenshot({ path: "test-results/api-partial-point-index.png" });
  expect(mock.posts).toBe(1);
});

test("API: airport-only scope blocks unsupported controls and applies available parameters explicitly", async ({ page }) => {
  await page.clock.setFixedTime(new Date("2026-11-10T08:00:00Z"));
  const result = structuredClone(urban) as AnalysisResult;
  result.status = "partial";
  result.metrics[0].name = "minimum_journey_seconds";
  result.metrics[0].subject.category = "airport";
  result.metrics[0].subject.mode = "walk";
  result.scores[0] = { ...result.scores[0], name: "point_index", value: null, status: "unknown", completeness: "partial", ranking_eligible: false, subject: { kind: "sample_point", point_id: "demo-point", mode: "walk" }, reason_code: "MINIMUM_TIME_UNPROVEN" };
  const mock = await mockApi(page, { capabilities: scopedCapabilities(), result, emptyProposals: true });
  await page.goto("/?data=api#atlas");
  await choose(page, "Город", "Калининград");
  await expect(page.getByRole("combobox", { name: "Передвижение", exact: true })).toContainText("Общественный транспорт");
  await expect(page.getByRole("checkbox", { name: "Школы", exact: true })).toBeChecked();
  await expect(page.getByRole("button", { name: "Рассчитать", exact: true }).first()).toBeDisabled();
  await page.getByRole("combobox", { name: "Передвижение", exact: true }).click();
  await expect(page.getByRole("option", { name: "Общественный транспорт", exact: true })).toBeDisabled();
  await expect(page.getByRole("option", { name: "Пешком", exact: true })).toBeEnabled();
  await page.keyboard.press("Escape");
  expect(mock.posts).toBe(0);
  await page.getByRole("button", { name: "Использовать доступные параметры", exact: true }).click();
  await expect(page.getByRole("combobox", { name: "Передвижение", exact: true })).toContainText("Пешком");
  await expect(page.getByRole("checkbox", { name: "Аэропорты", exact: true })).toBeChecked();
  await expect(page.getByRole("checkbox", { name: "Школы", exact: true })).not.toBeChecked();
  await expect(page.getByRole("button", { name: "Рассчитать", exact: true }).first()).toBeEnabled();
  expect(mock.posts).toBe(0);
  await page.getByRole("button", { name: "Рассчитать", exact: true }).first().click();
  await expect(page.getByRole("status")).toContainText("Результат получен");
  expect(mock.request).toMatchObject({ city_id: "city-2", snapshot_id: "demo-snapshot", passenger_mode: "walk", categories: ["airport"] });
  await expect(page.locator('.api-measure b[data-score-band="unknown"]')).toHaveText("Нет данных");
  await expect(page.locator(".api-card")).toContainText("Полное время до объекта");
  await expect(page.locator(".api-card")).not.toContainText("minimum_journey_seconds");
});

test("API: categories from separate scopes cannot be combined", async ({ page }) => {
  await page.clock.setFixedTime(new Date("2026-11-10T08:00:00Z"));
  const mock = await mockApi(page, { capabilities: scopedCapabilities([airportScope(), { ...airportScope(), scope_id: "school-walk", categories: ["school"] }]) });
  await page.goto("/?data=api#atlas");
  await choose(page, "Город", "Калининград");
  await choose(page, "Передвижение", "Пешком");
  for (const name of ["Взрослые поликлиники", "Школы", "ТЦ"]) await page.getByRole("checkbox", { name, exact: true }).uncheck();
  await page.getByRole("checkbox", { name: "Аэропорты", exact: true }).check();
  await expect(page.getByRole("checkbox", { name: "Школы", exact: true })).toBeDisabled();
  await expect(page.locator("#apiFilters")).toContainText("Нет общего набора расчёта");
  await expect(page.getByRole("button", { name: "Рассчитать", exact: true }).first()).toBeEnabled();
  expect(mock.posts).toBe(0);
});

test("API: scope end is exclusive and does not silently reset selected time or mode", async ({ page }) => {
  await page.clock.setFixedTime(new Date("2026-11-10T08:00:00Z"));
  const scope = { ...airportScope(), valid_from: "2026-11-10T06:30:00Z", valid_to: "2026-11-10T07:00:00Z" };
  const mock = await mockApi(page, { capabilities: scopedCapabilities([scope]) });
  await page.goto("/?data=api#atlas");
  await page.getByRole("button", { name: "Использовать доступные параметры", exact: true }).click();
  await expect(page.getByRole("button", { name: "Рассчитать", exact: true }).first()).toBeEnabled();
  const time = page.getByRole("slider", { name: "Время отправления, шаг 15 минут" });
  await time.focus();
  await time.press("ArrowRight");
  await expect(page.getByRole("button", { name: "Рассчитать", exact: true }).first()).toBeEnabled();
  await time.press("ArrowRight");
  await expect(time).toHaveValue("36");
  await expect(page.getByRole("button", { name: "Рассчитать", exact: true }).first()).toBeDisabled();
  await expect(page.getByRole("combobox", { name: "Передвижение", exact: true })).toContainText("Пешком");
  await expect(page.getByRole("checkbox", { name: "Аэропорты", exact: true })).toBeChecked();
  await expect(page.locator("#apiFilters")).toContainText("Для этой даты и времени нет доступного набора расчёта");
  expect(mock.posts).toBe(0);
});

for (const detail of [null, undefined]) test(`API: ${detail === null ? "null" : "omitted"} scope detail preserves legacy controls`, async ({ page }) => {
  const c = scopedCapabilities(detail === null ? null : []);
  if (detail === undefined) delete c.urban_passenger_scopes;
  await mockApi(page, { capabilities: c, emptyProposals: true });
  await page.goto("/?data=api#atlas");
  await choose(page, "Город", "Москва");
  await expect(page.getByRole("button", { name: "Рассчитать", exact: true }).first()).toBeEnabled();
  await expect(page.getByLabel("Доступность городского расчёта")).toHaveCount(0);
});

test("API: an explicitly empty scope list disables launch with a reason", async ({ page }) => {
  const mock = await mockApi(page, { capabilities: scopedCapabilities([]) });
  await page.goto("/?data=api#atlas");
  await expect(page.getByLabel("Доступность городского расчёта")).toContainText("нет зарегистрированных наборов параметров");
  await expect(page.getByRole("button", { name: "Рассчитать", exact: true }).first()).toBeDisabled();
  await expect(page.getByRole("button", { name: "Использовать доступные параметры", exact: true })).toHaveCount(0);
  expect(mock.posts).toBe(0);
});

test("API: overlapping scopes cannot be resolved by arbitrarily choosing one", async ({ page }) => {
  await page.clock.setFixedTime(new Date("2026-11-10T08:00:00Z"));
  const mock = await mockApi(page, { capabilities: scopedCapabilities([airportScope(), { ...airportScope(), scope_id: "overlap-airport" }]) });
  await page.goto("/?data=api#atlas");
  await choose(page, "Город", "Калининград");
  await choose(page, "Передвижение", "Пешком");
  for (const name of ["Взрослые поликлиники", "Школы", "ТЦ"]) await page.getByRole("checkbox", { name, exact: true }).uncheck();
  await page.getByRole("checkbox", { name: "Аэропорты", exact: true }).check();
  await expect(page.getByRole("button", { name: "Рассчитать", exact: true }).first()).toBeDisabled();
  await expect(page.getByLabel("Доступность городского расчёта")).toContainText("подходят несколько наборов");
  expect(mock.posts).toBe(0);
});
