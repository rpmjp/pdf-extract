import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { api, type Document } from "../api";
import ConfidenceMeter, { formatConfidence } from "../components/ConfidenceMeter";
import StatusBadge, { PriorityBadge } from "../components/StatusBadge";

function formatDate(value?: string) {
  if (!value) return "—";
  return new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }).format(new Date(value));
}

function averageConfidence(documents: Document[]) {
  const values = documents.map((doc) => doc.confidence_score).filter((value): value is number => typeof value === "number");
  if (!values.length) return null;
  return values.reduce((total, value) => total + value, 0) / values.length;
}

function StatCard({ label, value, tone = "slate" }: { label: string; value: string | number; tone?: "slate" | "amber" | "sky" | "rose" }) {
  const styles = {
    slate: "border-slate-200 bg-white text-slate-900",
    amber: "border-amber-200 bg-amber-50 text-amber-900",
    sky: "border-sky-200 bg-sky-50 text-sky-900",
    rose: "border-rose-200 bg-rose-50 text-rose-900",
  };
  return (
    <div className={`rounded-lg border p-4 ${styles[tone]}`}>
      <p className="text-xs font-medium uppercase tracking-wide opacity-70">{label}</p>
      <p className="mt-2 text-2xl font-semibold tabular-nums">{value}</p>
    </div>
  );
}

export default function Dashboard({ mode = "documents" }: { mode?: "documents" | "review" }) {
  const navigate = useNavigate();
  const isReview = mode === "review";
  const { data, isLoading, error } = useQuery({
    queryKey: [isReview ? "review-queue" : "documents"],
    queryFn: async () => (await api.get<Document[]>(isReview ? "/review-queue" : "/documents")).data,
    refetchInterval: 4000,
  });

  if (isLoading) return <p className="text-slate-500">Loading…</p>;
  if (error) return <p className="text-rose-600">Failed to load documents.</p>;

  const documents = data || [];
  const needsReview = documents.filter((doc) => doc.status === "needs_review").length;
  const inFlight = documents.filter((doc) => doc.status === "queued" || doc.status === "parsing").length;
  const failed = documents.filter((doc) => doc.status === "failed").length;
  const avgConfidence = averageConfidence(documents);

  return (
    <div>
      <div className="mb-6 flex items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold text-slate-900">{isReview ? "Review queue" : "Documents"}</h1>
          <p className="mt-1 text-sm text-slate-500">
            {isReview ? "Prioritized statements that need human attention." : "Operational view of uploaded statements and parse progress."}
          </p>
        </div>
      </div>

      <div className="mb-6 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard label="Total" value={documents.length} />
        <StatCard label="Needs review" value={needsReview} tone={needsReview ? "amber" : "slate"} />
        <StatCard label="Processing" value={inFlight} tone={inFlight ? "sky" : "slate"} />
        <StatCard label={failed ? "Failed" : "Avg confidence"} value={failed || formatConfidence(avgConfidence)} tone={failed ? "rose" : "slate"} />
      </div>

      <div className="overflow-hidden rounded-lg border border-slate-200 bg-white">
        <table className="w-full text-sm">
          <thead className="bg-slate-50 text-slate-600">
            <tr>
              <th className="px-4 py-3 text-left">Document</th>
              <th className="px-4 py-3 text-left">Account</th>
              <th className="px-4 py-3 text-left">Priority</th>
              <th className="px-4 py-3 text-left">Status</th>
              <th className="px-4 py-3 text-right">Confidence</th>
            </tr>
          </thead>
          <tbody>
            {documents.map((d) => (
              <tr
                key={d.id}
                onClick={() => navigate(isReview ? `/documents/${d.id}/review` : `/documents/${d.id}`)}
                className="cursor-pointer border-t border-slate-100 hover:bg-slate-50"
              >
                <td className="px-4 py-3">
                  <p className="font-medium text-slate-900">{d.filename}</p>
                  <p className="mt-0.5 text-xs text-slate-500">#{d.id} · {formatDate(d.created_at)}</p>
                </td>
                <td className="px-4 py-3">
                  <p className="font-medium text-slate-800">{d.account_holder || "—"}</p>
                  <p className="mt-0.5 text-xs text-slate-500">{d.account_number || d.statement_period || "No account details yet"}</p>
                </td>
                <td className="px-4 py-3">
                  <PriorityBadge priority={d.priority} />
                </td>
                <td className="px-4 py-3"><StatusBadge status={d.status} /></td>
                <td className="px-4 py-3 text-right">
                  <ConfidenceMeter value={d.confidence_score} compact />
                </td>
              </tr>
            ))}
            {documents.length === 0 && (
              <tr>
                <td colSpan={5} className="px-4 py-8 text-center text-slate-500">
                  {isReview ? "No documents need review." : "No documents yet."}
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
