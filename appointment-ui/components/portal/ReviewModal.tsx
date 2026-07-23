"use client";

// Star + text review form. Opens prefilled when the patient already reviewed
// this doctor (PUT is an upsert). Only reachable from appointments that
// actually happened — the backend enforces the same rule with a 403.

import { useState } from "react";
import { X } from "lucide-react";
import { ApiError, MyReview, portalSubmitReview } from "@/lib/api";
import { useLang } from "@/lib/i18n";
import { StarPicker } from "./RatingStars";

export default function ReviewModal({
  doctorId,
  doctorName,
  existing,
  onClose,
  onSaved,
}: {
  doctorId: number;
  doctorName: string;
  existing: MyReview | null;
  onClose: () => void;
  onSaved: (review: MyReview) => void;
}) {
  const { t } = useLang();
  const [rating, setRating] = useState(existing?.rating ?? 0);
  const [text, setText] = useState(existing?.text ?? "");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  async function save() {
    if (!rating || saving) return;
    setSaving(true);
    setError("");
    try {
      const review = await portalSubmitReview(doctorId, rating, text.trim());
      onSaved(review);
      onClose();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : t("reviewFailed"));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-end justify-center bg-black/50 p-4 backdrop-blur-sm sm:items-center"
      onClick={onClose}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label={t("reviewAbout", { name: doctorName })}
        className="w-full max-w-md animate-slide-up rounded-2xl border border-border bg-surface p-5 shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-start justify-between gap-3">
          <div>
            <h2 className="text-base font-bold text-fg">
              {existing ? t("editReview") : t("writeReview")}
            </h2>
            <p className="mt-0.5 text-xs text-muted">{doctorName}</p>
          </div>
          <button
            onClick={onClose}
            aria-label={t("close")}
            className="flex h-8 w-8 items-center justify-center rounded-xl border border-border text-muted transition hover:text-fg"
          >
            <X size={14} />
          </button>
        </div>

        <div className="mt-4 flex justify-center">
          <StarPicker value={rating} onChange={setRating} />
        </div>

        <textarea
          value={text}
          onChange={(e) => setText(e.target.value.slice(0, 1000))}
          rows={4}
          placeholder={t("reviewPlaceholder")}
          className="input mt-4 w-full resize-none text-sm"
        />

        {error && (
          <p role="alert" className="mt-2 text-xs text-danger">{error}</p>
        )}

        <button
          onClick={save}
          disabled={!rating || saving}
          className="btn-primary mt-4 w-full justify-center disabled:opacity-40"
        >
          {saving ? t("saving") : existing ? t("updateReview") : t("saveReview")}
        </button>
      </div>
    </div>
  );
}
