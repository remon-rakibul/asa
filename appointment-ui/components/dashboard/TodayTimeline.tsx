import { CalendarDays } from "lucide-react";
import type { TimelineSlot } from "@/lib/derive";

export default function TodayTimeline({ slots }: { slots: TimelineSlot[] }) {
  const bookedCount = slots.filter((s) => s.booked).length;

  return (
    <div className="card flex h-full flex-col overflow-hidden animate-fade-in-up delay-200">
      {/* Header */}
      <div className="flex shrink-0 items-center justify-between px-5 py-3.5" style={{ borderBottom: "1px solid var(--border)" }}>
        <div className="flex items-center gap-2">
          <span className="status-dot status-dot-info animate-pulse-dot" />
          <span className="text-sm font-semibold text-fg">Today&apos;s Schedule</span>
        </div>
        <span className="badge badge-primary">{bookedCount} booked</span>
      </div>

      {slots.length === 0 ? (
        <div className="empty-state flex-1">
          <div className="empty-icon">
            <CalendarDays size={22} />
          </div>
          <p className="empty-title">Doctor not scheduled today</p>
          <p className="empty-desc">No schedule has been set for today. Set it in the Schedule page.</p>
        </div>
      ) : (
        <div className="relative flex-1 overflow-y-auto px-5 py-3">
          {/* Vertical timeline line */}
          <div className="absolute left-[68px] top-0 bottom-0 w-px" style={{ background: "var(--border)" }} />

          <ul>
            {slots.map((slot, i) => (
              <li
                key={slot.iso}
                className="relative flex items-center gap-4 py-2 transition-colors hover:bg-surface-2 rounded-lg animate-fade-in"
                style={{ animationDelay: `${200 + i * 35}ms` }}
              >
                {/* Time */}
                <span className="w-12 shrink-0 text-right text-xs font-medium tabular-nums text-muted">
                  {slot.time}
                </span>

                {/* Timeline dot */}
                <span className="relative z-10 flex h-3 w-3 shrink-0 items-center justify-center rounded-full ring-2"
                  style={{
                    background: slot.booked
                      ? "var(--primary)"
                      : slot.past
                      ? "var(--faint)"
                      : "var(--surface)",
                    border: slot.booked
                      ? "2px solid color-mix(in srgb, var(--primary) 50%, transparent)"
                      : "2px solid var(--border)",
                    boxShadow: slot.booked
                      ? "0 0 0 3px color-mix(in srgb, var(--primary) 20%, transparent)"
                      : "none",
                  }}
                />

                {/* Content */}
                {slot.booked ? (
                  <div className="flex flex-1 items-center justify-between rounded-lg px-3 py-2"
                    style={{
                      background: "var(--brand-soft)",
                      border: "1px solid color-mix(in srgb, var(--primary) 20%, transparent)",
                    }}
                  >
                    <span className="text-sm font-medium text-primary">{slot.patientName}</span>
                    <span className="badge badge-surface">{slot.patientAge} yrs</span>
                  </div>
                ) : (
                  <span className={`flex-1 rounded-lg px-3 py-2 text-xs ${slot.past ? "opacity-40" : "opacity-70"}`}
                    style={{
                      background: "var(--surface-2)",
                      color: "var(--faint)",
                      border: "1px solid var(--border)",
                    }}
                  >
                    খালি — available
                  </span>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
