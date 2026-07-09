export default function Loading() {
  return (
    <div className="flex flex-1 flex-col overflow-hidden">
      <div
        className="flex h-16 shrink-0 items-center px-6"
        style={{
          borderBottom: "1px solid var(--border)",
          background: "color-mix(in srgb, var(--surface) 80%, transparent)",
        }}
      >
        <div className="skeleton-title h-5 w-36" />
      </div>

      <main className="flex-1 space-y-4 overflow-y-auto bg-bg p-6">
        {/* Neutral content placeholder — works for any route, not just the dashboard. */}
        <div className="skeleton h-10 w-full max-w-md rounded-xl" />
        <div className="space-y-3">
          {[...Array(6)].map((_, i) => (
            <div
              key={i}
              className="skeleton h-16 w-full rounded-xl"
              style={{ animationDelay: `${i * 60}ms` }}
            />
          ))}
        </div>
      </main>
    </div>
  );
}
