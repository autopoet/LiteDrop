import { readFile } from "node:fs/promises";
import { expect, test } from "@playwright/test";

test("upload, pick up and download the same file", async ({ page }) => {
  const content = Buffer.alloc(6 * 1024 * 1024 + 17, "CodeDrop V2");
  const fileName = "codedrop-e2e.bin";

  await page.goto("/");
  await page.locator('input[type="file"]').setInputFiles({
    name: fileName,
    mimeType: "application/octet-stream",
    buffer: content,
  });
  await page.getByLabel("上传口令").fill("e2e-code");
  await page.getByRole("button", { name: "开始上传" }).click();

  const codeButton = page.locator(".pickup-code");
  await expect(codeButton).toBeVisible({ timeout: 60_000 });
  const code = (await codeButton.textContent())?.match(/\d{6}/)?.[0];
  expect(code).toMatch(/^\d{6}$/);

  await page.locator(".main-nav button").nth(1).click();
  await page.getByLabel("六位取件码").fill(code!);
  await page.getByRole("button", { name: "查询文件" }).click();
  await expect(page.getByRole("heading", { name: fileName })).toBeVisible();

  const downloadPromise = page.waitForEvent("download");
  await page.getByRole("button", { name: "下载文件" }).click();
  const download = await downloadPromise;
  expect(download.suggestedFilename()).toBe(fileName);
  const downloadedPath = await download.path();
  expect(downloadedPath).not.toBeNull();
  expect(await readFile(downloadedPath!)).toEqual(content);
});
