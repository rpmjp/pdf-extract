import { useEffect, useMemo, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api, type ReviewItem, type Transaction, type TransactionUpdate } from "../api";
import EditableRow from "./EditableRow";

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

function issueForRow(items: ReviewItem[], index: number) {
  const rowToken = `transactions[${index + 1}]`;
  const zeroToken = `transactions[${index}]`;
  const reason = items.find((item) => item.reason.includes(rowToken) || item.reason.includes(zeroToken) || (item.reason.toLowerCase().includes("row") && item.reason.toLowerCase().includes("balance")));
  if (!reason) return undefined;
  return reason.reason.length > 90 ? `${reason.reason.slice(0, 90)}...` : reason.reason;
}

export default function EditableTransactionsTable({
  documentId,
  transactions,
  originalTransactions = [],
  reviewItems = [],
}: {
  documentId: number;
  transactions: Transaction[];
  originalTransactions?: Transaction[];
  reviewItems?: ReviewItem[];
}) {
  const queryClient = useQueryClient();
  const [drafts, setDrafts] = useState<Record<number, TransactionUpdate>>({});

  useEffect(() => {
    setDrafts(Object.fromEntries(transactions.map((transaction) => [transaction.id!, toFormState(transaction)])));
  }, [transactions]);

  const dirtyIds = useMemo(
    () => transactions.filter((transaction) => transaction.id && drafts[transaction.id] && isDirty(drafts[transaction.id], transaction)).map((transaction) => transaction.id!),
    [drafts, transactions],
  );

  const updateTransaction = useMutation({
    mutationFn: ({ transactionId, payload }: { transactionId: number; payload: TransactionUpdate }) =>
      api.patch<Transaction>(`/documents/${documentId}/transactions/${transactionId}`, payload).then((response) => response.data),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["document", String(documentId)] });
    },
  });

  const saveOne = (transactionId: number) => {
    const payload = drafts[transactionId];
    if (!payload) return;
    updateTransaction.mutate({ transactionId, payload });
  };

  const saveAll = async () => {
    for (const transactionId of dirtyIds) {
      const payload = drafts[transactionId];
      if (payload) await updateTransaction.mutateAsync({ transactionId, payload });
    }
    await queryClient.invalidateQueries({ queryKey: ["document", String(documentId)] });
  };

  const discardAll = () => {
    setDrafts(Object.fromEntries(transactions.map((transaction) => [transaction.id!, toFormState(transaction)])));
  };

  return (
    <section id="transactions" className="rounded-lg border border-slate-200 bg-white">
      <div className="flex items-start justify-between gap-4 border-b border-slate-200 px-4 py-3">
        <div>
          <h2 className="font-semibold text-slate-900">Edit transactions</h2>
          <p className="mt-1 text-xs text-slate-500">
            {dirtyIds.length ? `${dirtyIds.length} unsaved row${dirtyIds.length === 1 ? "" : "s"}` : "No unsaved changes"}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={discardAll}
            disabled={!dirtyIds.length || updateTransaction.isPending}
            className="rounded border border-slate-300 px-3 py-1.5 text-sm font-medium text-slate-700 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-40"
          >
            Discard all
          </button>
          <button
            type="button"
            onClick={saveAll}
            disabled={!dirtyIds.length || updateTransaction.isPending}
            className="rounded bg-slate-900 px-3 py-1.5 text-sm font-medium text-white hover:bg-slate-700 disabled:cursor-not-allowed disabled:opacity-40"
          >
            Save all
          </button>
        </div>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="bg-slate-50 text-slate-600">
            <tr>
              <th className="px-3 py-3 text-left">Date</th>
              <th className="px-3 py-3 text-left">Description</th>
              <th className="px-3 py-3 text-left">Type</th>
              <th className="px-3 py-3 text-right">Amount</th>
              <th className="px-3 py-3 text-right">Balance</th>
              <th className="px-3 py-3 text-right">Confidence</th>
              <th className="px-3 py-3 text-right">Action</th>
            </tr>
          </thead>
          <tbody>
            {transactions.map((transaction, index) => {
              if (!transaction.id || !drafts[transaction.id]) return null;
              return (
                <EditableRow
                  key={transaction.id}
                  transaction={transaction}
                  original={originalTransactions[index]}
                  form={drafts[transaction.id]}
                  issue={issueForRow(reviewItems, index)}
                  isSaving={updateTransaction.isPending}
                  onChange={(patch) => setDrafts((current) => ({ ...current, [transaction.id!]: { ...current[transaction.id!], ...patch } }))}
                  onSave={() => saveOne(transaction.id!)}
                />
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}
