import { useQuery } from "@tanstack/react-query";
import { Link, useLocation, useParams } from "react-router-dom";
import AccountCard from "../components/AccountCard";
import AuditLog from "../components/AuditLog";
import ConfidenceMeter from "../components/ConfidenceMeter";
import EditableTransactionsTable from "../components/EditableTransactionsTable";
import PdfViewer from "../components/PdfViewer";
import ReconciliationPanel from "../components/ReconciliationPanel";
import ReviewControls from "../components/ReviewControls";
import StatusBadge from "../components/StatusBadge";
import TransactionsTable from "../components/TransactionsTable";
import { api, getAuthToken, type DocumentDetail, type ReviewItem } from "../api";

function issueTarget(reason: string) {
  const lower = reason.toLowerCase();
  if (lower.includes("opening") || lower.includes("closing") || lower.includes("account") || lower.includes("holder") || lower.includes("period")) return "account-info";
  if (lower.includes("transaction") || lower.includes("row") || lower.includes("balance")) return "transactions";
  if (lower.includes("reconcil") || lower.includes("statement_balance") || lower.includes("status")) return "reconciliation";
  return "review-signal";
}

function hasIssueFor(items: ReviewItem[], target: string) {
  return items.some((item) => issueTarget(item.reason) === target);
}

function originalTransactions(document: DocumentDetail) {
  const candidates = document.versions
    .filter((version) => version.source !== "review_edit" && version.data.transactions?.length)
    .sort((a, b) => new Date(a.created_at).getTime() - new Date(b.created_at).getTime());
  return candidates[0]?.data.transactions || document.transactions;
}

function TrustSummary({ document }: { document: DocumentDetail }) {
  const failedChecks = document.reconciliation.checks.filter((check) => !check.passed);
  const hasOpenReview = document.review_items.some((item) => item.status === "open");
  const confidence = document.confidence_score;
  const lowConfidence = typeof confidence === "number" && confidence < 0.75;
  const processing = document.status === "queued" || document.status === "parsing";

  return (
    <section className="rounded-lg border border-slate-200 bg-white p-5">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h2 className="text-base font-semibold text-slate-900">Review signal</h2>
          <p className="mt-1 text-sm text-slate-500">
            {processing
              ? "Waiting for extraction to finish."
              : hasOpenReview || failedChecks.length || lowConfidence
                ? "Start with the highlighted issues before approving."
                : "Extraction and reconciliation are in good shape."}
          </p>
        </div>
        <StatusBadge status={document.status} />
      </div>

      <div className="mt-5">
        <ConfidenceMeter value={document.confidence_score} />
      </div>

      <div className="mt-5 grid gap-3 sm:grid-cols-3">
        <div className="rounded border border-slate-200 p-3">
          <p className="text-xs font-medium uppercase tracking-wide text-slate-500">Open issues</p>
          <p className={`mt-1 text-xl font-semibold tabular-nums ${hasOpenReview ? "text-amber-700" : "text-slate-900"}`}>
            {document.review_items.filter((item) => item.status === "open").length}
          </p>
        </div>
        <div className="rounded border border-slate-200 p-3">
          <p className="text-xs font-medium uppercase tracking-wide text-slate-500">Failed checks</p>
          <p className={`mt-1 text-xl font-semibold tabular-nums ${failedChecks.length ? "text-amber-700" : "text-slate-900"}`}>
            {failedChecks.length}
          </p>
        </div>
        <div className="rounded border border-slate-200 p-3">
          <p className="text-xs font-medium uppercase tracking-wide text-slate-500">Transactions</p>
          <p className="mt-1 text-xl font-semibold tabular-nums text-slate-900">{document.transactions.length}</p>
        </div>
      </div>
    </section>
  );
}

function ReviewIssues({ items }: { items: ReviewItem[] }) {
  if (!items.length) return null;
  return (
    <section className="rounded-lg border border-amber-200 bg-amber-50 p-5 text-amber-950">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h2 className="font-semibold">Review items</h2>
          <p className="mt-1 text-sm text-amber-800">Use these as the inspection checklist for this statement.</p>
        </div>
        <span className="rounded bg-amber-100 px-2 py-0.5 text-xs font-medium text-amber-800">{items.length}</span>
      </div>
      <ul className="mt-4 space-y-2 text-sm">
        {items.map((item) => (
          <li key={item.id} className="rounded border border-amber-200 bg-white/70 px-3 py-2">
            <a href={`#${issueTarget(item.reason)}`} className="block hover:text-amber-700">
              <span className="font-medium capitalize">{item.status}</span>
              <span className="mx-2 text-amber-400">/</span>
              {item.reason}
              <span className="ml-2 text-xs font-medium text-amber-700 underline">Jump</span>
            </a>
          </li>
        ))}
      </ul>
    </section>
  );
}

export default function DocumentPage() {
  const { id } = useParams();
  const location = useLocation();
  const searchParams = new URLSearchParams(location.search);
  const isEditing = location.pathname.endsWith("/review") || searchParams.get("edit") === "1";
  const { data, isLoading, error } = useQuery({
    queryKey: ["document", id],
    queryFn: async () => (await api.get<DocumentDetail>(`/documents/${id}`)).data,
    enabled: Boolean(id),
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return status === "queued" || status === "parsing" ? 2000 : false;
    },
  });

  if (isLoading) return <p className="text-slate-500">Loading...</p>;
  if (error || !data || !id) return <p className="text-rose-600">Failed to load document.</p>;

  const token = getAuthToken();
  const pdfUrl = `${api.defaults.baseURL}/documents/${id}/file${token ? `?token=${encodeURIComponent(token)}` : ""}`;
  const isProcessing = data.status === "queued" || data.status === "parsing";
  const hasFailed = data.status === "failed";
  const accountHasIssues = hasIssueFor(data.review_items, "account-info");
  const reconciliationHasIssues = hasIssueFor(data.review_items, "reconciliation");
  const transactionHasIssues = hasIssueFor(data.review_items, "transactions");
  const sourceTransactions = originalTransactions(data);

  return (
    <div className="flex h-[calc(100vh-8rem)] min-h-[720px] flex-col">
      <div className="sticky top-0 z-10 border-b border-slate-200 bg-slate-50 pb-4">
        <Link to="/" className="text-sm font-medium text-slate-600 hover:text-slate-900">
          Back to documents
        </Link>

        <div className="mt-4 flex items-start justify-between gap-4">
          <div>
            <h1 className="text-2xl font-semibold text-slate-900">{data.filename}</h1>
            <p className="mt-1 text-sm text-slate-500">Document #{data.id}</p>
          </div>
          <div className="flex items-center gap-3">
            {isEditing ? (
              <Link to={`/documents/${data.id}`} className="rounded border border-slate-300 px-3 py-2 text-sm font-medium text-slate-700 hover:bg-slate-100">
                View
              </Link>
            ) : (
              <Link to={`/documents/${data.id}/review`} className="rounded bg-slate-900 px-3 py-2 text-sm font-medium text-white hover:bg-slate-700">
                Edit
              </Link>
            )}
            <StatusBadge status={data.status} />
          </div>
        </div>
      </div>

      <div className="grid min-h-0 flex-1 gap-6 pt-6 lg:grid-cols-[minmax(0,1.05fr)_minmax(420px,0.95fr)]">
        <PdfViewer url={pdfUrl} />

        <div className="min-h-0 space-y-5 overflow-y-auto pr-1">
          {isProcessing ? (
            <section className="rounded-lg border border-sky-200 bg-sky-50 p-5 text-sky-900">
              <h2 className="font-semibold">Parsing in progress</h2>
              <p className="mt-2 text-sm">
                This document is {data.status === "queued" ? "waiting for the worker" : "being extracted"}. The parsed fields will appear here when the job finishes.
              </p>
            </section>
          ) : hasFailed ? (
            <section className="rounded-lg border border-rose-200 bg-rose-50 p-5 text-rose-900">
              <h2 className="font-semibold">Parse failed</h2>
              <p className="mt-2 text-sm">The worker could not finish this document. Review items below may include the error.</p>
            </section>
          ) : (
            <>
              <div id="review-signal">
                <TrustSummary document={data} />
              </div>
              <ReviewIssues items={data.review_items} />
              <div id="account-info" className={accountHasIssues ? "rounded-xl ring-2 ring-amber-300 ring-offset-2 ring-offset-slate-50" : ""}>
                <AccountCard document={data} />
              </div>
              <div id="reconciliation" className={reconciliationHasIssues ? "rounded-xl ring-2 ring-amber-300 ring-offset-2 ring-offset-slate-50" : ""}>
                <ReconciliationPanel reconciliation={data.reconciliation} />
              </div>
            </>
          )}

          {isProcessing || hasFailed ? (
            <AuditLog entries={data.audit_log} />
          ) : isEditing ? (
            <>
              <div className={transactionHasIssues ? "rounded-xl ring-2 ring-amber-300 ring-offset-2 ring-offset-slate-50" : ""}>
                <EditableTransactionsTable
                  documentId={data.id}
                  transactions={data.transactions}
                  originalTransactions={sourceTransactions}
                  reviewItems={data.review_items}
                />
              </div>
              <ReviewControls documentId={data.id} />
              <AuditLog entries={data.audit_log} />
            </>
          ) : (
            <>
              <div id="transactions" className={transactionHasIssues ? "rounded-xl ring-2 ring-amber-300 ring-offset-2 ring-offset-slate-50" : ""}>
                <TransactionsTable transactions={data.transactions} />
              </div>
              <AuditLog entries={data.audit_log} />
            </>
          )}
        </div>
      </div>
    </div>
  );
}
