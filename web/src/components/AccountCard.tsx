import type { DocumentDetail } from "../api";

function formatMoney(value: number | null) {
  if (value === null) return "—";
  return new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" }).format(value);
}

function Field({ label, value }: { label: string; value: string | number | null }) {
  return (
    <div>
      <dt className="text-xs font-medium uppercase tracking-wide text-slate-500">{label}</dt>
      <dd className="mt-1 text-sm font-medium text-slate-900">{value ?? "—"}</dd>
    </div>
  );
}

export default function AccountCard({ document }: { document: DocumentDetail }) {
  return (
    <section className="rounded-lg border border-slate-200 bg-white p-5">
      <h2 className="text-base font-semibold text-slate-900">Account info</h2>
      <dl className="mt-4 grid gap-4 sm:grid-cols-2">
        <Field label="Holder" value={document.account_holder} />
        <Field label="Account" value={document.account_number} />
        <Field label="Period" value={document.statement_period} />
        <Field label="Opening" value={formatMoney(document.opening_balance)} />
        <Field label="Closing" value={formatMoney(document.closing_balance)} />
      </dl>
    </section>
  );
}
