/**
 * Admin failure-analysis page.
 *
 * Correction examples are grouped by deterministic failure category so admins
 * can decide which rules, prompts, or eval cases deserve attention next.
 */

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api, type FailureCategorySummary, type FailuresResponse } from "../api";

function formatValue(value: unknown) {
  /** Render arbitrary diff values safely in a compact table row. */

  if (value === null || typeof value === "undefined") return "blank";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

function Trend({ points }: { points: FailureCategorySummary["trend"] }) {
  /** Tiny weekly bar chart for one failure category. */

  const max = Math.max(1, ...points.map((point) => point.count));
  return (
    <div className="flex h-10 items-end gap-1" aria-label="Weekly trend">
      {points.map((point) => (
        <div key={point.week} title={`${point.week}: ${point.count}`} className="w-3 rounded-t bg-slate-300" style={{ height: `${Math.max(10, (point.count / max) * 40)}px` }} />
      ))}
    </div>
  );
}

function CategoryRow({ category }: { category: FailureCategorySummary }) {
  /** Expandable row showing sample document diffs for a category. */

  const [open, setOpen] = useState(false);
  return (
    <>
      <tr className="border-t border-slate-100">
        <td className="px-4 py-3">
          <button type="button" onClick={() => setOpen((value) => !value)} className="font-medium text-slate-900 hover:underline">
            {category.category}
          </button>
        </td>
        <td className="px-4 py-3 text-right tabular-nums">{category.count}</td>
        <td className="px-4 py-3"><Trend points={category.trend} /></td>
      </tr>
      {open && (
        <tr className="border-t border-slate-100 bg-slate-50">
          <td colSpan={3} className="px-4 py-4">
            <div className="space-y-3">
              {category.samples.map((sample) => (
                <div key={sample.document_id} className="rounded border border-slate-200 bg-white p-3">
                  <Link to={`/documents/${sample.document_id}`} className="text-sm font-medium text-slate-900 underline">
                    #{sample.document_id} {sample.filename}
                  </Link>
                  <div className="mt-2 space-y-1 text-xs text-slate-600">
                    {sample.diffs.length ? sample.diffs.map((diff) => (
                      <p key={`${sample.document_id}-${diff.path}`}>
                        <span className="font-medium">{diff.path}</span>: {formatValue(diff.before)} → {formatValue(diff.after)}
                      </p>
                    )) : <p>No field changes recorded.</p>}
                  </div>
                </div>
              ))}
            </div>
          </td>
        </tr>
      )}
    </>
  );
}

export default function AdminFailuresPage() {
  /** Fetch and render the default 30-day failure window. */

  const { data, isLoading, error } = useQuery({
    queryKey: ["admin-failures", "30d"],
    queryFn: async () => (await api.get<FailuresResponse>("/admin/failures?since=30d")).data,
  });

  if (isLoading) return <p className="text-slate-500">Loading failure data...</p>;
  if (error) return <p className="text-rose-600">Failed to load failure data.</p>;

  return (
    <div>
      <div className="mb-6">
        <h1 className="text-2xl font-semibold text-slate-900">Failure analysis</h1>
        <p className="mt-1 text-sm text-slate-500">Correction categories from the last {data?.since || "30d"}.</p>
      </div>
      <div className="overflow-hidden rounded-lg border border-slate-200 bg-white">
        <table className="w-full text-sm">
          <thead className="bg-slate-50 text-slate-600">
            <tr>
              <th className="px-4 py-3 text-left">Category</th>
              <th className="px-4 py-3 text-right">Count</th>
              <th className="px-4 py-3 text-left">Trend</th>
            </tr>
          </thead>
          <tbody>
            {data?.categories.map((category) => <CategoryRow key={category.category} category={category} />)}
            {!data?.categories.length && (
              <tr>
                <td colSpan={3} className="px-4 py-8 text-center text-slate-500">No correction examples found for this window.</td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
