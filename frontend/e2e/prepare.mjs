import { rmSync } from "node:fs";
import { fileURLToPath } from "node:url";

const runId = process.env.E2E_RUN_ID ?? "current";
if (!/^[A-Za-z0-9_-]+$/.test(runId)) {
  throw new Error("E2E_RUN_ID may only contain letters, numbers, _ and -");
}
const runtimeRoot = fileURLToPath(
  new URL(`../../.e2e-data/${runId}`, import.meta.url),
);
rmSync(runtimeRoot, { recursive: true, force: true });
