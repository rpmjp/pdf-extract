import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import AccountCard from "../components/AccountCard";
import PdfViewer from "../components/PdfViewer";
import ReconciliationPanel from "../components/ReconciliationPanel";
import TransactionsTable from "../components/TransactionsTable";
import { api, type DocumentDetail } from "../api";

function StatusBadge({ status }: { status: string }) {
  const styles: Record<string, string> = {
    verified: "bg-emerald-100 text-emerald-700",
    needs_review: "bg-amber-100 text-amber-700",
    uploaded: "bg-slate-100 text-slate-700",
  };
  const cls = styles[status] || "bg-slate-100 text-slate-700";
  return <span className={`rounded px-2 py-0.5 text-xs font-medium ${cls}`}>{status}</span>;
}

export default function DocumentPage() {
  const { id } = useParams();
  const { data, isLoading, error } = useQuery({
    queryKey: ["document", id],
    queryFn: async () => (await api.get<DocumentDetail>(`/documents/${id}`)).data,
    enabled: Boolean(id),
  });

  if (isLoading) return <p className="text-slate-500">Loading...</p>;
  if (error || !data || !id) return <p className="text-rose-600">Failed to load document.</p>;

  const pdfUrl = `${api.defaults.baseURL}/documents/${id}/file`;

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
          <StatusBadge status={data.status} />
        </div>
      </div>

      <div className="grid min-h-0 flex-1 gap-6 pt-6 lg:grid-cols-[minmax(0,1.05fr)_minmax(420px,0.95fr)]">
        <PdfViewer url={pdfUrl} />

        <div className="min-h-0 space-y-5 overflow-y-auto pr-1">
          <AccountCard document={data} />
          <ReconciliationPanel reconciliation={data.reconciliation} />

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

          <TransactionsTable transactions={data.transactions} />
        </div>
      </div>
    </div>
  );
}
