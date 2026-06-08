/**
 * Upload completion summary.
 *
 * Rendered after a parse job succeeds so the uploader can immediately inspect
 * status, reconciliation result, failed checks, and the document detail link.
 */

import { Link } from "react-router-dom";
import type { ParseResponse } from "../api";

function StatusBadge({ status }: { status: string }) {
  /** Local lightweight badge for parse-result status. */

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
  return <span className={`rounded px-2 py-0.5 text-xs font-medium ${cls}`}>{status}</span>;
}

export default function UploadResult({ result }: { result: ParseResponse }) {
  /** Summarize the parse response returned by the completed background job. */

  const transactionCount = result.extraction.transactions.length;
  const failedChecks = result.reconciliation.checks.filter((check) => !check.passed);

  return (
    <div className="rounded-lg border border-slate-200 bg-white p-6">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h2 className="text-lg font-semibold text-slate-900">Parse complete</h2>
          <p className="mt-1 text-sm text-slate-500">Document #{result.id} is ready.</p>
        </div>
        <StatusBadge status={result.status} />
      </div>

      <dl className="mt-6 grid gap-4 text-sm sm:grid-cols-2">
        <div className="rounded border border-slate-200 p-4">
          <dt className="text-slate-500">Transactions</dt>
          <dd className="mt-1 text-2xl font-semibold text-slate-900">{transactionCount}</dd>
        </div>
        <div className="rounded border border-slate-200 p-4">
          <dt className="text-slate-500">Reconciliation</dt>
          <dd className={`mt-1 text-2xl font-semibold ${result.reconciliation.passed ? "text-emerald-700" : "text-amber-700"}`}>
            {result.reconciliation.passed ? "Passed" : "Needs review"}
          </dd>
        </div>
      </dl>

      {failedChecks.length > 0 && (
        <div className="mt-5 rounded border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900">
          <p className="font-medium">Review needed</p>
          <ul className="mt-2 space-y-1">
            {failedChecks.map((check) => (
              <li key={check.name}>{check.detail}</li>
            ))}
          </ul>
        </div>
      )}

      <div className="mt-6 flex gap-3">
        <Link to={`/documents/${result.id}`} className="rounded bg-slate-900 px-4 py-2 text-sm font-medium text-white hover:bg-slate-700">
          View document
        </Link>
        <Link to="/upload" className="rounded border border-slate-300 px-4 py-2 text-sm font-medium text-slate-700 hover:bg-slate-100">
          Upload another
        </Link>
      </div>
    </div>
  );
}
