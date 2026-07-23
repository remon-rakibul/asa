"use client";

import { Star } from "lucide-react";
import { useLang } from "@/lib/i18n";

/** Read-only star row, foodpanda-style: ★4.5 (12). Hidden when no reviews. */
export default function RatingStars({
  rating,
  count,
  size = 13,
}: {
  rating: number;
  count?: number;
  size?: number;
}) {
  const { t } = useLang();
  if (!count) {
    return <span className="text-xs text-faint">{t("newBadge")}</span>;
  }
  return (
    <span className="inline-flex items-center gap-1">
      <Star size={size} className="shrink-0 fill-amber-400 text-amber-400" />
      <span className="text-xs font-bold text-fg">{rating.toFixed(1)}</span>
      <span className="text-xs text-faint">({count})</span>
    </span>
  );
}

/** Interactive 1–5 star picker for the review form. */
export function StarPicker({
  value,
  onChange,
  size = 26,
}: {
  value: number;
  onChange: (v: number) => void;
  size?: number;
}) {
  const { t } = useLang();
  return (
    <div className="flex items-center gap-1" role="radiogroup" aria-label={t("rating")}>
      {[1, 2, 3, 4, 5].map((n) => (
        <button
          key={n}
          type="button"
          role="radio"
          aria-checked={value === n}
          aria-label={t("stars", { n })}
          onClick={() => onChange(n)}
          className="rounded-lg p-0.5 transition-transform hover:scale-110 active:scale-95"
        >
          <Star
            size={size}
            className={n <= value ? "fill-amber-400 text-amber-400" : "text-border"}
          />
        </button>
      ))}
    </div>
  );
}
