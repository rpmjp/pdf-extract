import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api, type Transaction, type TransactionUpdate } from "../api";

function toFormState(transaction: Transaction): TransactionUpdate {
  return {
    date: transaction.date,
    description: transaction.description,
    amount: transaction.amount,
    type: transaction.type,
    balance: transaction.balance,
  };
}

function isDirty(form: TransactionUpdate, transaction: Transaction) {
  return (
    form.date !== transaction.date ||
    form.description !== transaction.description ||
    form.amount !== transaction.amount ||
    form.type !== transaction.type ||
    form.balance !== transaction.balance
  );
}

export default function EditableRow({ documentId, transaction }: { documentId: number; transaction: Transaction }) {
  const queryClient = useQueryClient();
  const [form, setForm] = useState<TransactionUpdate>(() => toFormState(transaction));

  const dirty = isDirty(form, transaction);
  const updateTransaction = useMutation({
    mutationFn: (payload: TransactionUpdate) =>
      api.patch<Transaction>(`/documents/${documentId}/transactions/${transaction.id!}`, payload).then((response) => response.data),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["document", String(documentId)] });
    },
  });

  return (
    <tr className="border-t border-slate-100">
      <td className="px-3 py-3">
        <input
          type="date"
          value={form.date}
          onChange={(event) => setForm((current) => ({ ...current, date: event.target.value }))}
          className="w-36 rounded border border-slate-300 px-2 py-1 text-sm"
        />
      </td>
      <td className="px-3 py-3">
        <input
          type="text"
          value={form.description}
          onChange={(event) => setForm((current) => ({ ...current, description: event.target.value }))}
          className="w-full min-w-56 rounded border border-slate-300 px-2 py-1 text-sm"
        />
      </td>
      <td className="px-3 py-3">
        <select
          value={form.type}
          onChange={(event) => setForm((current) => ({ ...current, type: event.target.value as Transaction["type"] }))}
          className="rounded border border-slate-300 px-2 py-1 text-sm"
        >
          <option value="deposit">deposit</option>
          <option value="withdrawal">withdrawal</option>
        </select>
      </td>
      <td className="px-3 py-3">
        <input
          type="number"
          step="0.01"
          value={form.amount}
          onChange={(event) => setForm((current) => ({ ...current, amount: Number(event.target.value) }))}
          className="w-28 rounded border border-slate-300 px-2 py-1 text-right text-sm tabular-nums"
        />
      </td>
      <td className="px-3 py-3">
        <input
          type="number"
          step="0.01"
          value={form.balance ?? ""}
          onChange={(event) =>
            setForm((current) => ({
              ...current,
              balance: event.target.value === "" ? null : Number(event.target.value),
            }))
          }
          className="w-28 rounded border border-slate-300 px-2 py-1 text-right text-sm tabular-nums"
        />
      </td>
      <td className="px-3 py-3 text-right">
        <button
          type="button"
          disabled={!dirty || updateTransaction.isPending}
          onClick={() => updateTransaction.mutate(form)}
          className="rounded bg-slate-900 px-3 py-1.5 text-sm font-medium text-white hover:bg-slate-700 disabled:cursor-not-allowed disabled:opacity-40"
        >
          {updateTransaction.isPending ? "Saving" : "Save"}
        </button>
      </td>
    </tr>
  );
}
