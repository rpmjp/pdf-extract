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

const globalStats = {
  total: documents.length,
  needs_review: documents.filter((d) => d.status === "needs_review" || d.status === "failed").length,
  processing: documents.filter((d) => d.status === "queued").length,
  avg_confidence: 0.64,
};

const insightsOverview = {
  kpis: {
    pass_rate: { current: 0.75, delta: 0.05, previous: 0.70 },
    mean_confidence: { current: 0.82, delta: -0.02, previous: 0.84 },
    median_review_time_hours: { current: 4.5, delta: -1.2, previous: 5.7 },
    auto_approval_rate: { current: 0.65, delta: 0.03, previous: 0.62 },
  },
  alerts: [
    { type: "aged_review", message: "2 docs aged >24h in Needs Review", link: "/documents?filter=needs_review", severity: "warning" },
  ],
  volume_vs_confidence: [
    { document_id: documents[0].id, filename: documents[0].filename, date: "2026-06-01T10:00:00Z", confidence: 0.42, status: "needs_review" },
    { document_id: documents[2].id, filename: documents[2].filename, date: "2026-06-02T10:00:00Z", confidence: 0.94, status: "verified" },
  ],
  pass_rate_trend: [
    { date: "2026-06-01", rate: 0.72, count: 10 },
    { date: "2026-06-02", rate: 0.80, count: 8 },
  ],
  failure_breakdown: [
    { category: "missing_transactions", count: 5, link: "/admin/failures?category=missing_transactions" },
    { category: "balance_mismatch", count: 3, link: "/admin/failures?category=balance_mismatch" },
  ],
  by_source: [
    { source: "digital", count: 20, mean_confidence: 0.88, pass_rate: 0.90, mean_review_hours: null },
    { source: "scanned", count: 8, mean_confidence: 0.62, pass_rate: 0.50, mean_review_hours: null },
  ],
  queue_depth: [{ date: "2026-06-01", depth: 3 }],
  parse_latency: [{ date: "2026-06-01", p50: 4.2, p95: 12.5, p99: 20.1 }],
  failure_rate: [{ date: "2026-06-01", total: 10, failed: 1, rate: 0.1 }],
  lowest: [
    { ...documents[0], time_in_status_hours: 36.5, last_modified_by: "reviewer", priority: "P1" },
    { ...documents[1], time_in_status_hours: 12.0, last_modified_by: null, priority: "P2" },
    { ...documents[2], time_in_status_hours: 2.5, last_modified_by: "admin", priority: "P3" },
  ],
};

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
  await page.route("http://localhost:8003/documents/stats", async (route) => {
    await route.fulfill({ json: globalStats });
  });
  await page.route("http://localhost:8003/insights/overview**", async (route) => {
    await route.fulfill({ json: insightsOverview });
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

test("clicking Needs Review card filters table to needs_review and failed docs only", async ({ page }) => {
  await mockApi(page);
  await page.goto("/");
  await page.getByRole("button", { name: "Sign in" }).click();

  await page.getByRole("button", { name: /Needs review/i }).click();

  await expect(page).toHaveURL(/filter=needs_review/);
  await expect(page.getByRole("row", { name: /statement_failed\.pdf/ })).toBeVisible();
  await expect(page.getByRole("row", { name: /statement_noisy\.pdf/ })).toBeVisible();
  await expect(page.getByRole("row", { name: /statement_verified\.pdf/ })).toHaveCount(0);
  await expect(page.getByRole("row", { name: /statement_approved\.pdf/ })).toHaveCount(0);
  await expect(page.getByRole("row", { name: /statement_processing\.pdf/ })).toHaveCount(0);
});

test("clicking Total card resets filter and shows all docs", async ({ page }) => {
  await mockApi(page);
  await page.goto("/?filter=needs_review");
  await page.getByRole("button", { name: "Sign in" }).click();

  await page.getByRole("button", { name: /Total/i }).click();

  await expect(page).not.toHaveURL(/filter/);
  await expect(page.getByRole("row", { name: /statement_verified\.pdf/ })).toBeVisible();
  await expect(page.getByRole("row", { name: /statement_processing\.pdf/ })).toBeVisible();
});

test("clicking Avg confidence card navigates to insights page", async ({ page }) => {
  await mockApi(page);
  await page.goto("/");
  await page.getByRole("button", { name: "Sign in" }).click();

  await page.getByRole("button", { name: /Avg confidence/i }).click();

  await expect(page).toHaveURL(/\/insights\/confidence/);
  await expect(page.getByRole("heading", { name: "Confidence Insights" })).toBeVisible();
});

test("confidence insights page renders KPIs, alerts, charts, and lowest table", async ({ page }) => {
  await mockApi(page);
  await page.goto("/");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("heading", { name: "Documents" })).toBeVisible();
  await page.goto("/insights/confidence");

  await expect(page.getByRole("heading", { name: "Confidence Insights" })).toBeVisible();

  // KPI cards (buttons containing label + value)
  await expect(page.getByRole("button", { name: /Pass rate.*%/ }).first()).toBeVisible();
  await expect(page.getByRole("button", { name: /Mean confidence.*%/ })).toBeVisible();
  await expect(page.getByRole("button", { name: /Auto-approval rate.*%/ })).toBeVisible();

  // Alert card
  await expect(page.getByText("2 docs aged >24h in Needs Review")).toBeVisible();

  // Charts
  await expect(page.getByText(/Volume vs confidence/i)).toBeVisible();
  await expect(page.getByText(/Pass rate trend/i)).toBeVisible();
  await expect(page.getByText(/Failure categories/i)).toBeVisible();
  await expect(page.getByText(/Performance by source/i)).toBeVisible();
  await expect(page.getByText("Lowest confidence documents")).toBeVisible();

  // Clickable lowest-table row
  await expect(page.getByRole("row", { name: /statement_failed\.pdf/ })).toBeVisible();
  await page.getByRole("row", { name: /statement_failed\.pdf/ }).click();
  await expect(page).toHaveURL(/\/documents\/10\/review/);
});

test("insights range selector updates URL", async ({ page }) => {
  await mockApi(page);
  await page.goto("/");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("heading", { name: "Documents" })).toBeVisible();
  await page.goto("/insights/confidence");
  await expect(page.getByRole("heading", { name: "Confidence Insights" })).toBeVisible();

  await page.getByRole("button", { name: "7d" }).click();
  await expect(page).toHaveURL(/range=7d/);

  await page.getByRole("button", { name: "All" }).click();
  await expect(page).toHaveURL(/range=all/);
});
