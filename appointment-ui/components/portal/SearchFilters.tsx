"use client";

// Filter row under the search bar: hospital, max fee, and sort (earliest
// available / lowest fee / top rated) — foodpanda's sidebar collapsed into
// one mobile-friendly strip.

import { DoctorSort, Hospital } from "@/lib/api";
import { StringKey, useLang } from "@/lib/i18n";

const SORTS: { value: DoctorSort; labelKey: StringKey }[] = [
  { value: "rating", labelKey: "sortRating" },
  { value: "available", labelKey: "sortAvailable" },
  { value: "fee", labelKey: "sortFee" },
];

export default function SearchFilters({
  hospitals,
  hospitalId,
  onHospital,
  maxFee,
  onMaxFee,
  sort,
  onSort,
}: {
  hospitals: Hospital[];
  hospitalId: number | "";
  onHospital: (id: number | "") => void;
  maxFee: string;
  onMaxFee: (v: string) => void;
  sort: DoctorSort;
  onSort: (s: DoctorSort) => void;
}) {
  const { t } = useLang();
  return (
    <div className="flex flex-wrap items-center gap-2">
      {/* Sort segmented control */}
      <div className="flex rounded-xl border border-border bg-surface/80 p-0.5">
        {SORTS.map((s) => (
          <button
            key={s.value}
            onClick={() => onSort(s.value)}
            className={`rounded-[10px] px-3 py-1.5 text-xs font-semibold transition ${
              sort === s.value ? "text-white shadow" : "text-muted hover:text-fg"
            }`}
            style={sort === s.value ? { background: "var(--brand-grad)" } : undefined}
          >
            {t(s.labelKey)}
          </button>
        ))}
      </div>

      <select
        value={hospitalId}
        onChange={(e) => onHospital(e.target.value ? Number(e.target.value) : "")}
        aria-label={t("hospital")}
        className="input h-9 w-auto max-w-[180px] rounded-xl py-0 text-xs"
      >
        <option value="">{t("allHospitals")}</option>
        {hospitals.map((h) => (
          <option key={h.id} value={h.id}>{h.name}</option>
        ))}
      </select>

      <input
        type="number"
        inputMode="numeric"
        min={0}
        value={maxFee}
        onChange={(e) => onMaxFee(e.target.value)}
        placeholder={t("maxFee")}
        aria-label={t("maxFee")}
        className="input h-9 w-28 rounded-xl py-0 text-xs"
      />
    </div>
  );
}
