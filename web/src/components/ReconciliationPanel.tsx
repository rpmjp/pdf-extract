/**
 * Reconciliation summary panel.
 *
 * This is the deterministic accounting check that gives reviewers confidence
 * that extracted rows reconcile against statement balances.
 */

import type { ParseResponse } from "../api";

function formatMoney(value: number) {
  /** Format reconciliation totals in the app's current USD-only display. */

  return new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" }).format(value);
}

export default function ReconciliationPanel({ reconciliation }: { reconciliation: ParseResponse["reconciliation"] }) {
  /** Render totals and failed check reasons for the document trust view. */

  const failedChecks = reconciliation.checks.filter((check) => !check.passed);

  return (
    <section className="rounded-lg border border-slate-200 bg-white p-5">
      <div className="flex items-center justify-between gap-4">
        <h2 className="text-base font-semibold text-slate-900">Reconciliation</h2>
        <span
          className={`rounded px-2 py-0.5 text-xs font-medium ${
            reconciliation.passed ? "bg-emerald-100 text-emerald-700" : "bg-amber-100 text-amber-700"
          }`}
        >
          {reconciliation.passed ? "Passed" : "Needs review"}
        </span>
      </div>

      <dl className="mt-4 grid gap-3 sm:grid-cols-3">
        <div className="rounded border border-slate-200 p-3">
          <dt className="text-xs text-slate-500">Deposits</dt>
          <dd className="mt-1 font-semibold text-emerald-700">{formatMoney(reconciliation.deposits_total)}</dd>
        </div>
        <div className="rounded border border-slate-200 p-3">
          <dt className="text-xs text-slate-500">Withdrawals</dt>
          <dd className="mt-1 font-semibold text-rose-700">{formatMoney(reconciliation.withdrawals_total)}</dd>
        </div>
        <div className="rounded border border-slate-200 p-3">
          <dt className="text-xs text-slate-500">Corrections</dt>
          <dd className="mt-1 font-semibold text-slate-900">{reconciliation.sign_corrections}</dd>
        </div>
      </dl>

      {failedChecks.length > 0 && (
        <div className="mt-4 rounded border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900">
          <p className="font-medium">Failure reasons</p>
          <ul className="mt-2 space-y-1">
            {failedChecks.map((check) => (
              <li key={check.name}>{check.detail}</li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}
