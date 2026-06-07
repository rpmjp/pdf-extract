import type { DocStatus } from "../api";

const statusStyles: Record<string, string> = {
  verified: "bg-emerald-100 text-emerald-700 ring-emerald-200",
  approved: "bg-emerald-100 text-emerald-700 ring-emerald-200",
  needs_review: "bg-amber-100 text-amber-800 ring-amber-200",
  queued: "bg-sky-100 text-sky-700 ring-sky-200",
  parsing: "bg-sky-100 text-sky-700 ring-sky-200",
  failed: "bg-rose-100 text-rose-700 ring-rose-200",
  rejected: "bg-rose-100 text-rose-700 ring-rose-200",
  uploaded: "bg-slate-100 text-slate-700 ring-slate-200",
};

const priorityStyles: Record<string, string> = {
  P1: "bg-rose-100 text-rose-700 ring-rose-200",
  P2: "bg-amber-100 text-amber-800 ring-amber-200",
  P3: "bg-slate-100 text-slate-700 ring-slate-200",
};

const statusLabels: Record<string, string> = {
  needs_review: "Needs review",
  extracted_digital: "Extracted",
  "extracted:digital": "Extracted",
  "extracted:scanned": "Extracted",
};

export function formatStatus(status: string) {
  return statusLabels[status] || status.replace(/_/g, " ");
}

export default function StatusBadge({ status, pulse = false }: { status: DocStatus | string; pulse?: boolean }) {
  const cls = statusStyles[status] || "bg-slate-100 text-slate-700 ring-slate-200";
  const shouldPulse = pulse || status === "queued" || status === "parsing";
  return (
    <span className={`inline-flex items-center rounded px-2 py-0.5 text-xs font-medium capitalize ring-1 ring-inset ${cls}`}>
      {shouldPulse && <span className="mr-1.5 h-1.5 w-1.5 animate-pulse rounded-full bg-current" />}
      {formatStatus(status)}
    </span>
  );
}

export function PriorityBadge({ priority }: { priority?: "P1" | "P2" | "P3" | null }) {
  if (!priority) return <span className="text-slate-400">—</span>;
  return (
    <span className={`inline-flex items-center rounded px-2 py-0.5 text-xs font-medium ring-1 ring-inset ${priorityStyles[priority]}`}>
      {priority}
    </span>
  );
}
