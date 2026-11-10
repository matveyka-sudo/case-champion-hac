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
import { pointOverviewFixture } from "../tests/fixtures/point-overview";
import { partialWalkGeometryFixture } from "../tests/fixtures/partial-walk-geometry";
import { observedRouteFixture } from "../tests/fixtures/observed-route";
import { legGeometryFixture } from "../tests/fixtures/leg-geometry";
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
  const group = page.getByRole("group", { name: label, exact: true });
  await group.or(page.getByRole("combobox", { name: label, exact: true })).first().waitFor();
  if (await group.count()) {
    const original = option === "Общественный транспорт" ? "Общ. транспорт" : option === "Автомобиль" && label === "Передвижение" ? "Авто" : option;
    await group.getByRole("button", { name: original, exact: true }).click();
  } else {
    await page.getByRole("combobox", { name: label, exact: true }).click();
    await page.getByRole("option", { name: option, exact: true }).click();
  }
}
async function categories(page: Page, selected: string[]) {
  for (const name of ["Взрослые поликлиники", "Детские поликлиники", "Детсады", "Школы", "Колледжи и вузы", "Ж/д вокзалы", "Автовокзалы", "Аэропорты", "ТЦ"]) {
    const button = page.locator("#apiFilters").getByRole("button", { name, exact: true });
    if ((await button.getAttribute("aria-pressed") === "true") !== selected.includes(name)) await button.click();
  }
}
async function airportParameters(page: Page, area?: string) {
  await choose(page, "Город", "Калининград");
  await choose(page, "Передвижение", "Пешком");
  await categories(page, ["Аэропорты"]);
  if (area) await choose(page, "Район", area);
}
async function openSources(page: Page) {
  const details = page.locator("#apiFilters details").filter({ has: page.locator('summary', { hasText: "Источники и условия данных" }) });
  if (!(await details.getAttribute("open"))) await details.locator("summary").first().click();
}
async function mockApi(
  page: Page,
  options: {
    errorStatus?: number;
    errorCode?: string;
    running?: boolean;
    emptyProposals?: boolean;
    samplePopulation?: number | null;
    capabilities?: Schemas["Capabilities"];
    territories?: Territory[];
    result?: Partial<AnalysisResult>;
    scenarioResult?: Partial<Schemas["ScenarioResult"]>;
    scenarioProposal?: Partial<Schemas["ScenarioProposal"]>;
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
            code: options.errorCode || (
              status === 422
                ? "invalid_request"
                : status === 409
                  ? "stored_result_incompatible"
                  : "engine_unavailable"),
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
              ...options.scenarioProposal,
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
    await expect(page.locator(".status")).toContainText("Результат получен");
    await page.getByRole("button", { name: "Агент", exact: true }).first().click();
    await page.getByLabel("Вопрос агенту").fill("Выбрать точку из сохранённой метрики");
    await page.getByRole("button", { name: "Отправить", exact: true }).click();
    await page.getByRole("button", { name: "Synthetic elapsed travel time: 30 мин", exact: true }).click();
    await expect(page.locator("#apiResultCard")).toContainText(
      `Население ячейки: ${population === null ? "неизвестно" : population} · 2025 ·`,
    );
    await page.getByText("Источник населения", { exact: true }).click();
    await expect(page.locator(".api-population")).toContainText("synthetic-test-only");
  });
}
test("API: complete analysis, actual map features, scoped explanation and separate scenario", async ({
  page,
}) => {
  const mock = await mockApi(page);
  await page.goto("/?data=api#atlas");
  await cityRun(page);
  await expect(page.locator(".status")).toContainText("Результат получен");
  await expect(page.locator("#apiResultCard")).toContainText("30 мин");
  await expect(page.locator(".at-map canvas")).toBeVisible();
  await page.getByRole("button", { name: "Агент", exact: true }).first().click();
  await page.getByLabel("Вопрос агенту").fill("Почему?");
  await page.getByRole("button", { name: "Отправить", exact: true }).click();
  await expect(page.locator(".ch-log")).toContainText(
    "Шаблонное объяснение",
  );
  await page
    .getByRole("button", { name: "Проверить сценарий", exact: true })
    .click();
  await expect(page.locator("#apiResultCard")).toContainText("Сравнение до / после");
  await page.getByRole("button", { name: "Сценарий", exact: true }).click();
  await expect(page.locator("#apiResultCard")).toContainText("28,3 мин");
  await page.getByRole("button", { name: "База", exact: true }).click();
  await expect(page.locator("#apiResultCard")).toContainText("30 мин");
  expect(
    mock.requests.filter((r) => r.url.endsWith("/runs"))[0].key,
  ).toBeTruthy();
  await expect(page.locator("#apiResultCard")).not.toContainText(urban.analysis_id);
  await expect(page.locator("#apiResultCard")).not.toContainText(urban.result_hash);
  await expect(page.locator("#apiResultCard")).not.toContainText(
    scenario.overlay_graph_id,
  );
  await expect(page.locator("#apiResultCard")).not.toContainText(
    "TURNAROUND_UNVERIFIED",
  );
  await expect(page.locator("#apiResultCard")).toContainText(
    "Оборот транспорта и ресурсы",
  );
  await expect(page.locator(".at-chat")).not.toContainText(urban.analysis_id);
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
  await expect(page.locator("#apiResultCard")).toHaveCount(0);
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
    .getByRole("button", { name: "Отменить" })
    .click();
  await expect(page.locator(".status")).toContainText("Задание отменено");
  expect(mock.requests.some((r) => r.url.endsWith("/cancel"))).toBe(true);
  await expect(page.locator("#apiResultCard")).toHaveCount(0);
});
test("API: four analysis kinds send canonical requests including independent pipeline", async ({
  page,
}) => {
  const mock = await mockApi(page, { emptyProposals: true });
  await page.goto("/?data=api#atlas");
  await cityRun(page);
  await expect(page.locator(".status")).toContainText("Результат получен");
  expect(mock.request).toMatchObject({
    analysis_kind: "urban_passenger",
    city_id: "city-1",
  });
  await page
    .getByRole("tab", { name: "Пассажирские связи", exact: true })
    .click();
  await choose(page, "Территория А", "Москва");
  await choose(page, "Территория Б", "Калининград");
  await page
    .getByRole("button", { name: "Рассчитать", exact: true })
    .first()
    .click();
  await expect(page.locator(".status")).toContainText("Результат получен");
  expect(mock.request).toMatchObject({
    analysis_kind: "intercity_passenger",
    origin_id: "city-1",
    destination_id: "city-2",
  });
  await page
    .getByRole("tab", { name: "Грузовые связи", exact: true })
    .click();
  await choose(page, "Город", "Калининград");

  await page
    .getByRole("button", { name: "Рассчитать", exact: true })
    .first()
    .click();
  await expect(page.locator(".status")).toContainText("Результат получен");
  expect(mock.request).toMatchObject({
    analysis_kind: "urban_cargo",
    city_id: "city-2",
  });
  await page
    .getByRole("tab", { name: "Грузовые связи", exact: true })
    .click();
  await choose(page, "Режим грузовых связей", "Между территориями");
  await choose(page, "Территория А", "Москва");
  await choose(page, "Территория Б", "Калининград");
  await choose(page, "Обработка", "Наливной");
  await page.getByRole("button", { name: "Трубопровод", exact: true }).click();
  await choose(page, "Продукт трубопровода", "Нефть");

  await page
    .getByRole("button", { name: "Рассчитать", exact: true })
    .first()
    .click();
  await expect(page.locator(".status")).toContainText("Результат получен");
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
  await page.getByRole("button", { name: "Фильтры", exact: true }).first().click();
  await cityRun(page);
  await expect(page.locator(".status")).toContainText("Результат получен");
  await page.getByRole("button", { name: "Агент", exact: true }).first().click();
  await expect(page.locator("#chat")).toHaveAttribute("role", "dialog");
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
  await expect(page.locator(".status")).toContainText("Результат получен");
  await expect(page.locator("#apiResultCard")).toContainText("50 из 100");
  await expect(page.locator(".api-map-note")).toContainText(
    "Геометрия недоступна",
  );
  await page.getByText("Дополнительные показатели", { exact: true }).click();
  await expect(page.locator("#apiResultCard")).toContainText("Вычисляется");
  await expect(page.locator("#apiResultCard")).not.toContainText("count-job");
  fail = true;
  await choose(page, "Передвижение", "Пешком");
  await page
    .getByRole("button", { name: "Рассчитать", exact: true })
    .first()
    .click();
  await expect(
    page.getByRole("alert").filter({ hasText: "Engine not ready" }),
  ).toBeVisible();
  await expect(page.locator("#apiResultCard")).toContainText("50 из 100");
  await expect(page.locator(".status")).toContainText("прежний результат");
  await expect(page.locator("body")).not.toContainText("44 мин");
});

test("API: urban choices follow capabilities while intercity retains the national catalog", async ({ page }) => {
  const extra = { ...territories[0], territory_id: "city-extra", name: "Казань" } as Territory;
  const mock = await mockApi(page, { territories: [...territories, extra] as Territory[], emptyProposals: true });
  await page.goto("/?data=api#atlas");
  await expect(page.getByRole("group", { name: "Город", exact: true }).getByRole("button", { name: "Казань" })).toHaveCount(0);
  await page.getByRole("tab", { name: "Пассажирские связи", exact: true }).click();
  await page.getByRole("combobox", { name: "Территория А", exact: true }).click();
  await expect(page.getByRole("option", { name: "Казань", exact: true })).toBeVisible();
  expect(mock.posts).toBe(0);
});

test("API: overlapping snapshots bind only through a unique matching canonical scope", async ({ page }) => {
  await page.clock.setFixedTime(new Date("2026-11-10T08:00:00Z"));
  const c = structuredClone(capabilities) as Schemas["Capabilities"];
  c.snapshots = ["version-A-same-tail", "version-B-same-tail"].map((snapshot_id) => ({ ...c.snapshots![0], snapshot_id }));
  const mock = await mockApi(page, { capabilities: c, emptyProposals: true });
  await page.goto("/?data=api#atlas");
  await cityRun(page);
  await expect(page.locator(".statecard")).toContainText("данные пока не определены однозначно");
  expect(mock.posts).toBe(0);
  await expect(page.getByRole("combobox", { name: "Набор данных", exact: true })).toHaveCount(0);
  c.urban_passenger_scopes = [{ ...airportScope(), city_id: "city-1", snapshot_id: "version-A-same-tail", passenger_modes: ["public_transport"], categories: ["adult_polyclinic", "school", "shopping_center"] }];
  await page.route("**/api/v1/capabilities", (route) => route.fulfill({ json: c }));
  await page.reload();
  await cityRun(page);
  await expect(page.locator(".status")).toContainText("Результат получен");
  expect(mock.request).toMatchObject({ snapshot_id: "version-A-same-tail" });
  await choose(page, "Город", "Калининград");
  await expect(page.locator("#apiResultCard")).toHaveCount(0);
  await expect(page.getByRole("group", { name: "Передвижение", exact: true }).locator('[aria-pressed="true"]')).toHaveCount(0);
  expect(mock.posts).toBe(1);
});

test("API: unknown snapshot dates cannot be submitted and sole coverage needs no technical selection", async ({ page }) => {
  const mock = await mockApi(page, { emptyProposals: true });
  await page.goto("/?data=api#atlas");
  await cityRun(page);
  await expect(page.locator(".status")).toContainText("Результат получен");
  expect(mock.request).toMatchObject({ snapshot_id: "demo-snapshot" });
  const c = structuredClone(capabilities) as Schemas["Capabilities"];
  c.snapshots![0].valid_to = null;
  await page.route("**/api/v1/capabilities", (route) => route.fulfill({ json: c }));
  await page.reload();
  await cityRun(page);
  await expect(page.locator(".statecard")).toContainText("Нет подтверждённого набора данных");
  expect(mock.posts).toBe(1);
});

test("API: original pipeline product controls bind the exact registered product profile", async ({ page }) => {
  const c = structuredClone(capabilities) as Schemas["Capabilities"];
  c.cargo_profiles.push({ kind: "pipeline", product: "gas", profile_id: "pipe-gas" });
  const mock = await mockApi(page, { capabilities: c, emptyProposals: true });
  await page.goto("/?data=api#atlas");
  await page.getByRole("tab", { name: "Грузовые связи", exact: true }).click();
  await choose(page, "Режим грузовых связей", "Между территориями");
  await choose(page, "Территория А", "Москва");
  await choose(page, "Территория Б", "Калининград");
  await choose(page, "Обработка", "Наливной");
  await page.getByRole("button", { name: "Трубопровод", exact: true }).click();
  await choose(page, "Продукт трубопровода", "Нефть");
  await choose(page, "Продукт трубопровода", "Газ");
  await page.getByRole("button", { name: "Рассчитать", exact: true }).first().click();
  await expect(page.locator(".status")).toContainText("Результат получен");
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
  await expect(page.locator(".status")).toContainText("Результат получен");
  await expect(page.getByLabel("Условные обозначения карты")).toContainText("частичная оценка");
  await page.getByRole("button", { name: "Агент", exact: true }).first().click();
  await page.getByLabel("Вопрос агенту").fill("Покажи контекст");
  await page.getByRole("button", { name: "Отправить", exact: true }).click();
  await page.getByRole("button", { name: "Synthetic elapsed travel time: 30 мин", exact: true }).click();
  await page.getByText("Дополнительные показатели", { exact: true }).click();
  await expect(page.locator("#apiResultCard")).toContainText("18:00");
  await expect(page.locator("#apiResultCard")).toContainText("20:00");
  await expect(page.locator("#apiResultCard")).not.toContainText("19:00");
  await expect(page.locator("#apiResultCard")).toContainText("Частичная оценка");
  await page.getByRole("button", { name: "Synthetic destination time: 30 мин", exact: true }).click();
  await expect(page.locator("#apiResultCard")).toContainText("18:00");
  await expect(page.locator("#apiResultCard")).toContainText("19:00");
  await expect(page.locator("#apiResultCard")).not.toContainText("20:00");
  await expect(page.locator("#apiResultCard")).toContainText("Выбранная точка → Школа А");
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
  await expect(page.locator(".status")).toContainText("Результат получен");
  await expect(page.locator('.api-measure b[data-score-band="low"]')).toHaveText("0 из 100");
  await page.getByRole("button", { name: "Агент", exact: true }).first().click();
  await page.getByLabel("Вопрос агенту").fill("Объясни индекс");
  await page.getByRole("button", { name: "Отправить", exact: true }).click();
  await expect(page.locator('.api-claim[data-score-band="low"]')).toHaveText("Индекс доступности: 0 из 100 · частичная оценка");
  await expect(page.locator(".ch-log")).not.toContainText("99 из 100");
  data.scores[0] = { ...data.scores[0], value: null, status: "unknown" };
  await page.route(`**/analyses/${data.analysis_id}`, (route) => route.fulfill({ json: data }));
  await page.getByRole("button", { name: "Рассчитать", exact: true }).first().click();
  await expect(page.locator('.api-measure b[data-score-band="unknown"]')).toHaveText("Нет данных");
  await expect(page.locator("#apiResultCard")).not.toContainText("0 из 100");
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
  await expect(page.locator(".status")).toContainText("Результат получен");
  const otherRoute = page.locator("#apiResultCard .api-journey").last();
  await otherRoute.locator("summary").click();
  await expect(otherRoute).toContainText("Другая школа · 40 мин");
  await page.getByRole("button", { name: "Агент", exact: true }).first().click();
  await page.getByRole("button", { name: "Проверить сценарий", exact: true }).click();
  await expect(page.locator("#apiResultCard")).toContainText("Сравнение до / после");
  await page.getByRole("button", { name: "Сценарий", exact: true }).click();
  await expect(page.locator("#apiResultCard .api-journey summary")).toHaveText([
    "Путь сценария · Данные доступны · геометрия источника", "Путь сценария · Данные доступны · геометрия источника",
  ]);
  await otherRoute.locator("summary").click();
  await expect(otherRoute).toContainText("Другая школа · 45 мин");
  await expect(page.locator("#apiResultCard")).toContainText("Ухудшение · -5 мин");
  await expect(page.locator(".api-overlay-note")).toContainText("Показаны пути сценария");
  await page.getByRole("button", { name: "База", exact: true }).click();
  await otherRoute.locator("summary").click();
  await expect(otherRoute).toContainText("Другая школа · 40 мин");
  await expect(page.locator("#apiResultCard")).not.toContainText("Другая школа · 45 мин");
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
  await expect(page.locator(".status")).toContainText("Результат получен");
  await page.getByRole("button", { name: "Агент", exact: true }).first().click();
  await page.getByRole("button", { name: "Проверить сценарий", exact: true }).click();
  await expect(page.locator("#apiResultCard")).toContainText("Сравнение до / после");
  await page.getByRole("button", { name: "Сценарий", exact: true }).click();
  await expect(page.locator(".api-overlay-note")).toContainText("Геометрия путей сценария не предоставлена");
  const route = page.locator("#apiResultCard .api-journey").last();
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
  await expect(page.locator(".status")).toContainText("Результат получен");
  await page.getByRole("button", { name: "Агент", exact: true }).first().click();
  await page.getByRole("button", { name: "Проверить сценарий", exact: true }).click();
  await expect(page.locator("#apiResultCard")).toContainText("Связь появилась · Без численной разности");
  await expect(page.locator("#apiResultCard")).toContainText("Связь потеряна · Без численной разности");
  await expect(page.locator("#apiResultCard")).toContainText("Ухудшение · -5 мин");
});

test("API: partial point index stays numerical and scoped while the city total is unknown", async ({ page }) => {
  const data = structuredClone(urban) as AnalysisResult;
  data.status = "partial";
  data.scores[0] = { ...data.scores[0], name: "urban_accessibility_index", value: 72.5, completeness: "partial", ranking_eligible: false };
  data.scores.unshift({ ...data.scores[0], score_id: "city-total", value: null, status: "unknown", subject: { kind: "territory", territory_id: "city-1" } });
  const mock = await mockApi(page, { result: data, emptyProposals: true });
  await page.goto("/?data=api#atlas");
  await cityRun(page);
  await expect(page.locator(".status")).toContainText("Результат получен");
  const point = page.locator(".api-measure").filter({ hasText: "Точка анализа" });
  await expect(point.locator('b[data-score-band="medium"]')).toHaveText("72,5 из 100");
  await expect(page.locator(".api-measure").filter({ hasText: "Территория" }).locator('b[data-score-band="unknown"]')).toHaveText("Нет данных");
  await expect(page.locator("#apiResultCard")).toContainText("Частичная оценка");
  await expect(page.locator("#apiResultCard")).toContainText("не участвует в полном рейтинге");
  await page.screenshot({ path: "test-results/api-partial-point-index.png" });
  expect(mock.posts).toBe(1);
});

test("API: airport-only scope preserves original choices and reports an unavailable action without silently switching", async ({ page }) => {
  await page.clock.setFixedTime(new Date("2026-11-10T08:00:00Z"));
  const mock = await mockApi(page, { capabilities: scopedCapabilities(), result: legGeometryFixture(null), emptyProposals: true });
  await page.goto("/?data=api#atlas");
  await choose(page, "Город", "Калининград");
  await expect(page.getByRole("group", { name: "Передвижение", exact: true }).getByRole("button", { name: "Общ. транспорт" })).toBeEnabled();
  await expect(page.getByRole("button", { name: "Школы", exact: true })).toHaveAttribute("aria-pressed", "true");
  await page.getByRole("button", { name: "Рассчитать", exact: true }).first().click();
  await expect(page.locator(".statecard")).toContainText("способ передвижения недоступен");
  expect(mock.posts).toBe(0);
  await airportParameters(page);
  await page.getByRole("button", { name: "Рассчитать", exact: true }).first().click();
  await expect(page.locator(".status")).toContainText("Результат получен");
  expect(mock.request).toMatchObject({ city_id: "city-2", snapshot_id: "demo-snapshot", passenger_mode: "walk", categories: ["airport"] });
  await expect(page.locator("#apiResultCard")).not.toContainText("minimum_journey_seconds");
});

test("API: categories from separate scopes cannot be combined while all original choices remain selectable", async ({ page }) => {
  await page.clock.setFixedTime(new Date("2026-11-10T08:00:00Z"));
  const mock = await mockApi(page, { capabilities: scopedCapabilities([airportScope(), { ...airportScope(), scope_id: "school-walk", categories: ["school"] }]) });
  await page.goto("/?data=api#atlas");
  await airportParameters(page);
  await page.getByRole("button", { name: "Школы", exact: true }).click();
  await expect(page.getByRole("button", { name: "Школы", exact: true })).toHaveAttribute("aria-pressed", "true");
  await page.getByRole("button", { name: "Рассчитать", exact: true }).first().click();
  await expect(page.locator(".statecard")).toContainText("Нет одного набора расчёта");
  expect(mock.posts).toBe(0);
});

test("API: scope end is exclusive and does not silently reset selected time or mode", async ({ page }) => {
  await page.clock.setFixedTime(new Date("2026-11-10T08:00:00Z"));
  const mock = await mockApi(page, { capabilities: scopedCapabilities([{ ...airportScope(), valid_from: "2026-11-10T06:30:00Z", valid_to: "2026-11-10T07:00:00Z" }]) });
  await page.goto("/?data=api#atlas");
  await airportParameters(page);
  const time = page.getByRole("slider", { name: "Время отправления, шаг 15 минут" });
  await time.fill("36");
  await page.getByRole("button", { name: "Рассчитать", exact: true }).first().click();
  await expect(time).toHaveValue("36");
  await expect(page.getByRole("group", { name: "Передвижение", exact: true }).getByRole("button", { name: "Пешком" })).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByRole("button", { name: "Аэропорты", exact: true })).toHaveAttribute("aria-pressed", "true");
  await expect(page.locator(".statecard")).toContainText("Для этой даты и времени нет доступного набора расчёта");
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

test("API: an explicitly empty scope list reports unavailable launch while preserving original controls", async ({ page }) => {
  const mock = await mockApi(page, { capabilities: scopedCapabilities([]) });
  await page.goto("/?data=api#atlas");
  await cityRun(page);
  await expect(page.locator(".statecard")).toContainText("нет зарегистрированных наборов параметров");
  expect(mock.posts).toBe(0);
});

test("API: overlapping scopes cannot be resolved by arbitrarily choosing one", async ({ page }) => {
  await page.clock.setFixedTime(new Date("2026-11-10T08:00:00Z"));
  const mock = await mockApi(page, { capabilities: scopedCapabilities([airportScope(), { ...airportScope(), scope_id: "overlap-airport" }]) });
  await page.goto("/?data=api#atlas");
  await airportParameters(page);
  await page.getByRole("button", { name: "Рассчитать", exact: true }).first().click();
  await expect(page.locator(".statecard")).toContainText("подходят несколько наборов");
  expect(mock.posts).toBe(0);
});

for (const mobile of [false, true]) test(`API: calculated point overview reveals the numeric total explicitly (${mobile ? "mobile keyboard" : "desktop"})`, async ({ page }) => {
  if (mobile) await page.setViewportSize({ width: 390, height: 844 });
  await page.clock.setFixedTime(new Date("2026-11-10T08:00:00Z"));
  const data = pointOverviewFixture();
  const mock = await mockApi(page, { result: data, emptyProposals: true });
  await page.goto("/?data=api#atlas");
  if (mobile) await page.getByRole("button", { name: "Фильтры", exact: true }).first().click();
  await choose(page, "Город", "Калининград");
  await choose(page, "Передвижение", "Пешком");
  await categories(page, ["Аэропорты"]);
  await page.getByRole("button", { name: "Рассчитать", exact: true }).first().click();
  await expect(page.locator(".status")).toContainText("Результат получен");
  if ((page.viewportSize()?.width || 1440) < 768) await page.getByText("Результат и рассчитанные точки", { exact: true }).click();
  const card = page.locator("#apiResultCard");
  const overview = page.getByLabel("Обзор рассчитанных точек");
  await expect(overview).toContainText("Индекс города: Нет данных");
  await expect(card.locator(".api-measure")).toHaveCount(0);
  await expect(card.getByText("Все показатели (650)", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Все показатели", exact: true })).toHaveCount(0);
  await expect(card.locator("h3")).toHaveText("Калининград");
  const summary = overview.locator("summary");
  await expect(summary).toHaveText("Рассчитанные точки (1 из 216)");
  if (mobile) { await summary.focus(); await summary.press("Enter"); }
  else await summary.click();
  const point = overview.getByRole("button", { name: /Точка · 54.71500, 20.51500/ });
  await expect(point).toContainText("0,5 из 100");
  await expect(point).toContainText("Частичная оценка");
  await expect(card.locator("h3")).toHaveText("Калининград");
  if (mobile) { await point.focus(); await point.press("Enter"); }
  else await point.click();
  await expect(card.locator("h3")).toHaveText("Точка · 54.71500, 20.51500");
  await expect(card.locator("h3")).toBeFocused();
  await expect(overview.locator("details")).toHaveJSProperty("open", false);
  await expect(card.locator(".api-measure")).toHaveCount(3);
  await expect(card).toContainText("Индекс точки");
  await expect(card).toContainText("229,6 мин");
  await expect(overview).toContainText("Индекс города: Нет данных");
  await expect(card).not.toContainText("synthetic-point");
  await expect(page.getByRole("slider", { name: "Время отправления, шаг 15 минут" })).toHaveValue("34");
  expect(mock.posts).toBe(1);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: `test-results/api-calculated-points-${mobile ? "mobile" : "desktop"}.png` });
  await page.getByRole("button", { name: "Все показатели", exact: true }).click();
  await card.getByText("Все показатели (650)", { exact: true }).click();
  await expect(card.locator(".api-measure")).toHaveCount(650);
  await expect(card.locator("h3")).toHaveText("Калининград");
  await expect(overview.locator("details")).toHaveJSProperty("open", false);
  expect(mock.posts).toBe(1);
});

test("API: partial point inventory never presents the one known point as complete coverage", async ({ page }) => {
  await page.clock.setFixedTime(new Date("2026-11-10T08:00:00Z"));
  const data = pointOverviewFixture();
  data.map_data!.features = data.map_data!.features!.filter((f) => f.entity_kind === "sample_point" && f.entity_id === "synthetic-point-215");
  const mock = await mockApi(page, { result: data, emptyProposals: true });
  await page.goto("/?data=api#atlas");
  await choose(page, "Город", "Калининград");
  await choose(page, "Передвижение", "Пешком");
  await categories(page, ["Аэропорты"]);
  await page.getByRole("button", { name: "Рассчитать", exact: true }).first().click();
  await expect(page.locator(".status")).toContainText("Результат получен");
  const overview = page.getByLabel("Обзор рассчитанных точек");
  await expect(overview.locator("summary")).toHaveText("Рассчитанные точки (1)");
  await overview.locator("summary").click();
  await expect(overview).toContainText("Переданная карта содержит не все точки из результата. Соотношение не определено.");
  await expect(overview).not.toContainText("1 из 1");
  await expect(overview).toContainText("Индекс города: Нет данных");
  await expect(overview.getByRole("button", { name: /Точка · 54.71500, 20.51500/ })).toContainText("0,5 из 100");
  expect(mock.posts).toBe(1);
});

test("API: calculated point reveals its partial walk route with a separate rounded display timeline", async ({ page }) => {
  // Synthetic protocol evidence; this does not prove a production route or index.
  await page.clock.setFixedTime(new Date("2026-11-10T08:00:00Z"));
  const data = partialWalkGeometryFixture();
  const mock = await mockApi(page, { result: data, emptyProposals: true });
  await page.goto("/?data=api#atlas");
  await choose(page, "Город", "Калининград");
  await choose(page, "Передвижение", "Пешком");
  await categories(page, ["Аэропорты"]);
  await page.getByRole("button", { name: "Рассчитать", exact: true }).first().click();
  await expect(page.locator(".status")).toContainText("Результат получен");
  if ((page.viewportSize()?.width || 1440) < 768) await page.getByText("Результат и рассчитанные точки", { exact: true }).click();
  const card = page.locator("#apiResultCard");
  const overview = page.getByLabel("Обзор рассчитанных точек");
  await expect(overview.locator("summary")).toHaveText("Рассчитанные точки (1 из 3)");
  await overview.locator("summary").click();
  await overview.getByRole("button", { name: /Точка · 54.50200, 20.30200/ }).click();
  await expect(card.locator("h3")).toHaveText("Точка · 54.50200, 20.30200");
  await expect(card.locator("h3")).toBeFocused();
  await expect(overview).toContainText("Индекс города: Нет данных");
  await expect(card).toContainText("0,5 из 100");
  const route = card.locator(".api-journey");
  await expect(route).toHaveCount(1);
  await expect(route.locator("summary")).toHaveText("Путь · Данные доступны · геометрия источника · частичные данные");
  await route.locator("summary").click();
  await expect(route).toContainText("Синтетический аэропорт · 10 мин");
  await expect(route).toContainText("Пеший участок · Пешком · 10 мин");
  await expect(card).toContainText("Время маршрута округлено для отображения; точность расчёта индекса сохранена.");
  await expect(card).toContainText("Координаты линии округлены источником.");
  await expect(card).not.toContainText("MODEL_TIMELINE_MICROSECOND_PRECISION");
  await expect(card).not.toContainText("PINNED_TILE_SHAPE_6_DECIMAL_ENCODING");
  expect(mock.posts).toBe(1);
});

test("API: city inspection clears the completed city's route and requires fresh scope selections", async ({ page }) => {
  // Synthetic three-city inventory; no additional production profile is asserted.
  await page.clock.setFixedTime(new Date("2026-11-10T08:00:00Z"));
  const c = scopedCapabilities([{ ...airportScope(), whole_city: false, area_ids: ["synthetic-k-area"] }]);
  const third = { ...territories[0], territory_id: "synthetic-city-3", name: "Новороссийск" } as Territory;
  c.areas = [{ area_id: "synthetic-k-area", city_id: "city-2", name: "Синтетический район К" },
    { area_id: "synthetic-m-area", city_id: "city-1", name: "Синтетический район М" }];
  c.snapshots![0].territory_ids.push(third.territory_id);
  c.snapshots!.push({ ...c.snapshots![0], snapshot_id: "synthetic-other-version" });
  const mock = await mockApi(page, { capabilities: c, territories: [...territories, third] as Territory[], result: partialWalkGeometryFixture(), emptyProposals: true });
  await page.goto("/?data=api#atlas");
  await choose(page, "Город", "Калининград");
  await airportParameters(page, "Синтетический район К");
  await expect(page.getByRole("combobox", { name: "Район", exact: true })).toContainText("Синтетический район К");
  const date = await page.locator("#apiDate").textContent();
  await page.getByRole("button", { name: "Рассчитать", exact: true }).first().click();
  await expect(page.locator(".status")).toContainText("Результат получен");
  await expect(page.locator("#apiResultCard .api-journey")).toHaveCount(1);
  await choose(page, "Город", "Москва");
  await expect(page.locator("#apiResultCard")).toHaveCount(0);
  await expect(page.locator(".api-journey")).toHaveCount(0);
  await expect(page.locator(".status")).not.toContainText("Калининград");
  await expect(page.getByRole("group", { name: "Передвижение", exact: true }).locator('[aria-pressed="true"]')).toHaveCount(0);
  await expect(page.getByRole("combobox", { name: "Набор данных", exact: true })).toHaveCount(0);
  await expect(page.getByRole("combobox", { name: "Район", exact: true })).toContainText("Весь город");
  await expect(page.locator("#apiDate")).toHaveText(date || "");
  await expect(page.getByRole("slider", { name: "Время отправления, шаг 15 минут" })).toHaveValue("34");
  await expect(page.locator(".tz:visible")).toHaveText("Europe/Moscow");
  await page.getByRole("button", { name: "Рассчитать", exact: true }).first().click();
  await expect(page.locator(".statecard")).toContainText("Для выбранного города нет зарегистрированного расчёта.");
  await expect(page.getByRole("button", { name: "Использовать доступные параметры", exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Рассчитать", exact: true }).first()).toBeEnabled();
  await choose(page, "Город", "Новороссийск");
  await page.getByRole("button", { name: "Рассчитать", exact: true }).first().click();
  await expect(page.locator(".statecard")).toContainText("нет зарегистрированного расчёта");
  await expect(page.getByRole("button", { name: "Рассчитать", exact: true }).first()).toBeEnabled();
  await choose(page, "Город", "Калининград");
  await expect(page.locator("#apiResultCard")).toHaveCount(0);
  await expect(page.getByRole("group", { name: "Передвижение", exact: true }).locator('[aria-pressed="true"]')).toHaveCount(0);
  await expect(page.getByRole("combobox", { name: "Набор данных", exact: true })).toHaveCount(0);
  await expect(page.getByRole("combobox", { name: "Район", exact: true })).toContainText("Весь город");
  await expect(page.locator(".tz:visible")).toHaveText("Europe/Kaliningrad");
  expect(mock.posts).toBe(1);
});

test("API: changing city during a job discards its local context without borrowing another city's preset", async ({ page }) => {
  await page.clock.setFixedTime(new Date("2026-11-10T08:00:00Z"));
  const c = scopedCapabilities([airportScope(), { ...airportScope(), scope_id: "synthetic-m-walk", city_id: "city-1" }]);
  const mock = await mockApi(page, { capabilities: c, running: true, emptyProposals: true });
  await page.goto("/?data=api#atlas");
  await choose(page, "Город", "Калининград");
  await airportParameters(page);
  await page.getByRole("button", { name: "Рассчитать", exact: true }).first().click();
  await expect(page.getByRole("button", { name: "Отменить", exact: true })).toBeVisible();
  await choose(page, "Город", "Москва");
  await expect(page.getByRole("button", { name: "Отменить", exact: true })).toHaveCount(0);
  await expect(page.locator("#apiResultCard")).toHaveCount(0);
  await expect(page.getByRole("group", { name: "Передвижение", exact: true }).locator('[aria-pressed="true"]')).toHaveCount(0);
  await expect(page.getByRole("combobox", { name: "Доступный набор параметров", exact: true })).toHaveCount(0);
  await choose(page, "Город", "Калининград");
  await expect(page.getByRole("group", { name: "Передвижение", exact: true }).locator('[aria-pressed="true"]')).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Рассчитать", exact: true }).first()).toBeEnabled();
  expect(mock.posts).toBe(1);
  expect(mock.requests.some((request) => request.url.endsWith("/cancel"))).toBe(false);
});

test("API: disjoint movement segments retain partial-route labels and clear on city change", async ({ page }) => {
  // Synthetic missing approaches and gap: only two supplied network segments exist.
  await page.clock.setFixedTime(new Date("2026-11-10T08:00:00Z"));
  const mock = await mockApi(page, { capabilities: scopedCapabilities(), result: legGeometryFixture(), emptyProposals: true });
  await page.goto("/?data=api#atlas");
  await choose(page, "Город", "Калининград");
  await airportParameters(page);
  await page.getByRole("button", { name: "Рассчитать", exact: true }).first().click();
  await expect(page.locator(".status")).toContainText("Результат получен");
  const note = page.locator(".api-map-note");
  await expect(note).toContainText("Показан участок маршрута; геометрия подходов не подтверждена");
  const overview = page.getByLabel("Обзор рассчитанных точек");
  await expect(overview).toContainText("Индекс города: Нет данных");
  await overview.locator("summary").click();
  await overview.getByRole("button", { name: /Точка · 54.50200, 20.30200/ }).click();
  const card = page.locator("#apiResultCard"), route = card.locator(".api-journey");
  await expect(card).toContainText("0,5 из 100");
  await expect(route.locator("summary")).toHaveText("Путь · Данные доступны · геометрия участков · частичные данные");
  await route.locator("summary").click();
  await expect(route).toContainText("Показан участок маршрута; геометрия подходов не подтверждена");
  await expect(route.locator("li")).toHaveCount(5);
  await expect(route).toContainText("Синтетический аэропорт · 10 мин");
  await expect(route).not.toContainText("геометрия источника");
  await choose(page, "Город", "Москва");
  await expect(card).toHaveCount(0);
  await expect(note).toHaveCount(0);
  await expect(page.locator(".status")).not.toContainText("Калининград");
  expect(mock.posts).toBe(1);
});

test("API: readiness separates registered inputs from matching point zero and unknown city completeness", async ({ page }) => {
  await page.clock.setFixedTime(new Date("2026-11-10T08:00:00Z"));
  const mock = await mockApi(page, { capabilities: scopedCapabilities(), result: legGeometryFixture(0), emptyProposals: true });
  await page.goto("/?data=api#atlas");
  await choose(page, "Город", "Калининград");
  await airportParameters(page);
  await openSources(page);
  const readiness = page.getByLabel("Готовность выбранных условий");
  await expect(readiness).toContainText("Подходящий набор опубликован");
  await expect(readiness).toContainText("Параметры запуска зарегистрированы");
  await expect(readiness).toContainText("Полнота данных о населении не подтверждена");
  await expect(readiness).toContainText("Полнота каталога не подтверждена");
  await expect(readiness).toContainText("Для этих условий результат ещё не получен");
  await page.getByRole("button", { name: "Рассчитать", exact: true }).first().click();
  await expect(page.locator(".status")).toContainText("Результат получен");
  await expect(readiness).toContainText("Рассчитано точек: 1 из 3");
  await expect(readiness.locator('b[data-score-band="low"]')).toHaveText("0 из 100");
  await expect(readiness.locator('b[data-score-band="unknown"]')).toHaveText("Нет данных");
  await page.getByRole("slider", { name: "Время отправления, шаг 15 минут" }).fill("35");
  await expect(page.locator(".status")).toContainText("прежний результат");
  await expect(readiness).toContainText("Для этих условий результат ещё не получен");
  await expect(readiness).not.toContainText("Рассчитано точек");
  await expect(readiness.locator("b[data-score-band]")).toHaveCount(0);
  await choose(page, "Город", "Москва");
  await expect(readiness).toContainText("Готовность · Москва");
  await expect(readiness).toContainText("Для выбранных условий запуск не зарегистрирован");
  await expect(readiness).not.toContainText("Рассчитано точек");
  await expect(readiness.locator("b[data-score-band]")).toHaveCount(0);
  expect(mock.posts).toBe(1);
});

test("API: selected area never inherits the city index or its population completeness", async ({ page }) => {
  await page.clock.setFixedTime(new Date("2026-11-10T08:00:00Z"));
  const areaId = "synthetic-area";
  const c = scopedCapabilities([{ ...airportScope(), whole_city: false, area_ids: [areaId] }]);
  c.areas = [{ area_id: areaId, city_id: "city-2", name: "Синтетический район" }];
  const data = legGeometryFixture(0);
  const city = data.scores.find((score) => score.name === "territory_index")!;
  city.value = 85; city.status = "available";
  city.aggregation_basis = { unit: "persons", population: 1000, coverage: 1, year: 2025, population_method: "synthetic", unknown_positive_weight: false };
  const mock = await mockApi(page, { capabilities: c, result: data, emptyProposals: true });
  await page.goto("/?data=api#atlas");
  await choose(page, "Город", "Калининград");
  await airportParameters(page, "Синтетический район");
  await page.getByRole("button", { name: "Рассчитать", exact: true }).first().click();
  await expect(page.locator(".status")).toContainText("Результат получен");
  await openSources(page);
  const readiness = page.getByLabel("Готовность выбранных условий");
  await expect(readiness).toContainText("Индекс выбранной территории");
  await expect(readiness).toContainText("Не предоставлен.");
  await expect(readiness).not.toContainText("85 из 100");
  await expect(readiness).toContainText("Полнота данных о населении не подтверждена");
  await expect(page.getByLabel("Обзор рассчитанных точек")).toContainText("Индекс выбранной территории: не предоставлен");
  expect(mock.request).toMatchObject({ city_id: "city-2", area_id: areaId });
});

test("API: a saved route remains easy to select when every point index is unknown", async ({ page }) => {
  await page.clock.setFixedTime(new Date("2026-11-10T08:00:00Z"));
  await mockApi(page, { capabilities: scopedCapabilities(), result: legGeometryFixture(null), emptyProposals: true });
  await page.goto("/?data=api#atlas");
  await choose(page, "Город", "Калининград");
  await airportParameters(page);
  await page.getByRole("button", { name: "Рассчитать", exact: true }).first().click();
  await expect(page.locator(".status")).toContainText("Результат получен");
  await expect(page.getByLabel("Обзор рассчитанных точек")).toContainText("Рассчитанные точки (0 из 3)");
  const routes = page.getByLabel("Обзор рассчитанных маршрутов");
  await routes.locator("summary").click();
  const choice = routes.getByRole("button", { name: /Синтетический аэропорт/ });
  await expect(choice).toContainText("10 мин · полное время");
  await choice.focus();
  await choice.press("Enter");
  const card = page.locator("#apiResultCard");
  await expect(card.locator("h3")).toHaveText("Точка · 54.50200, 20.30200");
  await expect(card.locator("h3")).toBeFocused();
  await expect(routes.locator("details")).toHaveJSProperty("open", false);
  await expect(card.locator(".api-journey summary")).toContainText("геометрия участков");
  await expect(card.locator('.api-measure b[data-score-band="unknown"]')).toHaveCount(2);
  await expect(card.locator('.api-measure b[data-score-band="low"]')).toHaveCount(0);
  await page.getByRole("button", { name: "Все показатели", exact: true }).click();
  await expect(card.locator("h3")).toHaveText("Калининград");
  await expect(card.locator(".api-measure")).toHaveCount(12);
});

test("API: generated transport snapshot remains explicitly demo through canonical scenario before and after", async ({ page }) => {
  const snapshot = "demo-moscow-synthetic-ui-only";
  const c = structuredClone(capabilities) as Schemas["Capabilities"];
  c.snapshots![0] = { ...c.snapshots![0], snapshot_id: snapshot, territory_ids: ["city-1"] };
  const data = structuredClone(urban) as AnalysisResult;
  data.snapshot_id = snapshot; data.versions.snapshot_id = snapshot;
  const after = structuredClone(scenario) as Schemas["ScenarioResult"];
  after.versions.snapshot_id = snapshot;
  await mockApi(page, { capabilities: c, result: data, scenarioResult: after });
  await page.goto("/?data=api#atlas");
  await choose(page, "Город", "Москва");
  const notice = page.getByRole("note", { name: "Демонстрационная модель" });
  await expect(notice).toContainText("Демонстрационная транспортная модель — не оценка фактической доступности Москвы");
  await page.getByRole("button", { name: "Рассчитать", exact: true }).first().click();
  await expect(page.locator(".status")).toContainText("Результат получен");
  await expect(page.locator("#apiResultCard .k")).toHaveText("Демонстрационный анализ");
  await page.getByRole("button", { name: "Свернуть фильтры", exact: true }).click();
  await expect(page.getByRole("button", { name: "Развернуть фильтры", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Развернуть фильтры", exact: true }).click();
  await expect(page.getByRole("button", { name: "Центр Москвы", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Все точки", exact: true }).click();
  await page.getByRole("button", { name: "Центр Москвы", exact: true }).click();
  await page.getByRole("button", { name: "Агент", exact: true }).first().click();
  await page.getByRole("button", { name: "Проверить сценарий", exact: true }).click();
  await expect(page.getByRole("button", { name: "Сценарий", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Сценарий", exact: true }).click();
  await expect(page.locator("#apiResultCard .k")).toHaveText("Демонстрационный сценарий");
  await expect(notice).toBeVisible();
  await page.getByRole("button", { name: "База", exact: true }).click();
  await expect(page.locator("#apiResultCard .k")).toHaveText("Демонстрационный анализ");
  await expect(notice).toBeVisible();
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole("button", { name: "Фильтры", exact: true }).first().click();
  await expect(notice).toBeVisible();
  await expect(page.locator("#apiFilters [role=note]")).toContainText("Демонстрационная транспортная модель");
  await page.locator("#apiFilters").getByRole("button", { name: "Закрыть фильтры", exact: true }).click();
  await page.getByRole("button", { name: "Агент", exact: true }).first().click();
  await expect(page.locator("#chat [role=note]")).toContainText("Демонстрационная транспортная модель");
  await page.getByRole("button", { name: "Свернуть чат", exact: true }).click();
  await page.getByRole("button", { name: "Фильтры", exact: true }).first().click();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await choose(page, "Город", "Калининград");
  await expect(notice).toHaveCount(0);
});

test("API: synthetic saved proposal shows its exact two-minute shift before the run button is pressed", async ({ page }) => {
  // Synthetic UI protocol fixture; no production scenario or numerical proof.
  const mock = await mockApi(page, { scenarioProposal: { fixed_parameters: { ...proposal.fixed_parameters, shift_seconds: 120 } } });
  await page.goto("/?data=api#atlas");
  await cityRun(page);
  await page.getByRole("button", { name: "Агент", exact: true }).first().click();
  const card = page.locator(".at-chat .proposal");
  await expect(card).toContainText("Согласовать пересадку");
  await expect(card).toContainText("Сдвиг отправления: +2 мин");
  await expect(card.getByRole("button", { name: "Проверить сценарий", exact: true })).toBeEnabled();
  expect(mock.requests.some((request) => request.url.endsWith("/runs"))).toBe(false);
  await card.getByRole("button", { name: "Проверить сценарий", exact: true }).click();
  await expect(page.getByRole("button", { name: "Сценарий", exact: true })).toBeVisible();
  expect(mock.requests.filter((request) => request.url.endsWith("/runs"))).toHaveLength(1);
});


test("API: shared card and map distinguish an observed zero estimate from unknown canonical totals", async ({ page }) => {
  await page.clock.setFixedTime(new Date("2026-11-10T08:00:00Z"));
  const data = observedRouteFixture();
  const mock = await mockApi(page, { capabilities: scopedCapabilities(), result: data, emptyProposals: true });
  await page.goto("/?data=api#atlas");
  await airportParameters(page);
  await page.getByRole("button", { name: "Рассчитать", exact: true }).first().click();
  await expect(page.locator(".status")).toContainText("Результат получен");
  await expect(page.locator(".legend h3")).toContainText("Предварительная оценка по найденному маршруту");
  const overview = page.getByLabel("Обзор рассчитанных точек");
  await expect(overview.first()).toContainText("Рассчитанные точки (0 из 3)");
  const estimates = overview.filter({ hasText: "Точки с предварительной оценкой" });
  await estimates.locator("summary").click();
  await estimates.getByRole("button", { name: /Точка · 54.50200, 20.30200/ }).click();
  const card = page.locator("#apiResultCard");
  await expect(card.locator('.idxpill[data-score-band="low"]')).toHaveText("0 из 100");
  await expect(card).toContainText("Предварительная оценка по найденному маршруту");
  await expect(card).toContainText("минимум не подтверждён");
  await expect(card.getByText("Общий индекс", { exact: true }).locator("..").locator("..")).toContainText("Нет данных");
  await expect(card.locator('.api-measure b[data-score-band="unknown"]')).toHaveCount(2);
  await page.screenshot({ path: "test-results/api-shared-observed-desktop.png" });
  expect(mock.posts).toBe(1);
});


test("API: a unique real-source Moscow default uses the original controls without automatic submission", async ({ page }) => {
  await page.clock.setFixedTime(new Date("2026-11-10T08:00:00Z"));
  const c = structuredClone(capabilities) as Schemas["Capabilities"];
  c.snapshots![0].territory_ids.push("moscow");
  c.urban_passenger_scopes = [{ ...airportScope(), city_id: "moscow", categories: ["adult_polyclinic"] }];
  const cities = (territories as Territory[]).map((city) => city.territory_id === "city-1" ? { ...city, territory_id: "moscow" } : city);
  const mock = await mockApi(page, { capabilities: c, territories: cities });
  await page.goto("/?data=api#atlas");
  await expect(page.getByRole("group", { name: "Город", exact: true }).getByRole("button", { name: "Москва", exact: true })).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByRole("group", { name: "Передвижение", exact: true }).getByRole("button", { name: "Пешком", exact: true })).toHaveAttribute("aria-pressed", "true");
  await expect(page.locator(".cat-tog[aria-pressed=true]")).toHaveCount(1);
  await expect(page.getByRole("button", { name: "Взрослые поликлиники", exact: true })).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByRole("tab")).toHaveCount(3);
  await expect(page.getByRole("combobox", { name: "Набор данных", exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Использовать доступные параметры", exact: true })).toHaveCount(0);
  expect(mock.posts).toBe(0);
  await choose(page, "Передвижение", "Общ. транспорт");
  await expect(page.getByRole("group", { name: "Передвижение", exact: true }).getByRole("button", { name: "Общ. транспорт", exact: true })).toHaveAttribute("aria-pressed", "true");
  expect(mock.posts).toBe(0);
  await page.screenshot({ path: "test-results/api-shared-initial-source-desktop.png" });
});

test("API: preparation errors are human and manual retry retains the pending submission identity", async ({ page }) => {
  const mock = await mockApi(page, { errorStatus: 503, errorCode: "RUNTIME_INPUTS_BUSY", emptyProposals: true });
  await page.goto("/?data=api#atlas");
  await cityRun(page);
  await expect(page.getByRole("alert")).toContainText("Сервер подготавливает данные. Повторите расчёт чуть позже.");
  await expect(page.locator("body")).not.toContainText("RUNTIME_INPUTS_BUSY");
  await page.getByRole("button", { name: "Повторить", exact: true }).click();
  await expect.poll(() => mock.posts).toBe(2);
  const posts = mock.requests.filter((request) => request.method === "POST" && request.url.endsWith("/analyses"));
  expect(posts[0].key).toBeTruthy();
  expect(posts[0].key).toBe(posts[1].key);
});
