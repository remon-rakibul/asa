"use client";

export type BarDatum = { label: string; value: number; color?: string };

/** Lightweight dependency-free horizontal bar chart (CSS widths). */
export default function BarChart({
  data,
  emptyText = "No data for this range.",
}: {
  data: BarDatum[];
  emptyText?: string;
}) {
  if (data.length === 0) {
    return <p className="px-1 py-6 text-center text-sm text-faint">{emptyText}</p>;
  }
  const max = Math.max(...data.map((d) => d.value), 1);
  return (
    <div className="space-y-2.5">
      {data.map((d, i) => (
        <div key={`${d.label}-${i}`} className="flex items-center gap-3">
          <div className="w-28 shrink-0 truncate text-xs text-muted" dir="auto" title={d.label}>
            {d.label}
          </div>
          <div className="relative h-6 flex-1 overflow-hidden rounded-md bg-surface-2">
            <div
              className="absolute inset-y-0 left-0 rounded-md transition-all duration-500"
              style={{
                width: `${Math.max((d.value / max) * 100, d.value > 0 ? 4 : 0)}%`,
                background: d.color ?? "var(--brand-grad)",
              }}
            />
          </div>
          <div className="w-10 shrink-0 text-right text-xs font-semibold tabular-nums text-fg">
            {d.value}
          </div>
        </div>
      ))}
    </div>
  );
}
