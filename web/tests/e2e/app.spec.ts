import { expect, test, type Page } from "@playwright/test";

async function mockApi(page: Page) {
  await page.route("http://localhost:8003/auth/login", async (route) => {
    await route.fulfill({
      json: {
        access_token: "test-token",
        token_type: "bearer",
        user: { username: "reviewer", roles: ["reviewer", "uploader"] },
      },
    });
  });
  await page.route("http://localhost:8003/auth/me", async (route) => {
    await route.fulfill({ json: { username: "reviewer", roles: ["reviewer", "uploader"] } });
  });
  await page.route("http://localhost:8003/documents", async (route) => {
    if (route.request().method() === "GET") {
      await route.fulfill({
        json: [
          {
            id: 9,
            filename: "statement_noisy.pdf",
            status: "needs_review",
            sha256: "abc",
            confidence_score: 0.42,
            created_at: "2026-01-01T00:00:00Z",
          },
        ],
      });
      return;
    }
    await route.continue();
  });
  await page.route("http://localhost:8003/review-queue", async (route) => {
    await route.fulfill({
      json: [
        {
          id: 9,
          filename: "statement_noisy.pdf",
          status: "needs_review",
          sha256: "abc",
          confidence_score: 0.42,
          created_at: "2026-01-01T00:00:00Z",
        },
      ],
    });
  });
}

test("login shows dashboard documents", async ({ page }) => {
  await mockApi(page);
  await page.goto("/");
  await page.getByRole("button", { name: "Sign in" }).click();

  await expect(page.getByRole("heading", { name: "Documents" })).toBeVisible();
  await expect(page.getByText("statement_noisy.pdf")).toBeVisible();
  await expect(page.getByText("42%")).toBeVisible();
});

test("review queue routes to reviewer worklist", async ({ page }) => {
  await mockApi(page);
  await page.goto("/");
  await page.getByRole("button", { name: "Sign in" }).click();
  await page.getByRole("link", { name: "Review" }).click();

  await expect(page.getByRole("heading", { name: "Review queue" })).toBeVisible();
  await expect(page.getByText("statement_noisy.pdf")).toBeVisible();
});

test("upload page accepts multiple pdf selection", async ({ page }) => {
  await mockApi(page);
  await page.goto("/");
  await page.getByRole("button", { name: "Sign in" }).click();
  await page.getByRole("link", { name: "Upload" }).click();

  const input = page.locator('input[type="file"]');
  await input.setInputFiles([
    {
      name: "a.pdf",
      mimeType: "application/pdf",
      buffer: Buffer.from("%PDF-a"),
    },
    {
      name: "b.pdf",
      mimeType: "application/pdf",
      buffer: Buffer.from("%PDF-b"),
    },
  ]);

  await expect(page.getByText("2 PDFs selected")).toBeVisible();
  await expect(page.getByRole("button", { name: "Upload all" })).toBeVisible();
});
