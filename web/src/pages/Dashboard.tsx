import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { api, type Document } from "../api";

function StatusBadge({ status }: { status: string }) {
  const styles: Record<string, string> = {
    verified: "bg-emerald-100 text-emerald-700",
    approved: "bg-emerald-100 text-emerald-700",
    needs_review: "bg-amber-100 text-amber-700",
    queued: "bg-sky-100 text-sky-700",
    parsing: "bg-sky-100 text-sky-700",
    failed: "bg-rose-100 text-rose-700",
    uploaded: "bg-slate-100 text-slate-700",
  };
  const cls = styles[status] || "bg-slate-100 text-slate-700";
  return <span className={`px-2 py-0.5 rounded text-xs font-medium ${cls}`}>{status}</span>;
}

function formatConfidence(value?: number | null) {
  return typeof value === "number" ? `${Math.round(value * 100)}%` : "—";
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

  return (
    <div>
      <h1 className="text-2xl font-semibold mb-6">{isReview ? "Review queue" : "Documents"}</h1>
      <div className="bg-white border border-slate-200 rounded-lg overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-slate-50 text-slate-600">
            <tr>
              <th className="text-left px-4 py-3">ID</th>
              <th className="text-left px-4 py-3">Filename</th>
              <th className="text-left px-4 py-3">Status</th>
              <th className="text-right px-4 py-3">Confidence</th>
            </tr>
          </thead>
          <tbody>
            {data?.map((d) => (
              <tr
                key={d.id}
                onClick={() => navigate(isReview ? `/documents/${d.id}/review` : `/documents/${d.id}`)}
                className="cursor-pointer border-t border-slate-100 hover:bg-slate-50"
              >
                <td className="px-4 py-3 text-slate-500">#{d.id}</td>
                <td className="px-4 py-3">{d.filename}</td>
                <td className="px-4 py-3"><StatusBadge status={d.status} /></td>
                <td className="px-4 py-3 text-right tabular-nums text-slate-600">{formatConfidence(d.confidence_score)}</td>
              </tr>
            ))}
            {data?.length === 0 && (
              <tr>
                <td colSpan={4} className="px-4 py-8 text-center text-slate-500">
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
