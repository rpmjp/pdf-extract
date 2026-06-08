/**
 * Editable transaction row.
 *
 * The row highlights changed fields and shows the original extracted value
 * inline so reviewers can verify exactly what they are correcting.
 */

import type { Transaction, TransactionUpdate } from "../api";
import ConfidenceMeter from "./ConfidenceMeter";

const fields = ["date", "description", "type", "amount", "balance"] as const;

function formatValue(value: string | number | null | undefined) {
  /** Normalize blank values for the "Original" hint text. */

  if (value === null || typeof value === "undefined" || value === "") return "blank";
  return String(value);
}

function changedFields(form: TransactionUpdate, original?: Transaction) {
  /** Identify fields that differ from the original extraction snapshot. */

  if (!original) return new Set<string>();
  return new Set(
    fields.filter((field) => {
      const originalValue = field === "date" ? original.date : original[field];
      return form[field] !== originalValue;
    }),
  );
}

function OriginalValue({ show, value }: { show: boolean; value: string | number | null | undefined }) {
  /** Conditionally render the prior value under an edited input. */

  if (!show) return null;
  return <p className="mt-1 text-[11px] text-amber-700">Original: {formatValue(value)}</p>;
}

export default function EditableRow({
  form,
  original,
  transaction,
  issue,
  isSaving,
  onChange,
  onSave,
}: {
  form: TransactionUpdate;
  original?: Transaction;
  transaction: Transaction;
  issue?: string;
  isSaving: boolean;
  onChange: (patch: Partial<TransactionUpdate>) => void;
  onSave: () => void;
}) {
  const changed = changedFields(form, original || transaction);
  const dirty = changed.size > 0;

  return (
    <tr className={`border-t border-slate-100 ${dirty ? "bg-amber-50/50" : ""} ${issue ? "outline outline-1 outline-amber-200" : ""}`}>
      <td className="px-3 py-3 align-top">
        <input
          type="date"
          value={form.date}
          onChange={(event) => onChange({ date: event.target.value })}
          className={`w-36 rounded border px-2 py-1 text-sm ${changed.has("date") ? "border-amber-400 bg-white" : "border-slate-300"}`}
        />
        <OriginalValue show={changed.has("date")} value={original?.date ?? transaction.date} />
      </td>
      <td className="px-3 py-3 align-top">
        <input
          type="text"
          value={form.description}
          onChange={(event) => onChange({ description: event.target.value })}
          className={`w-full min-w-56 rounded border px-2 py-1 text-sm ${changed.has("description") ? "border-amber-400 bg-white" : "border-slate-300"}`}
        />
        <OriginalValue show={changed.has("description")} value={original?.description ?? transaction.description} />
        {issue && <p className="mt-1 text-[11px] font-medium text-amber-700">{issue}</p>}
      </td>
      <td className="px-3 py-3 align-top">
        <select
          value={form.type}
          onChange={(event) => onChange({ type: event.target.value as Transaction["type"] })}
          className={`rounded border px-2 py-1 text-sm ${changed.has("type") ? "border-amber-400 bg-white" : "border-slate-300"}`}
        >
          <option value="deposit">deposit</option>
          <option value="withdrawal">withdrawal</option>
        </select>
        <OriginalValue show={changed.has("type")} value={original?.type ?? transaction.type} />
      </td>
      <td className="px-3 py-3 align-top">
        <input
          type="number"
          step="0.01"
          value={form.amount}
          onChange={(event) => onChange({ amount: Number(event.target.value) })}
          className={`w-28 rounded border px-2 py-1 text-right text-sm tabular-nums ${changed.has("amount") ? "border-amber-400 bg-white" : "border-slate-300"}`}
        />
        <OriginalValue show={changed.has("amount")} value={original?.amount ?? transaction.amount} />
      </td>
      <td className="px-3 py-3 align-top">
        <input
          type="number"
          step="0.01"
          value={form.balance ?? ""}
          onChange={(event) => onChange({ balance: event.target.value === "" ? null : Number(event.target.value) })}
          className={`w-28 rounded border px-2 py-1 text-right text-sm tabular-nums ${changed.has("balance") ? "border-amber-400 bg-white" : "border-slate-300"}`}
        />
        <OriginalValue show={changed.has("balance")} value={original?.balance ?? transaction.balance} />
      </td>
      <td className="px-3 py-3 text-right align-top">
        <ConfidenceMeter value={transaction.confidence} compact />
      </td>
      <td className="px-3 py-3 text-right align-top">
        <button
          type="button"
          disabled={!dirty || isSaving}
          onClick={onSave}
          className="rounded bg-slate-900 px-3 py-1.5 text-sm font-medium text-white hover:bg-slate-700 disabled:cursor-not-allowed disabled:opacity-40"
        >
          {isSaving ? "Saving" : "Save"}
        </button>
      </td>
    </tr>
  );
}
