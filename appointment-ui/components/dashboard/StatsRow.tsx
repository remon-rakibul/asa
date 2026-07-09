"use client";

import { useEffect, useRef, useState } from "react";
import { CalendarCheck, CalendarRange, CircleSlash, Clock3 } from "lucide-react";
import type { StatsData } from "@/types";

function useCountUp(target: number, duration = 900) {
  const [value, setValue] = useState(0);
  const rafRef = useRef<number | null>(null);
  useEffect(() => {
    // Respect reduced-motion: show the final number immediately.
    if (typeof window !== "undefined" &&
        window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      setValue(target);
      return;
    }
    const start = performance.now();
    function tick(now: number) {
      const pct = Math.min(1, (now - start) / duration);
      const eased = 1 - Math.pow(1 - pct, 3);
      setValue(Math.round(eased * target));
      if (pct < 1) rafRef.current = requestAnimationFrame(tick);
    }
    rafRef.current = requestAnimationFrame(tick);
    return () => { if (rafRef.current) cancelAnimationFrame(rafRef.current); };
  }, [target, duration]);
  return value;
}

const CARDS = [
  {
    key: "today_count" as keyof StatsData,
    label: "Today's appointments",
    icon: CalendarCheck,
    accent: "var(--primary)",
    softBg: "var(--brand-soft)",
    trend: "today",
  },
  {
    key: "week_count" as keyof StatsData,
    label: "This week",
    icon: CalendarRange,
    accent: "#8b5cf6",
    softBg: "rgba(139,92,246,0.12)",
    trend: "last 7 days",
  },
  {
    key: "available_today" as keyof StatsData,
    label: "Available today",
    icon: Clock3,
    accent: "var(--success)",
    softBg: "var(--success-bg)",
    trend: "open slots",
  },
  {
    key: "cancellations_week" as keyof StatsData,
    label: "Cancellations (7d)",
    icon: CircleSlash,
    accent: "var(--danger)",
    softBg: "var(--danger-bg)",
    trend: "last 7 days",
  },
];

function StatsCard({
  label,
  value,
  icon: Icon,
  accent,
  softBg,
  trend,
  delay,
}: {
  label: string;
  value: number;
  icon: React.ElementType;
  accent: string;
  softBg: string;
  trend: string;
  delay: number;
}) {
  const count = useCountUp(value, 800 + delay);

  return (
    <div
      className="card card-lift relative overflow-hidden p-5 animate-fade-in-up"
      style={{ animationDelay: `${delay}ms` }}
    >
      {/* Background glow blob */}
      <div
        aria-hidden
        className="pointer-events-none absolute -right-4 -top-4 h-24 w-24 rounded-full opacity-40"
        style={{
          background: `radial-gradient(circle, ${accent}, transparent 70%)`,
          filter: "blur(16px)",
        }}
      />

      <div className="relative flex items-start justify-between">
        <div
          className="flex h-11 w-11 items-center justify-center rounded-xl"
          style={{
            background: softBg,
            border: `1px solid color-mix(in srgb, ${accent} 20%, transparent)`,
            color: accent,
          }}
        >
          <Icon size={20} />
        </div>

        <span
          className="inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium"
          style={{ background: softBg, color: accent }}
        >
          {trend}
        </span>
      </div>

      <div className="relative mt-4">
        <span className="text-3xl font-bold tracking-tight text-fg">
          {count}
        </span>
        <div className="mt-1 text-xs font-medium text-muted">{label}</div>
      </div>
    </div>
  );
}

export default function StatsRow({ stats }: { stats: StatsData }) {
  return (
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
      {CARDS.map((c, i) => (
        <StatsCard
          key={c.key}
          label={c.label}
          value={stats[c.key]}
          icon={c.icon}
          accent={c.accent}
          softBg={c.softBg}
          trend={c.trend}
          delay={i * 80}
        />
      ))}
    </div>
  );
}
