function confidenceTone(value?: number | null) {
  if (typeof value !== "number") return { label: "Unknown", bar: "bg-slate-300", text: "text-slate-500", bg: "bg-slate-100" };
  if (value >= 0.9) return { label: "High", bar: "bg-emerald-500", text: "text-emerald-700", bg: "bg-emerald-50" };
  if (value >= 0.75) return { label: "Medium", bar: "bg-amber-500", text: "text-amber-700", bg: "bg-amber-50" };
  return { label: "Low", bar: "bg-rose-500", text: "text-rose-700", bg: "bg-rose-50" };
}

export function formatConfidence(value?: number | null) {
  return typeof value === "number" ? `${Math.round(value * 100)}%` : "—";
}

export default function ConfidenceMeter({ value, compact = false }: { value?: number | null; compact?: boolean }) {
  const tone = confidenceTone(value);
  const width = typeof value === "number" ? `${Math.max(4, Math.min(100, Math.round(value * 100)))}%` : "0%";

  if (compact) {
    return (
      <span className={`inline-flex items-center rounded px-2 py-0.5 text-xs font-medium ${tone.bg} ${tone.text}`}>
        {formatConfidence(value)}
      </span>
    );
  }

  return (
    <div>
      <div className="flex items-center justify-between gap-3">
        <span className="text-xs font-medium uppercase tracking-wide text-slate-500">Confidence</span>
        <span className={`text-sm font-semibold ${tone.text}`}>
          {tone.label} · {formatConfidence(value)}
        </span>
      </div>
      <div className="mt-2 h-2 overflow-hidden rounded-full bg-slate-100">
        <div className={`h-full rounded-full ${tone.bar}`} style={{ width }} />
      </div>
    </div>
  );
}
