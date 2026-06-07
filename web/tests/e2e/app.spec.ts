import { expect, test, type Page } from "@playwright/test";

const documents = [
  {
    id: 10,
    filename: "statement_failed.pdf",
    status: "failed",
    sha256: "def",
    confidence_score: 0.2,
    priority: "P1",
    account_holder: "Failed Holder",
    account_number: "****0001",
    created_at: "2026-01-02T00:00:00Z",
  },
  {
    id: 9,
    filename: "statement_noisy.pdf",
    status: "needs_review",
    sha256: "abc",
    confidence_score: 0.42,
    priority: "P1",
    account_holder: "Noisy Holder",
    account_number: "****0002",
    created_at: "2026-01-01T00:00:00Z",
  },
  {
    id: 8,
    filename: "statement_medium.pdf",
    status: "needs_review",
    sha256: "ghi",
    confidence_score: 0.72,
    priority: "P2",
    account_holder: "Medium Holder",
    account_number: "****0003",
    created_at: "2026-01-01T00:00:00Z",
  },
  {
    id: 7,
    filename: "statement_verified.pdf",
    status: "verified",
    sha256: "jkl",
    confidence_score: 0.94,
    priority: "P3",
    account_holder: "Verified Holder",
    account_number: "****0004",
    created_at: "2026-01-01T00:00:00Z",
  },
  {
    id: 6,
    filename: "statement_approved.pdf",
    status: "approved",
    sha256: "mno",
    confidence_score: 0.91,
    priority: "P3",
    account_holder: "Approved Holder",
    account_number: "****0005",
    created_at: "2026-01-01T00:00:00Z",
  },
  {
    id: 5,
    filename: "statement_processing.pdf",
    status: "queued",
    sha256: "pqr",
    confidence_score: null,
    priority: null,
    account_holder: "Processing Holder",
    account_number: "****0006",
    created_at: "2026-01-01T00:00:00Z",
  },
];

function applyListParams(url: URL, source = documents) {
  let rows = [...source];
  const q = url.searchParams.get("q")?.toLowerCase();
  const filter = url.searchParams.get("filter");
  const sort = url.searchParams.get("sort");
  const order = url.searchParams.get("order") || "asc";
  const page = Number(url.searchParams.get("page") || "1");
  const perPage = Number(url.searchParams.get("per_page") || "25");

  if (q) rows = rows.filter((doc) => doc.filename.toLowerCase().includes(q) || doc.account_holder.toLowerCase().includes(q));
  if (filter === "needs_review") rows = rows.filter((doc) => doc.status === "needs_review" || doc.status === "failed");
  if (filter === "processing") rows = rows.filter((doc) => doc.status === "queued" || doc.status === "parsing");
  if (sort) {
    rows.sort((a, b) => {
      const left = a[sort as keyof typeof a] ?? "";
      const right = b[sort as keyof typeof b] ?? "";
      const value = left < right ? -1 : left > right ? 1 : 0;
      return order === "desc" ? -value : value;
    });
  }

  const total = rows.length;
  const start = (page - 1) * perPage;
  return { items: rows.slice(start, start + perPage), total, page, per_page: perPage };
}

async function mockApi(page: Page, roles = ["reviewer", "uploader"]) {
  await page.route("http://localhost:8003/auth/login", async (route) => {
    await route.fulfill({
      json: {
        access_token: "test-token",
        token_type: "bearer",
        user: { username: roles.includes("admin") ? "admin" : "reviewer", roles },
      },
    });
  });
  await page.route("http://localhost:8003/auth/me", async (route) => {
    await route.fulfill({ json: { username: roles.includes("admin") ? "admin" : "reviewer", roles } });
  });
  await page.route("http://localhost:8003/documents**", async (route) => {
    const url = new URL(route.request().url());
    if (url.pathname === "/documents" && route.request().method() === "GET") {
      await route.fulfill({ json: applyListParams(url) });
      return;
    }
    if (url.pathname === "/documents/bulk" && route.request().method() === "POST") {
      const body = route.request().postDataJSON() as { document_ids: number[] };
      await route.fulfill({ json: { succeeded: body.document_ids, failed: [] } });
      return;
    }
    await route.fulfill({
      json: {
        ...documents.find((doc) => url.pathname === `/documents/${doc.id}`),
        reconciliation: { passed: true, deposits_total: 0, withdrawals_total: 0, sign_corrections: 0, checks: [] },
        transactions: [],
        review_items: [],
        audit_log: [],
        versions: [],
      },
    });
  });
  await page.route("http://localhost:8003/review-queue**", async (route) => {
    const url = new URL(route.request().url());
    await route.fulfill({ json: applyListParams(url, documents.filter((doc) => doc.status === "needs_review" || doc.status === "failed")) });
  });
  await page.route("http://localhost:8003/admin/failures?since=30d", async (route) => {
    await route.fulfill({
      json: {
        since: "30d",
        categories: [
          {
            category: "other",
            count: 2,
            trend: [{ week: "2026-W23", count: 2 }],
            samples: [
              {
                document_id: 9,
                filename: "statement_noisy.pdf",
                diffs: [{ path: "transactions[0].description", transaction_index: 0, before: "", after: "Coffee" }],
              },
            ],
          },
        ],
      },
    });
  });
}

test("login shows dashboard documents", async ({ page }) => {
  await mockApi(page);
  await page.goto("/");
  await page.getByRole("button", { name: "Sign in" }).click();

  await expect(page.getByRole("heading", { name: "Documents" })).toBeVisible();
  const row = page.getByRole("row", { name: /statement_noisy\.pdf/ });
  await expect(row).toBeVisible();
  await expect(row.getByText("42%")).toBeVisible();
});

test("dashboard renders backend priority pills", async ({ page }) => {
  await mockApi(page);
  await page.goto("/");
  await page.getByRole("button", { name: "Sign in" }).click();

  const failed = page.getByRole("row", { name: /statement_failed\.pdf/ });
  const medium = page.getByRole("row", { name: /statement_medium\.pdf/ });
  const verified = page.getByRole("row", { name: /statement_verified\.pdf/ });

  await expect(failed.getByText("P1")).toBeVisible();
  await expect(medium.getByText("P2")).toBeVisible();
  await expect(verified.getByText("P3")).toBeVisible();
  await expect(failed.getByRole("cell").nth(3)).not.toContainText("Failed Holder");
});

test("clicking a dashboard row navigates to matching document route", async ({ page }) => {
  await mockApi(page);
  await page.goto("/");
  await page.getByRole("button", { name: "Sign in" }).click();

  await page.getByRole("row", { name: /statement_noisy\.pdf/ }).click();

  await expect(page).toHaveURL(/\/documents\/9\/review$/);
});

test("summary cards filter and update the URL", async ({ page }) => {
  await mockApi(page);
  await page.goto("/");
  await page.getByRole("button", { name: "Sign in" }).click();

  await page.getByRole("button", { name: /Needs review/ }).click();

  await expect(page).toHaveURL(/filter=needs_review/);
  await expect(page.getByRole("row", { name: /statement_processing\.pdf/ })).toHaveCount(0);
});

test("sortable headers update URL sort state", async ({ page }) => {
  await mockApi(page);
  await page.goto("/");
  await page.getByRole("button", { name: "Sign in" }).click();

  await page.getByRole("button", { name: /Confidence/ }).click();

  await expect(page).toHaveURL(/sort=confidence_score/);
  await expect(page).toHaveURL(/order=asc/);
});

test("search filters after debounce and survives reload", async ({ page }) => {
  await mockApi(page);
  await page.goto("/");
  await page.getByRole("button", { name: "Sign in" }).click();

  await page.getByPlaceholder("Search filename or account").fill("verified");

  await expect(page).toHaveURL(/q=verified/);
  await expect(page.getByRole("row", { name: /statement_verified\.pdf/ })).toBeVisible();
  await expect(page.getByRole("row", { name: /statement_noisy\.pdf/ })).toHaveCount(0);

  await page.reload();
  await expect(page.getByPlaceholder("Search filename or account")).toHaveValue("verified");
});

test("bulk select shows action bar and reparse calls bulk endpoint", async ({ page }) => {
  await mockApi(page);
  await page.goto("/");
  await page.getByRole("button", { name: "Sign in" }).click();

  await page.getByLabel("Select statement_verified.pdf").check();
  await page.getByLabel("Select statement_approved.pdf").check();
  const requestPromise = page.waitForRequest((request) => request.url() === "http://localhost:8003/documents/bulk" && request.method() === "POST");
  await page.getByRole("button", { name: "Re-parse selected" }).click();
  const request = await requestPromise;

  expect(request.postDataJSON()).toEqual({ action: "reparse", document_ids: [7, 6] });
  await expect(page.getByText("2 documents updated.")).toBeVisible();
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

test("admin failures page shows expandable samples", async ({ page }) => {
  await mockApi(page, ["admin", "reviewer", "uploader"]);
  await page.goto("/");
  await page.getByRole("button", { name: "Sign in" }).click();
  await page.getByRole("link", { name: "Failures" }).click();

  await expect(page.getByRole("heading", { name: "Failure analysis" })).toBeVisible();
  await page.getByRole("button", { name: "other" }).click();
  await expect(page.getByText("transactions[0].description")).toBeVisible();
  await expect(page.getByText("#9 statement_noisy.pdf")).toBeVisible();
});
