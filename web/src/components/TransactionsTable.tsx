/**
 * Read-only transaction table for the document trust view.
 */

import type { Transaction } from "../api";
import ConfidenceMeter from "./ConfidenceMeter";

function formatAmount(transaction: Transaction) {
  /** Prefix deposits/withdrawals with reviewer-friendly signs. */

  const formatted = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" }).format(transaction.amount);
  return transaction.type === "deposit" ? `+${formatted}` : `−${formatted}`;
}

function formatBalance(value: number | null) {
  /** Render optional running balances as currency or an em dash. */

  if (value === null) return "—";
  return new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" }).format(value);
}

export default function TransactionsTable({ transactions }: { transactions: Transaction[] }) {
  /** Render extracted transactions with type, amount, and confidence styling. */

  return (
    <section className="rounded-lg border border-slate-200 bg-white">
      <div className="border-b border-slate-200 px-4 py-3">
        <h2 className="font-semibold text-slate-900">Transactions</h2>
      </div>
      {transactions.length === 0 ? (
        <p className="px-4 py-6 text-sm text-slate-500">No transactions parsed yet.</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="bg-slate-50 text-slate-600">
              <tr>
                <th className="px-4 py-3 text-left">Date</th>
                <th className="px-4 py-3 text-left">Description</th>
                <th className="px-4 py-3 text-left">Type</th>
                <th className="px-4 py-3 text-right">Amount</th>
                <th className="px-4 py-3 text-right">Balance</th>
                <th className="px-4 py-3 text-right">Confidence</th>
              </tr>
            </thead>
            <tbody>
              {transactions.map((transaction, index) => {
                const isDeposit = transaction.type === "deposit";
                return (
                  <tr key={transaction.id ?? index} className="border-t border-slate-100">
                    <td className="whitespace-nowrap px-4 py-3 text-slate-500">{transaction.date}</td>
                    <td className="px-4 py-3">{transaction.description}</td>
                    <td className="px-4 py-3">
                      <span
                        className={`rounded px-2 py-0.5 text-xs font-medium ${
                          isDeposit ? "bg-emerald-100 text-emerald-700" : "bg-rose-100 text-rose-700"
                        }`}
                      >
                        {transaction.type}
                      </span>
                    </td>
                    <td className={`whitespace-nowrap px-4 py-3 text-right tabular-nums ${isDeposit ? "text-emerald-700" : "text-rose-700"}`}>
                      {formatAmount(transaction)}
                    </td>
                    <td className="whitespace-nowrap px-4 py-3 text-right tabular-nums text-slate-500">
                      {formatBalance(transaction.balance)}
                    </td>
                    <td className="whitespace-nowrap px-4 py-3 text-right">
                      <ConfidenceMeter value={transaction.confidence} compact />
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
