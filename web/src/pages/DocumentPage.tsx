import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
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

function formatMoney(value: number | null) {
  if (value === null) return "—";
  return new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" }).format(value);
}

export default function DocumentPage() {
  const { id } = useParams();
  const { data, isLoading, error } = useQuery({
    queryKey: ["document", id],
    queryFn: async () => (await api.get<DocumentDetail>(`/documents/${id}`)).data,
    enabled: Boolean(id),
  });

  if (isLoading) return <p className="text-slate-500">Loading...</p>;
  if (error || !data) return <p className="text-rose-600">Failed to load document.</p>;

  return (
    <div>
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

      {data.review_items.length > 0 && (
        <div className="mt-6 rounded-lg border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900">
          <p className="font-medium">Review items</p>
          <ul className="mt-2 space-y-1">
            {data.review_items.map((item) => (
              <li key={item.id}>{item.reason}</li>
            ))}
          </ul>
        </div>
      )}

      <div className="mt-6 rounded-lg border border-slate-200 bg-white">
        <div className="border-b border-slate-200 px-4 py-3">
          <h2 className="font-semibold text-slate-900">Transactions</h2>
        </div>
        {data.transactions.length === 0 ? (
          <p className="px-4 py-6 text-sm text-slate-500">No transactions parsed yet.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-slate-50 text-slate-600">
                <tr>
                  <th className="px-4 py-3 text-left">Date</th>
                  <th className="px-4 py-3 text-left">Description</th>
                  <th className="px-4 py-3 text-right">Amount</th>
                  <th className="px-4 py-3 text-right">Balance</th>
                </tr>
              </thead>
              <tbody>
                {data.transactions.map((transaction) => (
                  <tr key={transaction.id} className="border-t border-slate-100">
                    <td className="whitespace-nowrap px-4 py-3 text-slate-500">{transaction.date}</td>
                    <td className="px-4 py-3">{transaction.description}</td>
                    <td className="whitespace-nowrap px-4 py-3 text-right">{formatMoney(transaction.amount)}</td>
                    <td className="whitespace-nowrap px-4 py-3 text-right text-slate-500">{formatMoney(transaction.balance)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
