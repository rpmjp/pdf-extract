/**
 * Documents and review queue dashboard.
 *
 * This page is intentionally URL-driven: filters, search, sort, and pagination
 * are stored in query params so review links are shareable and browser history
 * behaves predictably. The backend owns priority sorting and filtering.
 */

import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate, useSearchParams } from "react-router-dom";
import { api, type BulkDocumentsResponse, type Document, type DocumentStats, type PaginatedDocumentsResponse } from "../api";
import ConfidenceMeter, { formatConfidence } from "../components/ConfidenceMeter";
import StatusBadge, { PriorityBadge } from "../components/StatusBadge";

type FilterValue = "needs_review" | "processing";
type SortField = "filename" | "account_holder" | "priority" | "status" | "confidence_score";

const SORT_LABELS: { label: string; field: SortField; align?: "left" | "right" }[] = [
  { label: "Document", field: "filename" },
  { label: "Account", field: "account_holder" },
  { label: "Priority", field: "priority" },
  { label: "Status", field: "status" },
  { label: "Confidence", field: "confidence_score", align: "right" },
];

function formatDate(value?: string) {
  /** Format backend timestamps for compact table metadata. */

  if (!value) return "—";
  return new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }).format(new Date(value));
}

function documentHref(doc: Document) {
  /** Send risky documents directly to the review/edit route. */

  return doc.status === "needs_review" || doc.status === "failed" ? `/documents/${doc.id}/review` : `/documents/${doc.id}`;
}

function isInteractiveTarget(target: EventTarget | null) {
  /** Prevent row navigation when users interact with controls inside the row. */

  return target instanceof HTMLElement && Boolean(target.closest("button,a,input,select,textarea"));
}

function normalizeDocumentsResponse(data: PaginatedDocumentsResponse | Document[]): PaginatedDocumentsResponse {
  /** Accept legacy array responses while newer endpoints return pagination. */

  if (Array.isArray(data)) {
    return {
      items: data,
      total: data.length,
      page: 1,
      per_page: data.length || 25,
    };
  }
  return data;
}

function StatCard({
  label,
  value,
  theme,
  tone = "slate",
  active = false,
  disabled = false,
  onClick,
}: {
  label: string;
  value: string | number;
  theme: "light" | "dark";
  tone?: "slate" | "amber" | "sky" | "rose";
  active?: boolean;
  disabled?: boolean;
  onClick?: () => void;
}) {
  /** Reusable summary card that can behave as either display or filter button. */

  const dark = theme === "dark";
  const styles = dark
    ? {
        slate: "border-slate-800 bg-[#101827] text-slate-100",
        amber: "border-amber-400/50 bg-[#231118] text-amber-100",
        sky: "border-sky-500/40 bg-[#0c1b2a] text-sky-100",
        rose: "border-rose-500/40 bg-[#28121d] text-rose-100",
      }
    : {
        slate: "border-slate-200 bg-white text-slate-900",
        amber: "border-amber-200 bg-amber-50 text-amber-900",
        sky: "border-sky-200 bg-sky-50 text-sky-900",
        rose: "border-rose-200 bg-rose-50 text-rose-900",
      };
  const accents = {
    slate: "before:bg-slate-300",
    amber: "before:bg-amber-400",
    sky: "before:bg-sky-400",
    rose: "before:bg-rose-400",
  };
  const cls = `relative overflow-hidden rounded-lg border p-4 text-left shadow-sm transition before:absolute before:inset-x-0 before:top-0 before:h-1 ${styles[tone]} ${accents[tone]} ${
    active ? (dark ? "ring-2 ring-offset-1 ring-cyan-500 ring-offset-[#070b16]" : "ring-2 ring-offset-1 ring-blue-300") : ""
  } ${disabled ? "cursor-not-allowed opacity-50" : onClick ? `cursor-pointer hover:-translate-y-0.5 hover:shadow-md ${dark ? "hover:border-slate-600" : "hover:border-slate-300"}` : ""}`;
  if (!onClick) {
    return (
      <div className={cls}>
        <p className="text-xs font-medium uppercase tracking-wide opacity-70">{label}</p>
        <p className="mt-2 text-2xl font-semibold tabular-nums">{value}</p>
      </div>
    );
  }
  return (
    <button type="button" onClick={onClick} disabled={disabled} className={cls}>
      <p className="text-xs font-medium uppercase tracking-wide opacity-70">{label}</p>
      <p className="mt-2 text-2xl font-semibold tabular-nums">{value}</p>
    </button>
  );
}

function SortHeader({
  label,
  field,
  align = "left",
  theme,
  activeField,
  activeOrder,
  onSort,
}: {
  label: string;
  field: SortField;
  align?: "left" | "right";
  theme: "light" | "dark";
  activeField: string | null;
  activeOrder: string | null;
  onSort: (field: SortField) => void;
}) {
  /** Clickable column header that cycles backend sort params. */

  const active = activeField === field;
  const arrow = active ? (activeOrder === "asc" ? "↑" : "↓") : "";
  const dark = theme === "dark";
  return (
    <th className={`px-4 py-3 ${align === "right" ? "text-right" : "text-left"}`}>
      <button
        type="button"
        onClick={() => onSort(field)}
        className={`inline-flex items-center gap-1 rounded text-sm font-medium ${dark ? "text-slate-300 hover:text-cyan-200" : "text-slate-600 hover:text-blue-700"} ${align === "right" ? "justify-end" : ""}`}
      >
        {label}
        <span className="w-3 text-xs text-slate-400">{arrow}</span>
      </button>
    </th>
  );
}

export default function Dashboard({ mode = "documents", theme = "light" }: { mode?: "documents" | "review"; theme?: "light" | "dark" }) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [searchParams, setSearchParams] = useSearchParams();
  const [searchText, setSearchText] = useState(searchParams.get("q") || "");
  const [selectedIds, setSelectedIds] = useState<number[]>([]);
  const [message, setMessage] = useState<string | null>(null);
  const [rejectOpen, setRejectOpen] = useState(false);
  const [rejectReason, setRejectReason] = useState("");
  const isReview = mode === "review";
  const dark = theme === "dark";

  const filter = searchParams.get("filter") as FilterValue | null;
  const sort = searchParams.get("sort");
  const order = searchParams.get("order");
  const page = Number(searchParams.get("page") || "1");
  const perPage = Number(searchParams.get("per_page") || "25");
  const q = searchParams.get("q") || "";

  useEffect(() => {
    // Keep the search input in sync when browser navigation changes query params.
    setSearchText(searchParams.get("q") || "");
  }, [searchParams]);

  useEffect(() => {
    // Debounce search updates so typing does not issue one request per keypress.
    const id = window.setTimeout(() => {
      const current = searchParams.get("q") || "";
      if (searchText === current) return;
      const next = new URLSearchParams(searchParams);
      if (searchText.trim()) next.set("q", searchText.trim());
      else next.delete("q");
      next.delete("page");
      setSearchParams(next);
    }, 300);
    return () => window.clearTimeout(id);
  }, [searchParams, searchText, setSearchParams]);

  const params = useMemo(() => {
    // Build only valid backend params. Invalid page-size values fall back to the
    // supported set to avoid surprising API requests.
    const next: Record<string, string | number> = { page: Number.isFinite(page) ? page : 1, per_page: [25, 50, 100].includes(perPage) ? perPage : 25 };
    if (filter) next.filter = filter;
    if (q) next.q = q;
    if (sort && order) {
      next.sort = sort;
      next.order = order;
    }
    return next;
  }, [filter, order, page, perPage, q, sort]);

  const queryKey = [isReview ? "review-queue" : "documents", params];
  const { data, isLoading, error } = useQuery({
    queryKey,
    queryFn: async () => {
      const response = await api.get<PaginatedDocumentsResponse | Document[]>(isReview ? "/review-queue" : "/documents", { params });
      return normalizeDocumentsResponse(response.data);
    },
    refetchInterval: 4000,
  });

  const { data: stats } = useQuery({
    // Stats intentionally poll independently from rows so in-flight jobs update
    // the dashboard even if the current table page is unchanged.
    queryKey: ["document-stats"],
    queryFn: async () => {
      const response = await api.get<DocumentStats>("/documents/stats");
      return response.data;
    },
    refetchInterval: 4000,
  });

  const bulk = useMutation({
    mutationFn: (payload: { action: "reparse" | "approve" | "reject"; document_ids: number[]; reason?: string }) =>
      api.post<BulkDocumentsResponse>("/documents/bulk", payload).then((response) => response.data),
    onSuccess: (result) => {
      // Bulk actions can move documents between the main dashboard and review
      // queue, so both cache families are invalidated together.
      queryClient.invalidateQueries({ queryKey: ["documents"] });
      queryClient.invalidateQueries({ queryKey: ["review-queue"] });
      setSelectedIds([]);
      setRejectOpen(false);
      const failedText = result.failed.length ? ` ${result.failed.length} failed.` : "";
      setMessage(`${result.succeeded.length} document${result.succeeded.length === 1 ? "" : "s"} updated.${failedText}`);
    },
    onError: () => setMessage("Bulk action failed. Check selected documents and try again."),
  });

  useEffect(() => {
    // Selection is page/filter scoped. Clearing it prevents accidental actions
    // against rows the reviewer can no longer see.
    setSelectedIds([]);
    setMessage(null);
  }, [queryKey[0], JSON.stringify(params)]);

  if (isLoading) return <p className="text-slate-500">Loading…</p>;
  if (error) return <p className="text-rose-600">Failed to load documents.</p>;

  const documents = data?.items || [];
  const total = data?.total || 0;
  const currentPage = data?.page || 1;
  const currentPerPage = data?.per_page || 25;
  const totalPages = Math.max(1, Math.ceil(total / currentPerPage));
  const needsReview = stats?.needs_review ?? 0;
  const inFlight = stats?.processing ?? 0;
  const selectedDocuments = documents.filter((doc) => selectedIds.includes(doc.id));
  const allVisibleSelected = documents.length > 0 && documents.every((doc) => selectedIds.includes(doc.id));
  const canReparse = selectedDocuments.length > 0 && selectedDocuments.every((doc) => doc.status !== "queued" && doc.status !== "parsing");
  const canApprove = selectedDocuments.length > 0 && selectedDocuments.every((doc) => doc.status === "verified");
  const canReject = selectedDocuments.length > 0 && selectedDocuments.every((doc) => doc.status === "needs_review" || doc.status === "failed");

  const updateParams = (changes: Record<string, string | number | null>) => {
    /** Merge query-param changes while resetting pagination by default. */

    const next = new URLSearchParams(searchParams);
    Object.entries(changes).forEach(([key, value]) => {
      if (value === null || value === "") next.delete(key);
      else next.set(key, String(value));
    });
    if (!("page" in changes)) next.delete("page");
    setSearchParams(next);
  };

  const cycleSort = (field: SortField) => {
    /** Cycle a column from asc to desc to backend default ordering. */

    if (sort !== field) {
      updateParams({ sort: field, order: "asc" });
    } else if (order === "asc") {
      updateParams({ sort: field, order: "desc" });
    } else {
      updateParams({ sort: null, order: null });
    }
  };

  const toggleRow = (id: number) => {
    /** Toggle one row in the current selection set. */

    setSelectedIds((current) => (current.includes(id) ? current.filter((value) => value !== id) : [...current, id]));
  };

  const runBulk = (action: "reparse" | "approve" | "reject", reason?: string) => {
    /** Dispatch the selected IDs to the backend bulk endpoint. */

    if (!selectedIds.length) return;
    bulk.mutate({ action, document_ids: selectedIds, reason });
  };

  return (
    <div>
      <section className={`mb-6 overflow-hidden rounded-xl border shadow-sm ${dark ? "border-slate-800 bg-[#101827]" : "border-slate-200 bg-white"}`}>
        <div className={`border-b px-5 py-3 ${dark ? "border-slate-800 bg-[#0b1120]" : "border-slate-200 bg-[#f8fafc]"}`}>
          <p className={`text-xs font-semibold uppercase tracking-wide ${dark ? "text-slate-400" : "text-slate-500"}`}>
            {isReview ? "Human review workbench" : "Operations command center"}
          </p>
        </div>
        <div className="flex flex-col gap-5 p-5 lg:flex-row lg:items-end lg:justify-between">
          <div>
            <h1 className={`text-3xl font-semibold tracking-tight ${dark ? "text-white" : "text-slate-950"}`}>{isReview ? "Review queue" : "Documents"}</h1>
            <p className={`mt-2 max-w-2xl text-sm leading-6 ${dark ? "text-slate-400" : "text-slate-600"}`}>
              {isReview
                ? "Work the highest-risk statements first, with confidence, review reasons, and reconciliation signals in one queue."
                : "Track statement intake, parsing progress, priority, and confidence without losing the documents that need attention."}
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <button
              type="button"
              onClick={() => updateParams({ filter: "needs_review" })}
              className={`rounded-lg border px-3 py-2 text-sm font-semibold ${
                dark ? "border-amber-400/50 bg-amber-400/10 text-amber-100 hover:bg-amber-400/20" : "border-amber-300 bg-amber-50 text-amber-900 hover:bg-amber-100"
              }`}
            >
              Review now
            </button>
            <button
              type="button"
              onClick={() => navigate("/upload")}
              className={`rounded-lg px-3 py-2 text-sm font-semibold shadow-sm ${dark ? "bg-white text-slate-950 hover:bg-slate-200" : "bg-blue-700 text-white hover:bg-blue-800"}`}
            >
              Upload PDF
            </button>
            <button
              type="button"
              onClick={() => navigate("/insights/confidence")}
              className={`rounded-lg border px-3 py-2 text-sm font-semibold ${
                dark ? "border-slate-700 text-slate-200 hover:bg-slate-800 hover:text-cyan-200" : "border-slate-300 text-slate-700 hover:bg-blue-50 hover:text-blue-700"
              }`}
            >
              Insights
            </button>
          </div>
        </div>
      </section>

      <div className="mb-6 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard theme={theme} label="Total" value={stats?.total ?? total} active={!filter} onClick={() => updateParams({ filter: null, q: null, sort: null, order: null })} />
        <StatCard
          theme={theme}
          label="Needs review"
          value={needsReview}
          tone={filter === "needs_review" || needsReview ? "amber" : "slate"}
          active={filter === "needs_review"}
          onClick={() => updateParams({ filter: "needs_review" })}
        />
        <StatCard
          theme={theme}
          label="Processing"
          value={inFlight}
          tone={inFlight ? "sky" : "slate"}
          active={filter === "processing"}
          disabled={inFlight === 0}
          onClick={() => updateParams({ filter: "processing" })}
        />
        <StatCard
          theme={theme}
          label="Avg confidence"
          value={formatConfidence(stats?.avg_confidence ?? null)}
          onClick={() => navigate("/insights/confidence")}
        />
      </div>

      <div className="mb-3 flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
        <label className="w-full sm:max-w-xs">
          <span className={`mb-1 block text-xs font-semibold uppercase tracking-wide ${dark ? "text-slate-400" : "text-slate-500"}`}>Search documents</span>
          <input
            value={searchText}
            onChange={(event) => setSearchText(event.target.value)}
            placeholder="Search filename or account"
            className={`w-full rounded-lg border px-3 py-2 text-sm shadow-sm outline-none focus:ring-2 ${
              dark
                ? "border-slate-700 bg-[#101827] text-slate-100 placeholder:text-slate-500 focus:border-cyan-500 focus:ring-cyan-500/30"
                : "border-slate-300 bg-white text-slate-900 placeholder:text-slate-400 focus:border-blue-500 focus:ring-blue-200"
            }`}
          />
        </label>
        <div className={`rounded-full border px-3 py-1.5 text-sm shadow-sm ${dark ? "border-slate-800 bg-[#101827] text-slate-400" : "border-slate-200 bg-white text-slate-500"}`}>
          Showing {documents.length ? (currentPage - 1) * currentPerPage + 1 : 0}-{Math.min(currentPage * currentPerPage, total)} of {total}
        </div>
      </div>

      {message && <div className="mb-3 rounded-lg border border-slate-200 bg-white px-4 py-3 text-sm text-slate-700">{message}</div>}

      {selectedIds.length > 0 && (
        <div className="mb-3 flex flex-wrap items-center gap-2 rounded-lg border border-slate-200 bg-white px-4 py-3 text-sm">
          <span className="font-medium text-slate-900">{selectedIds.length} selected</span>
          <button
            type="button"
            disabled={!canReparse || bulk.isPending}
            onClick={() => runBulk("reparse")}
            className="rounded border border-slate-300 px-3 py-1.5 font-medium text-slate-700 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-50"
          >
            Re-parse selected
          </button>
          <button
            type="button"
            disabled={!canApprove || bulk.isPending}
            onClick={() => runBulk("approve")}
            className="rounded border border-emerald-200 bg-emerald-50 px-3 py-1.5 font-medium text-emerald-800 hover:bg-emerald-100 disabled:cursor-not-allowed disabled:opacity-50"
          >
            Approve selected
          </button>
          <button
            type="button"
            disabled={!canReject || bulk.isPending}
            onClick={() => setRejectOpen(true)}
            className="rounded border border-rose-200 bg-rose-50 px-3 py-1.5 font-medium text-rose-700 hover:bg-rose-100 disabled:cursor-not-allowed disabled:opacity-50"
          >
            Reject selected
          </button>
        </div>
      )}

      <div className={`overflow-hidden rounded-xl border shadow-sm ${dark ? "border-slate-800 bg-[#101827]" : "border-slate-200 bg-white"}`}>
        <table className="w-full text-sm">
          <thead className={dark ? "bg-[#0b1120] text-slate-300" : "bg-[#f8fafc] text-slate-600"}>
            <tr>
              <th className="w-10 px-4 py-3 text-left">
                <input
                  aria-label="Select all visible documents"
                  type="checkbox"
                  checked={allVisibleSelected}
                  onChange={(event) => setSelectedIds(event.target.checked ? documents.map((doc) => doc.id) : [])}
                  className="h-4 w-4 rounded border-slate-300"
                />
              </th>
              {SORT_LABELS.map((header) => (
                <SortHeader
                  key={header.field}
                  label={header.label}
                  field={header.field}
                  align={header.align}
                  theme={theme}
                  activeField={sort}
                  activeOrder={order}
                  onSort={cycleSort}
                />
              ))}
            </tr>
          </thead>
          <tbody>
            {documents.map((d) => (
              <tr
                key={d.id}
                tabIndex={0}
                onClick={(event) => {
                  if (!isInteractiveTarget(event.target)) navigate(documentHref(d));
                }}
                onKeyDown={(event) => {
                  if ((event.key === "Enter" || event.key === " ") && !isInteractiveTarget(event.target)) {
                    event.preventDefault();
                    navigate(documentHref(d));
                  }
                }}
                className={`cursor-pointer border-t focus:outline-none focus:ring-2 focus:ring-inset ${
                  dark ? "border-slate-800 hover:bg-slate-800/70 focus:bg-slate-800/70 focus:ring-cyan-500/40" : "border-slate-100 hover:bg-blue-50/70 focus:bg-blue-50/70 focus:ring-blue-200"
                }`}
              >
                <td className="px-4 py-3">
                  <input
                    aria-label={`Select ${d.filename}`}
                    type="checkbox"
                    checked={selectedIds.includes(d.id)}
                    onClick={(event) => event.stopPropagation()}
                    onChange={() => toggleRow(d.id)}
                    className="h-4 w-4 rounded border-slate-300"
                  />
                </td>
                <td className="px-4 py-3">
                  <p className={`font-semibold ${dark ? "text-slate-100" : "text-slate-950"}`}>{d.filename}</p>
                  <p className={`mt-0.5 text-xs ${dark ? "text-slate-400" : "text-slate-500"}`}>
                    #{d.id} · {formatDate(d.created_at)}
                  </p>
                </td>
                <td className="px-4 py-3">
                  <p className={`font-semibold ${dark ? "text-slate-100" : "text-slate-800"}`}>{d.account_holder || "—"}</p>
                  <p className={`mt-0.5 text-xs ${dark ? "text-slate-400" : "text-slate-500"}`}>{d.account_number || d.statement_period || "No account details yet"}</p>
                </td>
                <td className="px-4 py-3">
                  <PriorityBadge priority={d.priority} />
                </td>
                <td className="px-4 py-3">
                  <StatusBadge status={d.status} />
                </td>
                <td className="px-4 py-3 text-right">
                  <ConfidenceMeter value={d.confidence_score} compact />
                </td>
              </tr>
            ))}
            {documents.length === 0 && (
              <tr>
                <td colSpan={6} className="px-4 py-8 text-center text-slate-500">
                  {isReview ? "No documents need review." : "No documents yet."}
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      <div className="mt-4 flex flex-col gap-3 text-sm text-slate-600 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex items-center gap-2">
          <button
            type="button"
            disabled={currentPage <= 1}
            onClick={() => updateParams({ page: currentPage - 1 })}
            className="rounded border border-slate-300 px-3 py-1.5 font-medium hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-50"
          >
            « Prev
          </button>
          <span>
            Page {currentPage} of {totalPages}
          </span>
          <button
            type="button"
            disabled={currentPage >= totalPages}
            onClick={() => updateParams({ page: currentPage + 1 })}
            className="rounded border border-slate-300 px-3 py-1.5 font-medium hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-50"
          >
            Next »
          </button>
        </div>
        <label className="flex items-center gap-2">
          Per page
          <select
            value={currentPerPage}
            onChange={(event) => updateParams({ per_page: event.target.value, page: 1 })}
            className="rounded border border-slate-300 bg-white px-2 py-1.5"
          >
            <option value="25">25</option>
            <option value="50">50</option>
            <option value="100">100</option>
          </select>
        </label>
      </div>

      {rejectOpen && (
        <div className="fixed inset-0 z-10 flex items-center justify-center bg-slate-900/30 p-4">
          <div className="w-full max-w-md rounded-lg bg-white p-5 shadow-xl">
            <h2 className="text-lg font-semibold text-slate-900">Reject selected documents</h2>
            <label className="mt-4 block text-sm font-medium text-slate-700">
              Reason
              <textarea
                value={rejectReason}
                onChange={(event) => setRejectReason(event.target.value)}
                className="mt-2 min-h-24 w-full rounded border border-slate-300 px-3 py-2 text-sm outline-none focus:border-slate-500 focus:ring-2 focus:ring-slate-200"
                placeholder="Rejected by reviewer"
              />
            </label>
            <div className="mt-4 flex justify-end gap-2">
              <button type="button" onClick={() => setRejectOpen(false)} className="rounded border border-slate-300 px-3 py-2 text-sm font-medium text-slate-700">
                Cancel
              </button>
              <button
                type="button"
                onClick={() => runBulk("reject", rejectReason || undefined)}
                disabled={bulk.isPending}
                className="rounded bg-rose-600 px-3 py-2 text-sm font-medium text-white hover:bg-rose-700 disabled:opacity-50"
              >
                Reject selected
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
