"use client";

// Specialty tiles — foodpanda's cuisine row. Each tile gets a themed icon in
// a softly tinted chip (stable hue per specialty). Tapping a tile filters the
// doctor grid; tapping the active tile clears the filter.

import {
  Activity,
  Baby,
  Bone,
  Brain,
  Droplets,
  Ear,
  Eye,
  Heart,
  Pill,
  Ribbon,
  Smile,
  Sparkles,
  Stethoscope,
  Syringe,
  Wind,
  type LucideIcon,
} from "lucide-react";
import { Specialty } from "@/lib/api";
import { nameHue } from "@/lib/avatar";
import { useLang } from "@/lib/i18n";

// Keyword → icon, matched against the (English) specialty name from the DB.
const ICONS: [RegExp, LucideIcon][] = [
  [/cardio|heart/i, Heart],
  [/neuro|brain|psychiat|mental/i, Brain],
  [/pediatr|child/i, Baby],
  [/orthop|bone|trauma/i, Bone],
  [/derma|skin/i, Sparkles],
  [/ophthal|eye/i, Eye],
  [/ent|ear|nose|throat|otolaryn/i, Ear],
  [/dent/i, Smile],
  [/gynec|obstet/i, Ribbon],
  [/nephro|uro|kidney/i, Droplets],
  [/pulmo|chest|respir/i, Wind],
  [/gastro|hepat|liver/i, Activity],
  [/endocrin|diabet|hormone/i, Syringe],
  [/oncol|cancer/i, Ribbon],
  [/medicine|general/i, Pill],
];

function iconFor(specialty: string): LucideIcon {
  for (const [re, Icon] of ICONS) if (re.test(specialty)) return Icon;
  return Stethoscope;
}

export default function SpecialtyTiles({
  specialties,
  active,
  onPick,
}: {
  specialties: Specialty[];
  active: string;
  onPick: (specialty: string) => void;
}) {
  const { t } = useLang();
  if (specialties.length === 0) return null;
  return (
    <div className="flex gap-2 overflow-x-auto pb-1 [-ms-overflow-style:none] [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
      {specialties.map((s) => {
        const isActive = active === s.specialty;
        const Icon = iconFor(s.specialty);
        const hue = nameHue(s.specialty);
        return (
          <button
            key={s.specialty}
            onClick={() => onPick(isActive ? "" : s.specialty)}
            className={`flex shrink-0 items-center gap-2.5 rounded-2xl border px-3.5 py-2.5 text-left transition-all active:scale-95 ${
              isActive
                ? "border-transparent text-white shadow-lg"
                : "border-border bg-surface/80 text-fg hover:-translate-y-0.5 hover:border-indigo-500/40 hover:shadow-md"
            }`}
            style={isActive ? { background: "var(--brand-grad)" } : undefined}
          >
            <span
              className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl"
              style={
                isActive
                  ? { background: "rgba(255,255,255,0.22)" }
                  : {
                      background: `hsl(${hue} 70% 50% / 0.14)`,
                      color: `hsl(${hue} 70% 55%)`,
                    }
              }
            >
              <Icon size={17} className={isActive ? "text-white" : undefined} />
            </span>
            <span>
              <p className="text-xs font-bold">{s.specialty}</p>
              <p className={`text-[10px] ${isActive ? "text-white/70" : "text-faint"}`}>
                {s.doctor_count === 1 ? t("statDoctorOne") : t("statDoctors", { n: s.doctor_count })}
              </p>
            </span>
          </button>
        );
      })}
    </div>
  );
}
