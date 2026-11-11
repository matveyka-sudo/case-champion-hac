import { defineConfig } from "@playwright/test";

if (process.env.WAYDEX_RUN_LIVE_E2E !== "1") {
  throw new Error("Set WAYDEX_RUN_LIVE_E2E=1 to run against the real local API and worker.");
}

export default defineConfig({
  testDir: "./live-e2e",
  outputDir: "./live-test-results",
  workers: 1,
  retries: 0,
  timeout: 60_000,
  use: {
    baseURL: process.env.WAYDEX_LIVE_BASE_URL || "http://127.0.0.1:5178",
    viewport: { width: 1440, height: 1000 },
    reducedMotion: "reduce",
    trace: "retain-on-failure",
    launchOptions: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH
      ? { executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH }
      : {},
  },
  reporter: "list",
});
