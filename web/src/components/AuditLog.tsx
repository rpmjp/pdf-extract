import type { AuditEntry } from "../api";

function describe(entry: AuditEntry) {
  if (entry.action === "edit_txn") return `edited transaction #${String(entry.details.transaction_id ?? "")}`;
  if (entry.action === "approve") return "approved the document";
  if (entry.action === "reject") return "rejected the document";
  return entry.action;
}

export default function AuditLog({ entries }: { entries: AuditEntry[] }) {
  return (
    <section className="rounded-lg border border-slate-200 bg-white p-5">
      <h2 className="text-base font-semibold text-slate-900">Activity</h2>
      {entries.length === 0 ? (
        <p className="mt-3 text-sm text-slate-500">No review activity yet.</p>
      ) : (
        <ol className="mt-4 space-y-3 text-sm">
          {entries.map((entry) => (
            <li key={entry.id} className="border-l-2 border-slate-200 pl-3">
              <p className="font-medium text-slate-900">
                {entry.actor} {describe(entry)}
              </p>
              <p className="mt-0.5 text-xs text-slate-500">{new Date(entry.created_at).toLocaleString()}</p>
              {entry.action === "reject" && typeof entry.details.reason === "string" && (
                <p className="mt-1 text-slate-600">{entry.details.reason}</p>
              )}
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}
