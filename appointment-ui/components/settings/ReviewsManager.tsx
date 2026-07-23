"use client";

// Patient-review moderation for this clinic's doctors. Hidden reviews stay
// out of the portal and out of the doctor's average; the review itself is
// never deleted (the patient can still see and edit their own copy).

import { useCallback, useEffect, useState } from "react";
import { Eye, EyeOff, Star } from "lucide-react";
import {
  AdminReview,
  listClinicReviews,
  setReviewStatus,
} from "@/lib/api";
import { useToast } from "@/lib/toast";

type Filter = "" | "published" | "hidden";

const FILTERS: { value: Filter; label: string }[] = [
  { value: "", label: "All" },
  { value: "published", label: "Published" },
  { value: "hidden", label: "Hidden" },
];

function Stars({ rating }: { rating: number }) {
  return (
    <span className="inline-flex items-center gap-0.5">
      {[1, 2, 3, 4, 5].map((n) => (
        <Star
          key={n}
          size={12}
          className={n <= rating ? "fill-amber-400 text-amber-400" : "text-border"}
        />
      ))}
    </span>
  );
}

export default function ReviewsManager() {
  const toast = useToast();
  const [filter, setFilter] = useState<Filter>("");
  const [reviews, setReviews] = useState<AdminReview[] | null>(null);
  const [busyId, setBusyId] = useState<number | null>(null);

  const load = useCallback(async (f: Filter) => {
    try {
      setReviews(await listClinicReviews(f || undefined));
    } catch {
      setReviews([]);
    }
  }, []);

  useEffect(() => { load(filter); }, [load, filter]);

  async function toggle(r: AdminReview) {
    const next = r.status === "published" ? "hidden" : "published";
    setBusyId(r.id);
    try {
      await setReviewStatus(r.id, next);
      await load(filter);
      toast.success(next === "hidden" ? "Review hidden" : "Review published");
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Could not update review");
    } finally {
      setBusyId(null);
    }
  }

  return (
    <div className="space-y-4">
      {/* Filter chips */}
      <div className="flex items-center gap-2">
        {FILTERS.map((f) => (
          <button
            key={f.value}
            onClick={() => setFilter(f.value)}
            className={`rounded-full px-3.5 py-1.5 text-xs font-semibold transition ${
              filter === f.value
                ? "text-white shadow"
                : "border border-border bg-surface text-muted hover:text-fg"
            }`}
            style={filter === f.value ? { background: "var(--brand-grad)" } : undefined}
          >
            {f.label}
          </button>
        ))}
      </div>

      {reviews === null ? (
        <div className="space-y-3">
          {[0, 1, 2].map((i) => (
            <div key={i} className="skeleton h-24 rounded-2xl" style={{ animationDelay: `${i * 80}ms` }} />
          ))}
        </div>
      ) : reviews.length === 0 ? (
        <div className="card flex flex-col items-center justify-center py-14 text-center animate-fade-in">
          <div
            className="mb-3 flex h-14 w-14 items-center justify-center rounded-2xl text-faint"
            style={{ background: "var(--surface-2)" }}
          >
            <Star size={24} />
          </div>
          <p className="text-sm font-medium text-muted">No reviews yet</p>
          <p className="mt-1 text-xs text-faint">
            Patients can review a doctor after a completed appointment.
          </p>
        </div>
      ) : (
        <div className="space-y-3">
          {reviews.map((r, i) => (
            <div
              key={r.id}
              className="card p-4 animate-fade-in-up"
              style={{ animationDelay: `${Math.min(i, 8) * 50}ms`, opacity: r.status === "hidden" ? 0.7 : 1 }}
            >
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <Stars rating={r.rating} />
                    <span className="text-sm font-bold text-fg">{r.reviewer_name || "Patient"}</span>
                    <span className="text-xs text-faint">→ {r.doctor_name}</span>
                    <span
                      className={`badge ${r.status === "published" ? "badge-success" : "badge-warning"}`}
                    >
                      {r.status}
                    </span>
                  </div>
                  {r.text && (
                    <p className="mt-2 text-sm leading-relaxed text-muted" dir="auto">{r.text}</p>
                  )}
                  <p className="mt-2 text-[11px] text-faint">
                    {new Date(r.created_at).toLocaleDateString("en-GB", { day: "2-digit", month: "short", year: "numeric" })}
                    {r.updated_at !== r.created_at ? " · edited" : ""}
                  </p>
                </div>
                <button
                  onClick={() => toggle(r)}
                  disabled={busyId === r.id}
                  className="btn-ghost btn-sm shrink-0"
                  style={{ border: "1px solid var(--border)" }}
                  title={r.status === "published" ? "Hide from the portal" : "Publish to the portal"}
                >
                  {r.status === "published"
                    ? <><EyeOff size={13} /> Hide</>
                    : <><Eye size={13} /> Publish</>}
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
