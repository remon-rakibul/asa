"use client";

// Marketplace doctor card (foodpanda vendor-card parity): photo, name,
// degrees, specialty, hospital + department, fee, rating, next-slot chip.

import Link from "next/link";
import { Building2, Clock } from "lucide-react";
import { SearchDoctor, doctorPhotoUrl } from "@/lib/api";
import { avatarGradient, initialsOf } from "@/lib/avatar";
import { useLang } from "@/lib/i18n";
import RatingStars from "./RatingStars";

type TFn = ReturnType<typeof useLang>["t"];

export function feeLabel(
  doc: { fee_new: number | null; fee_followup: number | null },
  t: TFn,
): string {
  if (doc.fee_new != null && doc.fee_followup != null)
    return `৳${doc.fee_new} / ${t("followUpFee", { n: doc.fee_followup })}`;
  if (doc.fee_new != null) return `৳${doc.fee_new}`;
  if (doc.fee_followup != null) return t("followUpFee", { n: doc.fee_followup });
  return t("feeNotSet");
}

export default function DoctorCard({ doc, index = 0 }: { doc: SearchDoctor; index?: number }) {
  const { t } = useLang();
  return (
    <Link
      href={`/portal/doctor/${doc.id}`}
      className="group relative block overflow-hidden rounded-2xl border border-border bg-surface/80 p-4 backdrop-blur-sm transition-all duration-300 hover:border-indigo-500/40 hover:shadow-[0_0_40px_-8px_rgba(99,102,241,0.5)] active:scale-[0.98] animate-fade-in-up"
      style={{ animationDelay: `${Math.min(index, 8) * 60}ms` }}
    >
      <div className="absolute inset-x-0 top-0 h-[2px]" style={{ background: "var(--brand-grad)" }} />
      <div className="flex items-start gap-3.5">
        {doc.has_photo ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img
            src={doctorPhotoUrl(doc.id)}
            alt={doc.name}
            className="h-[56px] w-[56px] shrink-0 rounded-2xl object-cover shadow-lg transition-transform duration-300 group-hover:scale-105"
          />
        ) : (
          <span
            className="flex h-[56px] w-[56px] shrink-0 items-center justify-center rounded-2xl text-[17px] font-extrabold tracking-wide text-white shadow-lg transition-transform duration-300 group-hover:scale-105"
            style={{ background: avatarGradient(doc.name) }}
          >
            {initialsOf(doc.name)}
          </span>
        )}
        <div className="min-w-0 flex-1">
          <div className="flex items-start justify-between gap-2">
            <p className="truncate text-[15px] font-bold text-fg">{doc.name}</p>
            <RatingStars rating={doc.avg_rating} count={doc.review_count} />
          </div>
          {doc.degrees && (
            <p className="mt-0.5 truncate text-xs font-semibold text-primary">{doc.degrees}</p>
          )}
          {doc.specialty && <p className="mt-0.5 truncate text-xs text-muted">{doc.specialty}</p>}
          <p className="mt-1 flex items-center gap-1 truncate text-xs text-faint">
            <Building2 size={11} className="shrink-0" />
            <span className="truncate">{doc.hospital_name} · {doc.department_name}</span>
          </p>
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <span className="rounded-full bg-[var(--brand-soft)] px-2.5 py-1 text-[11px] font-bold text-primary">
              {feeLabel(doc, t)}
            </span>
            {doc.next_slot && (
              <span className="inline-flex items-center gap-1 rounded-full px-2.5 py-1 text-[11px] font-semibold text-success"
                style={{ background: "var(--success-bg)" }}>
                <Clock size={10} /> {doc.next_slot.label}
              </span>
            )}
          </div>
        </div>
      </div>
    </Link>
  );
}
