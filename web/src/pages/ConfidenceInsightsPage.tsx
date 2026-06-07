import { useQuery } from "@tanstack/react-query";
import { useNavigate, useSearchParams } from "react-router-dom";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Scatter,
  ScatterChart,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { api, type InsightsOverview, type KpiMetric, type LowestDoc, type ScatterPoint } from "../api";
import { formatConfidence } from "../components/ConfidenceMeter";
import StatusBadge, { PriorityBadge } from "../components/StatusBadge";

type Range = "7d" | "30d" | "90d" | "all";

function pct(v: number | null) {
  if (v === null || v === undefined) return "—";
  return `${Math.round(v * 100)}%`;
}

function deltaColor(delta: number | null, lowerIsBetter = false) {
  if (delta === null) return "text-slate-400";
  const positive = lowerIsBetter ? delta < 0 : delta > 0;
  return positive ? "text-emerald-600" : delta === 0 ? "text-slate-400" : "text-rose-600";
}

function deltaLabel(delta: number | null, isPercent = true) {
  if (delta === null) return null;
  const sign = delta > 0 ? "+" : "";
  return isPercent ? `${sign}${Math.round(delta * 100)}pp` : `${sign}${delta.toFixed(1)}`;
}

const STATUS_COLORS: Record<string, string> = {
  verified: "#10b981",
  approved: "#6366f1",
  needs_review: "#f59e0b",
  failed: "#ef4444",
  queued: "#94a3b8",
  parsing: "#94a3b8",
};

// ── KPI Card ──────────────────────────────────────────────────────────────────

interface KpiCardProps {
  label: string;
  kpi: KpiMetric;
  format?: (v: number) => string;
  lowerIsBetter?: boolean;
  tooltip?: string;
  onClick?: () => void;
}

function KpiCard({ label, kpi, format = pct, lowerIsBetter = false, tooltip, onClick }: KpiCardProps) {
  const valueStr = kpi.current !== null ? format(kpi.current) : "—";
  const color = deltaColor(kpi.delta, lowerIsBetter);
  const label_ = deltaLabel(kpi.delta, format === pct);

  return (
    <button
      type="button"
      onClick={onClick}
      title={tooltip}
      className={`rounded-lg border border-slate-200 bg-white p-5 text-left transition-shadow ${onClick ? "cursor-pointer hover:shadow-md" : "cursor-default"}`}
    >
      <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">{label}</p>
      <p className="mt-2 text-3xl font-bold text-slate-900">{valueStr}</p>
      {label_ !== null && (
        <p className={`mt-1 text-sm font-medium ${color}`}>
          {label_} vs prior period
        </p>
      )}
      {kpi.previous !== null && (
        <p className="mt-0.5 text-xs text-slate-400">Previous: {format(kpi.previous)}</p>
      )}
    </button>
  );
}

// ── Alert Card ────────────────────────────────────────────────────────────────

function AlertCard({ alert, onNavigate }: { alert: InsightsOverview["alerts"][0]; onNavigate: (url: string) => void }) {
  const isError = alert.severity === "error";
  return (
    <button
      type="button"
      onClick={() => onNavigate(alert.link)}
      className={`flex w-full items-start gap-3 rounded-lg border px-4 py-3 text-left text-sm transition-shadow hover:shadow-sm ${
        isError
          ? "border-rose-200 bg-rose-50 text-rose-800"
          : "border-amber-200 bg-amber-50 text-amber-800"
      }`}
    >
      <span className="mt-0.5 text-base">{isError ? "🔴" : "⚠️"}</span>
      <span>{alert.message}</span>
      <span className="ml-auto shrink-0 text-xs underline">View →</span>
    </button>
  );
}

// ── Chart Card ────────────────────────────────────────────────────────────────

function ChartCard({ title, children, empty, id }: { title: string; children: React.ReactNode; empty?: boolean; id?: string }) {
  return (
    <div id={id} className="rounded-lg border border-slate-200 bg-white p-5">
      <h2 className="mb-4 text-xs font-semibold uppercase tracking-wide text-slate-500">{title}</h2>
      {empty ? (
        <p className="py-10 text-center text-sm text-slate-400">Not enough data yet</p>
      ) : (
        children
      )}
    </div>
  );
}

// ── Scatter: Volume vs Confidence ─────────────────────────────────────────────

function VolumeScatter({ data, onDotClick }: { data: ScatterPoint[]; onDotClick: (id: number, status: string) => void }) {
  const plotData = data.map((d) => ({
    x: new Date(d.date).getTime(),
    y: d.confidence,
    id: d.document_id,
    filename: d.filename,
    status: d.status,
  }));

  return (
    <ChartCard id="chart-volume-confidence" title="Volume vs confidence" empty={data.length === 0}>
      {data.length > 0 && (
        <ResponsiveContainer width="100%" height={220}>
          <ScatterChart>
            <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
            <XAxis
              dataKey="x"
              type="number"
              scale="time"
              domain={["auto", "auto"]}
              tickFormatter={(ts) => new Date(ts).toLocaleDateString("en-US", { month: "short", day: "numeric" })}
              tick={{ fontSize: 10 }}
              axisLine={false}
              tickLine={false}
            />
            <YAxis
              dataKey="y"
              type="number"
              domain={[0, 1]}
              tickFormatter={pct}
              tick={{ fontSize: 11 }}
              axisLine={false}
              tickLine={false}
              width={36}
            />
            <Tooltip
              content={({ payload }) => {
                if (!payload?.length) return null;
                const d = payload[0].payload as (typeof plotData)[0];
                return (
                  <div className="rounded border border-slate-200 bg-white p-2 text-xs shadow">
                    <p className="font-medium">{d.filename}</p>
                    <p>Confidence: {pct(d.y)}</p>
                    <p>Status: {d.status}</p>
                  </div>
                );
              }}
            />
            <Scatter
              data={plotData}
              onClick={(p: unknown) => { const d = p as { id: number; status: string }; onDotClick(d.id, d.status); }}
              cursor="pointer"
              shape={(props: unknown) => {
                const p = props as { cx: number; cy: number; status: string };
                return <circle cx={p.cx} cy={p.cy} r={5} fill={STATUS_COLORS[p.status] ?? "#94a3b8"} fillOpacity={0.75} />;
              }}
            />
          </ScatterChart>
        </ResponsiveContainer>
      )}
    </ChartCard>
  );
}

// ── Pass Rate Trend ───────────────────────────────────────────────────────────

function PassRateTrend({ data }: { data: InsightsOverview["pass_rate_trend"] }) {
  return (
    <ChartCard id="chart-pass-rate-trend" title="Pass rate trend — target 95%" empty={data.length === 0}>
      {data.length > 0 && (
        <ResponsiveContainer width="100%" height={220}>
          <LineChart data={data}>
            <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" vertical={false} />
            <XAxis dataKey="date" tick={{ fontSize: 10 }} axisLine={false} tickLine={false} />
            <YAxis
              tick={{ fontSize: 11 }}
              axisLine={false}
              tickLine={false}
              domain={[0, 1]}
              tickFormatter={pct}
              width={36}
            />
            <ReferenceLine y={0.95} stroke="#ef4444" strokeDasharray="5 3" label={{ value: "95%", position: "right", fontSize: 10, fill: "#ef4444" }} />
            <Tooltip formatter={(v) => (typeof v === "number" ? pct(v) : String(v))} />
            <Line type="monotone" dataKey="rate" stroke="#6366f1" strokeWidth={2} dot={{ r: 3 }} activeDot={{ r: 5 }} />
          </LineChart>
        </ResponsiveContainer>
      )}
    </ChartCard>
  );
}

// ── Failure Breakdown ─────────────────────────────────────────────────────────

function FailureBreakdown({
  data,
  onBarClick,
}: {
  data: InsightsOverview["failure_breakdown"];
  onBarClick: (link: string) => void;
}) {
  return (
    <ChartCard title="Failure categories — what to fix next" empty={data.length === 0}>
      {data.length > 0 && (
        <ResponsiveContainer width="100%" height={Math.max(180, data.length * 36)}>
          <BarChart data={data} layout="vertical" barSize={18}>
            <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" horizontal={false} />
            <XAxis type="number" tick={{ fontSize: 11 }} axisLine={false} tickLine={false} allowDecimals={false} />
            <YAxis type="category" dataKey="category" tick={{ fontSize: 11 }} axisLine={false} tickLine={false} width={130} />
            <Tooltip cursor={{ fill: "#f1f5f9" }} />
            <Bar dataKey="count" fill="#f59e0b" radius={[0, 4, 4, 0]} cursor="pointer"
              onClick={(d: unknown) => onBarClick((d as { link: string }).link)} />
          </BarChart>
        </ResponsiveContainer>
      )}
    </ChartCard>
  );
}

// ── By Source Table ───────────────────────────────────────────────────────────

function BySourceTable({ data }: { data: InsightsOverview["by_source"] }) {
  return (
    <ChartCard id="chart-by-source" title="Performance by source" empty={data.length === 0}>
      {data.length > 0 && (
        <table className="w-full text-sm">
          <thead className="text-xs text-slate-500">
            <tr>
              <th className="pb-2 text-left">Source</th>
              <th className="pb-2 text-right">Docs</th>
              <th className="pb-2 text-right">Mean conf</th>
              <th className="pb-2 text-right">Pass rate</th>
            </tr>
          </thead>
          <tbody>
            {data.map((row) => (
              <tr key={row.source} className="border-t border-slate-100">
                <td className="py-2 font-medium capitalize">{row.source}</td>
                <td className="py-2 text-right text-slate-600">{row.count}</td>
                <td className="py-2 text-right">{formatConfidence(row.mean_confidence)}</td>
                <td className={`py-2 text-right font-medium ${row.pass_rate !== null && row.pass_rate >= 0.95 ? "text-emerald-600" : "text-rose-600"}`}>
                  {pct(row.pass_rate)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </ChartCard>
  );
}

// ── Small metric line charts ──────────────────────────────────────────────────

function MiniLineChart({
  title,
  data,
  dataKeys,
  colors,
  empty,
  tickFormatter,
}: {
  title: string;
  data: object[];
  dataKeys: string[];
  colors: string[];
  empty: boolean;
  tickFormatter?: (v: number) => string;
}) {
  return (
    <div className="rounded-lg border border-slate-200 bg-white p-4">
      <h3 className="mb-3 text-xs font-semibold uppercase tracking-wide text-slate-500">{title}</h3>
      {empty ? (
        <p className="py-6 text-center text-xs text-slate-400">Not enough data yet</p>
      ) : (
        <ResponsiveContainer width="100%" height={110}>
          <LineChart data={data}>
            <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" vertical={false} />
            <XAxis dataKey="date" hide />
            <YAxis tick={{ fontSize: 10 }} axisLine={false} tickLine={false} width={30} tickFormatter={tickFormatter} />
            <Tooltip formatter={(v) => (typeof v === "number" && tickFormatter ? tickFormatter(v) : String(v))} />
            {dataKeys.map((k, i) => (
              <Line key={k} type="monotone" dataKey={k} stroke={colors[i]} strokeWidth={1.5} dot={false} />
            ))}
          </LineChart>
        </ResponsiveContainer>
      )}
    </div>
  );
}

// ── Lowest Table ──────────────────────────────────────────────────────────────

function LowestTable({ data, onRowClick }: { data: LowestDoc[]; onRowClick: (doc: LowestDoc) => void }) {
  return (
    <div id="chart-lowest-table" className="overflow-hidden rounded-lg border border-slate-200 bg-white">
      <div className="border-b border-slate-100 px-5 py-4">
        <h2 className="text-xs font-semibold uppercase tracking-wide text-slate-500">Lowest confidence documents</h2>
      </div>
      <table className="w-full text-sm">
        <thead className="bg-slate-50 text-slate-600">
          <tr>
            <th className="px-4 py-3 text-left">Document</th>
            <th className="px-4 py-3 text-left">Priority</th>
            <th className="px-4 py-3 text-left">Status</th>
            <th className="px-4 py-3 text-right">Confidence</th>
            <th className="px-4 py-3 text-right">Time in status</th>
            <th className="px-4 py-3 text-right">Last actor</th>
          </tr>
        </thead>
        <tbody>
          {data.length === 0 && (
            <tr>
              <td colSpan={6} className="px-4 py-8 text-center text-slate-500">No documents with confidence scores yet.</td>
            </tr>
          )}
          {data.map((doc) => (
            <tr
              key={doc.id}
              tabIndex={0}
              onClick={() => onRowClick(doc)}
              onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onRowClick(doc); } }}
              className="cursor-pointer border-t border-slate-100 hover:bg-slate-50 focus:bg-slate-50 focus:outline-none focus:ring-2 focus:ring-inset focus:ring-slate-300"
            >
              <td className="px-4 py-3">
                <p className="font-medium text-slate-900">{doc.filename}</p>
                <p className="mt-0.5 text-xs text-slate-500">#{doc.id}</p>
              </td>
              <td className="px-4 py-3"><PriorityBadge priority={doc.priority} /></td>
              <td className="px-4 py-3"><StatusBadge status={doc.status} /></td>
              <td className="px-4 py-3 text-right font-medium text-rose-600">{formatConfidence(doc.confidence_score)}</td>
              <td className="px-4 py-3 text-right text-slate-500">
                {doc.time_in_status_hours < 1
                  ? "<1h"
                  : doc.time_in_status_hours < 24
                  ? `${doc.time_in_status_hours.toFixed(0)}h`
                  : `${(doc.time_in_status_hours / 24).toFixed(1)}d`}
              </td>
              <td className="px-4 py-3 text-right text-slate-500">{doc.last_modified_by ?? "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ── Main Page ─────────────────────────────────────────────────────────────────

function scrollToSection(id: string) {
  document.getElementById(id)?.scrollIntoView({ behavior: "smooth", block: "start" });
}

export default function ConfidenceInsightsPage() {
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const range = (searchParams.get("range") ?? "30d") as Range;

  const { data, isLoading, error } = useQuery({
    queryKey: ["insights-overview", range],
    queryFn: async () => {
      const response = await api.get<InsightsOverview>(`/insights/overview?range=${range}`);
      return response.data;
    },
  });

  function setRange(r: Range) {
    setSearchParams({ range: r });
  }

  function docHref(id: number, status: string) {
    return status === "needs_review" || status === "failed" ? `/documents/${id}/review` : `/documents/${id}`;
  }

  if (isLoading) return <p className="text-slate-500">Loading…</p>;
  if (error) return <p className="text-rose-600">Failed to load insights.</p>;
  if (!data) return null;

  const ranges: Range[] = ["7d", "30d", "90d", "all"];
  const rangeLabel: Record<Range, string> = { "7d": "7d", "30d": "30d", "90d": "90d", all: "All" };

  return (
    <div>
      {/* Header */}
      <div className="mb-6 flex items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold text-slate-900">Confidence Insights</h1>
          <p className="mt-1 text-sm text-slate-500">Operational quality dashboard — is the system getting better or worse?</p>
        </div>
        <div className="flex rounded-lg border border-slate-200 bg-white p-1">
          {ranges.map((r) => (
            <button
              key={r}
              type="button"
              onClick={() => setRange(r)}
              className={`rounded px-3 py-1.5 text-sm font-medium transition-colors ${
                range === r ? "bg-slate-900 text-white" : "text-slate-600 hover:text-slate-900"
              }`}
            >
              {rangeLabel[r]}
            </button>
          ))}
        </div>
      </div>

      {/* KPI Strip */}
      <div className="mb-6 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <KpiCard
          label="Pass rate"
          kpi={data.kpis.pass_rate}
          tooltip="% of processed docs that reached verified or approved. Click to see the trend."
          onClick={() => scrollToSection("chart-pass-rate-trend")}
        />
        <KpiCard
          label="Mean confidence"
          kpi={data.kpis.mean_confidence}
          tooltip="Average extraction confidence. Click to see how confidence is distributed over time."
          onClick={() => scrollToSection("chart-volume-confidence")}
        />
        <KpiCard
          label="Median review turnaround"
          kpi={data.kpis.median_review_time_hours}
          format={(v) => v < 1 ? "<1h" : v < 24 ? `${v.toFixed(1)}h` : `${(v / 24).toFixed(1)}d`}
          tooltip="Median time from needs_review to approved. Click to see the longest-waiting docs."
          lowerIsBetter
          onClick={() => scrollToSection("chart-lowest-table")}
        />
        <KpiCard
          label="Auto-approval rate"
          kpi={data.kpis.auto_approval_rate}
          tooltip="Docs verified by the system without edits. Click to see breakdown by source."
          onClick={() => scrollToSection("chart-by-source")}
        />
      </div>

      {/* Alerts */}
      {data.alerts.length > 0 && (
        <div className="mb-6 flex flex-col gap-2">
          {data.alerts.map((alert, i) => (
            <AlertCard key={i} alert={alert} onNavigate={(link) => navigate(link)} />
          ))}
        </div>
      )}

      {/* 2×2 Chart Grid */}
      <div className="mb-6 grid gap-5 lg:grid-cols-2">
        <VolumeScatter
          data={data.volume_vs_confidence}
          onDotClick={(id, status) => navigate(docHref(id, status))}
        />
        <PassRateTrend data={data.pass_rate_trend} />
        <FailureBreakdown
          data={data.failure_breakdown}
          onBarClick={(link) => navigate(link)}
        />
        <BySourceTable data={data.by_source} />
      </div>

      {/* Operational Metrics Row */}
      {(data.queue_depth.length > 0 || data.parse_latency.length > 0 || data.failure_rate.length > 0) && (
        <div className="mb-6 grid gap-4 lg:grid-cols-3">
          <MiniLineChart
            title="Jobs queued / day"
            data={data.queue_depth}
            dataKeys={["depth"]}
            colors={["#6366f1"]}
            empty={data.queue_depth.length === 0}
          />
          <MiniLineChart
            title="Parse latency (s) — p50 / p95 / p99"
            data={data.parse_latency}
            dataKeys={["p50", "p95", "p99"]}
            colors={["#10b981", "#f59e0b", "#ef4444"]}
            empty={data.parse_latency.length === 0}
          />
          <MiniLineChart
            title="Failure rate / day"
            data={data.failure_rate}
            dataKeys={["rate"]}
            colors={["#ef4444"]}
            empty={data.failure_rate.length === 0}
            tickFormatter={pct}
          />
        </div>
      )}

      {/* Lowest Confidence Table */}
      <LowestTable
        data={data.lowest}
        onRowClick={(doc) => navigate(docHref(doc.id, doc.status))}
      />
    </div>
  );
}
