"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { Search, Calendar, X } from "lucide-react";

export default function AppointmentFilters({ count }: { count?: number }) {
  const router = useRouter();
  const params = useSearchParams();

  const urlQ = params.get("q") ?? "";
  const [q, setQ] = useState(urlQ);

  const update = useCallback(
    (key: string, value: string) => {
      const next = new URLSearchParams(params.toString());
      if (value) next.set(key, value);
      else next.delete(key);
      const qs = next.toString();
      router.replace(qs ? `/appointments?${qs}` : "/appointments", { scroll: false });
    },
    [params, router]
  );

  // Keep the search box in sync with back/forward navigation.
  useEffect(() => { setQ(urlQ); }, [urlQ]);

  // Debounce search so we navigate once the user pauses, not per keystroke.
  useEffect(() => {
    if (q === urlQ) return;
    const t = setTimeout(() => update("q", q), 350);
    return () => clearTimeout(t);
  }, [q, urlQ, update]);

  const dateFrom = params.get("date_from") ?? "";
  const dateTo = params.get("date_to") ?? "";
  const status = params.get("status") ?? "all";
  const hasFilters = Boolean(dateFrom || dateTo || q || (status && status !== "all"));

  function clearAll() {
    setQ("");
    router.replace("/appointments", { scroll: false });
  }

  return (
    <div className="card p-4 animate-fade-in-up">
      <div className="flex flex-wrap items-end gap-3">
        <label className="flex flex-col gap-1.5">
          <span className="flex items-center gap-1.5 text-xs font-medium text-muted">
            <Calendar size={11} className="text-primary" /> From
          </span>
          <input
            type="date"
            value={dateFrom}
            onChange={(e) => update("date_from", e.target.value)}
            className="input w-40"
          />
        </label>

        <label className="flex flex-col gap-1.5">
          <span className="flex items-center gap-1.5 text-xs font-medium text-muted">
            <Calendar size={11} className="text-primary" /> To
          </span>
          <input
            type="date"
            value={dateTo}
            onChange={(e) => update("date_to", e.target.value)}
            className="input w-40"
          />
        </label>

        <label className="flex flex-col gap-1.5">
          <span className="text-xs font-medium text-muted">Status</span>
          <select
            value={status}
            onChange={(e) => update("status", e.target.value)}
            className="select w-36"
          >
            <option value="all">All statuses</option>
            <option value="confirmed">Confirmed</option>
            <option value="checked_in">Checked in</option>
            <option value="completed">Completed</option>
            <option value="no_show">No-show</option>
            <option value="cancelled">Cancelled</option>
          </select>
        </label>

        <label className="flex flex-1 flex-col gap-1.5" style={{ minWidth: "180px" }}>
          <span className="flex items-center gap-1.5 text-xs font-medium text-muted">
            <Search size={11} className="text-primary" /> Search
          </span>
          <input
            type="text"
            placeholder="Patient name or 017…"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            className="input"
          />
        </label>

        {hasFilters && (
          <button onClick={clearAll} className="btn-ghost btn-sm mb-0.5">
            <X size={13} /> Clear
          </button>
        )}
      </div>

      {count !== undefined && (
        <p className="mt-3 text-xs text-faint">
          {count} appointment{count === 1 ? "" : "s"}
          {hasFilters ? " match your filters" : ""}
        </p>
      )}
    </div>
  );
}
