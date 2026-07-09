"use client";

export type LinePoint = { label: string; value: number };

/** Lightweight dependency-free SVG area/line chart for a time series. */
export default function LineChart({
  data,
  height = 160,
  emptyText = "No data for this range.",
}: {
  data: LinePoint[];
  height?: number;
  emptyText?: string;
}) {
  if (data.length === 0) {
    return <p className="px-1 py-6 text-center text-sm text-faint">{emptyText}</p>;
  }

  const W = 600;
  const H = height;
  const padX = 8;
  const padY = 12;
  const max = Math.max(...data.map((d) => d.value), 1);
  const n = data.length;
  const x = (i: number) => padX + (n === 1 ? (W - 2 * padX) / 2 : (i / (n - 1)) * (W - 2 * padX));
  const y = (v: number) => H - padY - (v / max) * (H - 2 * padY);

  const linePts = data.map((d, i) => `${x(i)},${y(d.value)}`).join(" ");
  const areaPts = `${padX},${H - padY} ${linePts} ${W - padX},${H - padY}`;

  // Show at most ~6 x-axis labels to avoid crowding.
  const step = Math.max(1, Math.ceil(n / 6));

  return (
    <div className="w-full overflow-hidden">
      <svg viewBox={`0 0 ${W} ${H + 18}`} className="w-full" preserveAspectRatio="none" role="img" aria-label="Trend chart">
        <defs>
          <linearGradient id="lc-fill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="var(--primary)" stopOpacity="0.25" />
            <stop offset="100%" stopColor="var(--primary)" stopOpacity="0" />
          </linearGradient>
        </defs>
        <polygon points={areaPts} fill="url(#lc-fill)" />
        <polyline points={linePts} fill="none" stroke="var(--primary)" strokeWidth="2" strokeLinejoin="round" strokeLinecap="round" />
        {data.map((d, i) => (
          <circle key={i} cx={x(i)} cy={y(d.value)} r={n > 30 ? 0 : 2.5} fill="var(--primary)" />
        ))}
        {data.map((d, i) =>
          i % step === 0 || i === n - 1 ? (
            <text key={`t-${i}`} x={x(i)} y={H + 12} textAnchor="middle" className="fill-[var(--faint)]" style={{ fontSize: 9 }}>
              {d.label}
            </text>
          ) : null
        )}
      </svg>
    </div>
  );
}
