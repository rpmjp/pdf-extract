import type { Transaction } from "../api";
import EditableRow from "./EditableRow";

export default function EditableTransactionsTable({ documentId, transactions }: { documentId: number; transactions: Transaction[] }) {
  return (
    <section className="rounded-lg border border-slate-200 bg-white">
      <div className="border-b border-slate-200 px-4 py-3">
        <h2 className="font-semibold text-slate-900">Edit transactions</h2>
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
              <th className="px-3 py-3 text-right">Action</th>
            </tr>
          </thead>
          <tbody>
            {transactions.map((transaction) => (
              <EditableRow key={transaction.id} documentId={documentId} transaction={transaction} />
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
