import { useQuery } from "@tanstack/react-query";
import { Link, useLocation, useParams } from "react-router-dom";
import AccountCard from "../components/AccountCard";
import AuditLog from "../components/AuditLog";
import EditableTransactionsTable from "../components/EditableTransactionsTable";
import PdfViewer from "../components/PdfViewer";
import ReconciliationPanel from "../components/ReconciliationPanel";
import ReviewControls from "../components/ReviewControls";
import TransactionsTable from "../components/TransactionsTable";
import { api, getAuthToken, type DocumentDetail } from "../api";

function StatusBadge({ status }: { status: string }) {
  const styles: Record<string, string> = {
    verified: "bg-emerald-100 text-emerald-700",
    approved: "bg-emerald-100 text-emerald-700",
    needs_review: "bg-amber-100 text-amber-700",
    queued: "bg-sky-100 text-sky-700",
    parsing: "bg-sky-100 text-sky-700",
    failed: "bg-rose-100 text-rose-700",
    rejected: "bg-rose-100 text-rose-700",
    uploaded: "bg-slate-100 text-slate-700",
  };
  const cls = styles[status] || "bg-slate-100 text-slate-700";
  return <span className={`rounded px-2 py-0.5 text-xs font-medium ${cls}`}>{status}</span>;
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
              <AccountCard document={data} />
              <ReconciliationPanel reconciliation={data.reconciliation} />
            </>
          )}

          {data.review_items.length > 0 && (
            <section className="rounded-lg border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900">
              <h2 className="font-semibold">Review items</h2>
              <ul className="mt-2 space-y-1">
                {data.review_items.map((item) => (
                  <li key={item.id}>{item.reason}</li>
                ))}
              </ul>
            </section>
          )}

          {isProcessing || hasFailed ? (
            <AuditLog entries={data.audit_log} />
          ) : isEditing ? (
            <>
              <EditableTransactionsTable documentId={data.id} transactions={data.transactions} />
              <ReviewControls documentId={data.id} />
              <AuditLog entries={data.audit_log} />
            </>
          ) : (
            <>
              <TransactionsTable transactions={data.transactions} />
              <AuditLog entries={data.audit_log} />
            </>
          )}
        </div>
      </div>
    </div>
  );
}
