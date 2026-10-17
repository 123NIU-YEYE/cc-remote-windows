import { defineConfig } from "../web/node_modules/@playwright/test/index.mjs";
import { fileURLToPath } from "node:url";

export default defineConfig({
  testDir: "./tests",
  testMatch: "shell.spec.mjs",
  outputDir: "./test-results",
  workers: 1,
  reporter: "line",
  use: {
    baseURL: "http://127.0.0.1:4176",
    viewport: { width: 390, height: 844 },
    locale: "zh-CN",
    trace: "retain-on-failure",
  },
  webServer: {
    command: "node node_modules/vite/bin/vite.js --host 127.0.0.1 --port 4176",
    cwd: fileURLToPath(new URL("../web", import.meta.url)),
    url: "http://127.0.0.1:4176/tests/history-browser.html",
    reuseExistingServer: false,
  },
});
