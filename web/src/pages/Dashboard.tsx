import { useQuery } from "@tanstack/react-query";
import { api, type Document } from "../api";

function StatusBadge({ status }: { status: string }) {
  const styles: Record<string, string> = {
    verified: "bg-emerald-100 text-emerald-700",
    needs_review: "bg-amber-100 text-amber-700",
    uploaded: "bg-slate-100 text-slate-700",
  };
  const cls = styles[status] || "bg-slate-100 text-slate-700";
  return <span className={`px-2 py-0.5 rounded text-xs font-medium ${cls}`}>{status}</span>;
}

export default function Dashboard() {
  const { data, isLoading, error } = useQuery({
    queryKey: ["documents"],
    queryFn: async () => (await api.get<Document[]>("/documents")).data,
  });

  if (isLoading) return <p className="text-slate-500">Loading…</p>;
  if (error) return <p className="text-rose-600">Failed to load documents.</p>;

  return (
    <div>
      <h1 className="text-2xl font-semibold mb-6">Documents</h1>
      <div className="bg-white border border-slate-200 rounded-lg overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-slate-50 text-slate-600">
            <tr>
              <th className="text-left px-4 py-3">ID</th>
              <th className="text-left px-4 py-3">Filename</th>
              <th className="text-left px-4 py-3">Status</th>
            </tr>
          </thead>
          <tbody>
            {data?.map((d) => (
              <tr key={d.id} className="border-t border-slate-100">
                <td className="px-4 py-3 text-slate-500">#{d.id}</td>
                <td className="px-4 py-3">{d.filename}</td>
                <td className="px-4 py-3"><StatusBadge status={d.status} /></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
