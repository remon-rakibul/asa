/** Generic shimmer placeholder for table-style pages, matching `.table-wrap`. */
export default function TableSkeleton({
  rows = 6,
  cols = 4,
}: {
  rows?: number;
  cols?: number;
}) {
  return (
    <div className="table-wrap animate-fade-in">
      <div className="border-b border-border bg-surface-2 px-5 py-3.5">
        <div className="skeleton h-3 w-32" />
      </div>
      <div>
        {Array.from({ length: rows }).map((_, i) => (
          <div
            key={i}
            className="flex items-center gap-4 border-b border-border px-5 py-4 last:border-b-0"
            style={{ animationDelay: `${i * 60}ms` }}
          >
            <div className="skeleton-avatar h-9 w-9 shrink-0" />
            {Array.from({ length: cols }).map((_, j) => (
              <div
                key={j}
                className="skeleton h-4"
                style={{ width: `${[28, 18, 22, 14, 20][j % 5]}%` }}
              />
            ))}
          </div>
        ))}
      </div>
    </div>
  );
}
