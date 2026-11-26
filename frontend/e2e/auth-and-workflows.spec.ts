import { createHmac } from "node:crypto";
import { expect, request as playwrightRequest, test, type APIRequestContext, type Page } from "@playwright/test";

test.describe.configure({ mode: "serial" });

const baseURL = process.env.ECC_E2E_BASE_URL || "http://127.0.0.1:5173";
const adminEmail = process.env.ECC_ADMIN_EMAIL || "e2e-admin@example.com";
const adminPassword = process.env.ECC_ADMIN_PASSWORD || "e2e-admin-password-123456";
const viewerPassword = "viewer-e2e-password-123";
let adminTotpSecret = "";
let viewerEmail = "";
let assetName = "";
let requestTitle = "";
let lastUsedTotpCounter = -1;
let setupRequest: APIRequestContext | undefined;

function currentTotpCounter(): number {
  return Math.floor(Date.now() / 1000 / 30);
}

function decodeBase32(value: string): Buffer {
  const alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";
  let bits = "";
  for (const char of value.replace(/=+$/g, "").toUpperCase()) {
    const index = alphabet.indexOf(char);
    if (index < 0) throw new Error("Invalid Base32 TOTP secret");
    bits += index.toString(2).padStart(5, "0");
  }
  const bytes: number[] = [];
  for (let offset = 0; offset + 8 <= bits.length; offset += 8) {
    bytes.push(Number.parseInt(bits.slice(offset, offset + 8), 2));
  }
  return Buffer.from(bytes);
}

function totp(secret: string, instant = Date.now()): string {
  const counter = Math.floor(instant / 1000 / 30);
  const message = Buffer.alloc(8);
  message.writeBigUInt64BE(BigInt(counter));
  const digest = createHmac("sha1", decodeBase32(secret)).update(message).digest();
  const offset = digest[digest.length - 1] & 0x0f;
  const binary = digest.readUInt32BE(offset) & 0x7fffffff;
  return String(binary % 1_000_000).padStart(6, "0");
}

async function waitForFreshTotp(): Promise<string> {
  while (currentTotpCounter() <= lastUsedTotpCounter) {
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  const counter = currentTotpCounter();
  lastUsedTotpCounter = counter;
  return totp(adminTotpSecret, (counter * 30 + 1) * 1000);
}

async function rawPasswordLogin(request: APIRequestContext, email: string, password: string) {
  const response = await request.post(baseURL + "/api/v1/auth/token", {
    form: { username: email, password },
  });
  expect(response.ok(), "password login failed: " + response.status()).toBeTruthy();
  return response.json() as Promise<{ access_token: string | null }>;
}

test.beforeAll(async () => {
  test.setTimeout(120_000);
  setupRequest = await playwrightRequest.newContext({ baseURL });
  const initial = await rawPasswordLogin(setupRequest, adminEmail, adminPassword);
  expect(initial.access_token).toBeTruthy();
  const headers = { Authorization: "Bearer " + initial.access_token };

  // Seed business records and the viewer before MFA activation revokes the bootstrap session.
  const suffix = Date.now();
  assetName = "E2E workstation " + suffix;
  const assetResponse = await setupRequest.post(baseURL + "/api/v1/assets", {
    headers,
    data: {
      name: assetName,
      asset_tag: "E2E-" + suffix,
      category: "Компьютер",
      manufacturer: "ECC E2E",
      model: "Virtual",
      serial_number: "E2E-SN-" + suffix,
      assigned_to: "E2E Test",
      status: "active",
    },
  });
  expect(assetResponse.ok()).toBeTruthy();
  const asset = await assetResponse.json() as { id: number };

  requestTitle = "E2E maintenance " + suffix;
  const maintenanceResponse = await setupRequest.post(baseURL + "/api/v1/maintenance-requests", {
    headers,
    data: {
      asset_id: asset.id,
      title: requestTitle,
      description: "Created by browser end-to-end test",
      priority: "normal",
    },
  });
  expect(maintenanceResponse.ok()).toBeTruthy();

  viewerEmail = "viewer-" + suffix + "@example.com";
  const viewerResponse = await setupRequest.post(baseURL + "/api/v1/users", {
    headers,
    data: { email: viewerEmail, full_name: "E2E Viewer", password: viewerPassword, role: "viewer" },
  });
  expect(viewerResponse.ok()).toBeTruthy();

  const statusResponse = await setupRequest.get(baseURL + "/api/v1/auth/mfa/status", { headers });
  expect(statusResponse.ok()).toBeTruthy();
  const status = await statusResponse.json() as { enabled: boolean };
  expect(status.enabled).toBeFalsy();

  const enrollmentResponse = await setupRequest.post(baseURL + "/api/v1/auth/mfa/enrollment", { headers });
  expect(enrollmentResponse.ok()).toBeTruthy();
  const enrollment = await enrollmentResponse.json() as { secret: string };
  adminTotpSecret = enrollment.secret;
  const confirmResponse = await setupRequest.post(baseURL + "/api/v1/auth/mfa/enrollment/confirm", {
    headers,
    data: { code: totp(adminTotpSecret) },
  });
  expect(confirmResponse.ok()).toBeTruthy();
  lastUsedTotpCounter = currentTotpCounter();
});

test.afterAll(async () => {
  await setupRequest?.dispose();
});

async function loginThroughUi(page: Page, email: string, password: string, secret?: string) {
  await page.goto("/");
  await page.getByLabel("Электронная почта").fill(email);
  await page.getByLabel("Пароль").fill(password);
  await page.getByRole("button", { name: "Войти" }).click();

  if (secret) {
    await expect(page.getByRole("heading", { name: "Подтверждение входа" })).toBeVisible();
    await page.getByLabel("Код подтверждения").fill(await waitForFreshTotp());
    await page.getByRole("button", { name: "Подтвердить и войти" }).click();
  }
  await expect(page.getByRole("heading", { name: "Оборудование" })).toBeVisible();
}

test("MFA login, assets, maintenance requests, sessions and admin navigation", async ({ page }) => {
  await loginThroughUi(page, adminEmail, adminPassword, adminTotpSecret);
  await expect(page.getByText(assetName)).toBeVisible();
  await expect(page.getByRole("button", { name: "Пользователи" })).toBeVisible();
  await page.getByRole("button", { name: "Обслуживание" }).click();
  await expect(page.getByRole("heading", { name: "Заявки" })).toBeVisible();
  await expect(page.getByText(requestTitle)).toBeVisible();

  await page.getByRole("button", { name: "Безопасность и сеансы" }).click();
  await expect(page.getByRole("heading", { name: "Безопасность и сеансы" })).toBeVisible();
  await expect(page.getByText("Многофакторная аутентификация")).toBeVisible();
  await expect(page.getByText("Включена")).toBeVisible();
});

test("viewer role hides administration and is denied user-management API", async ({ page, request }) => {
  await loginThroughUi(page, viewerEmail, viewerPassword);
  await expect(page.getByRole("button", { name: "Обслуживание" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Пользователи" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Сотрудники" })).toHaveCount(0);

  const viewerLogin = await rawPasswordLogin(request, viewerEmail, viewerPassword);
  expect(viewerLogin.access_token).toBeTruthy();
  const forbidden = await request.get(baseURL + "/api/v1/users", {
    headers: { Authorization: "Bearer " + viewerLogin.access_token },
  });
  expect(forbidden.status()).toBe(403);
});
