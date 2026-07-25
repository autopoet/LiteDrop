import { existsSync } from "node:fs";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { defineConfig } from "@playwright/test";

const frontendRoot = fileURLToPath(new URL(".", import.meta.url));
const projectRoot = resolve(frontendRoot, "..");
const runId = process.env.E2E_RUN_ID ?? "current";
if (!/^[A-Za-z0-9_-]+$/.test(runId)) {
  throw new Error("E2E_RUN_ID may only contain letters, numbers, _ and -");
}
const runtimeRoot = resolve(projectRoot, ".e2e-data", runId);
const windowsPython = resolve(projectRoot, ".venv", "Scripts", "python.exe");
const unixPython = resolve(projectRoot, ".venv", "bin", "python");
const python = process.env.E2E_PYTHON
  ?? (process.platform === "win32" ? windowsPython : unixPython);

const windowsChrome =
  "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe";
const browserPath = process.env.E2E_BROWSER_PATH
  ?? (process.platform === "win32" && existsSync(windowsChrome)
    ? windowsChrome
    : undefined);

export default defineConfig({
  testDir: "./e2e",
  timeout: 90_000,
  fullyParallel: false,
  reporter: "line",
  use: {
    baseURL: "http://127.0.0.1:14173",
    trace: "retain-on-failure",
    launchOptions: browserPath ? { executablePath: browserPath } : undefined,
  },
  webServer: [
    {
      command: `"${python}" -m uvicorn app.main:app --host 127.0.0.1 --port 18080`,
      cwd: resolve(projectRoot, "backend"),
      url: "http://127.0.0.1:18080/health",
      reuseExistingServer: false,
      timeout: 30_000,
      env: {
        ...process.env,
        APP_ENV: "test",
        APP_SECRET: "e2e-app-secret-with-enough-length",
        DOWNLOAD_SECRET: "e2e-download-secret-with-enough-length",
        DATABASE_PATH: resolve(runtimeRoot, "litedrop.db"),
        STORAGE_ROOT: resolve(runtimeRoot, "storage"),
        DISK_RESERVE_BYTES: "0",
        PUBLIC_UPLOAD_ENABLED: "true",
        UPLOAD_ACCESS_CODE: "e2e-code",
        ALLOWED_ORIGINS: "http://127.0.0.1:14173",
      },
    },
    {
      command: "npm run dev -- --host 127.0.0.1 --port 14173 --strictPort",
      cwd: frontendRoot,
      url: "http://127.0.0.1:14173",
      reuseExistingServer: false,
      timeout: 30_000,
      env: {
        ...process.env,
        VITE_API_PROXY_TARGET: "http://127.0.0.1:18080",
      },
    },
  ],
});
