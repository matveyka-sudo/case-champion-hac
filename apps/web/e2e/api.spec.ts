import { test, expect, type Page } from "@playwright/test";
import type { AnalysisResult } from "../src/api/types";
import capabilities from "../tests/fixtures/api/capabilities.json" with { type: "json" };
import territories from "../tests/fixtures/api/territories.json" with { type: "json" };
import urban from "../tests/fixtures/api/urban_passenger.json" with { type: "json" };
import proposal from "../tests/fixtures/api/proposal.json" with { type: "json" };
import scenario from "../tests/fixtures/api/scenario.json" with { type: "json" };
import pax from "../tests/fixtures/api/intercity_passenger.json" with { type: "json" };
import cargo from "../tests/fixtures/api/urban_cargo.json" with { type: "json" };
import interCargo from "../tests/fixtures/api/intercity_cargo.json" with { type: "json" };
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
    if (path.endsWith("/capabilities")) body = capabilities;
    else if (path.endsWith("/territories"))
      body = territories.filter(
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
          request: sent,
          ...(kind === "urban_passenger" ? { map_data: base.map_data } : {}),
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
