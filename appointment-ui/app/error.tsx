"use client";

export default function GlobalError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <div className="flex flex-1 flex-col items-center justify-center bg-bg p-6">
      <div className="empty-state">
        <div className="empty-icon">
          <span className="text-4xl">⚠️</span>
        </div>
        <div className="empty-title">Something went wrong</div>
        <div className="empty-desc">
          {error.message || "An unexpected error occurred."}
        </div>
        <button onClick={reset} className="btn-primary">
          Try again
        </button>
      </div>
    </div>
  );
}
