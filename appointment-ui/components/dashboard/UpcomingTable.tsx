import { format } from "date-fns";
import { CalendarClock } from "lucide-react";
import type { Appointment } from "@/types";
import CancelDialog from "@/components/appointments/CancelDialog";

function Initials({ name }: { name: string }) {
  const parts = name.trim().split(/\s+/);
  const letters = parts.length >= 2
    ? parts[0][0] + parts[1][0]
    : name.slice(0, 2);
  return (
    <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-xs font-bold uppercase text-white"
      style={{ background: "var(--brand-grad)" }}
    >
      {letters.toUpperCase()}
    </span>
  );
}

export default function UpcomingTable({
  rows,
  onCancelled,
}: {
  rows: Appointment[];
  onCancelled?: () => void;
}) {
  return (
    <div className="card flex h-full flex-col overflow-hidden animate-fade-in-up delay-300">
      {/* Header */}
      <div className="flex shrink-0 items-center justify-between px-5 py-3.5" style={{ borderBottom: "1px solid var(--border)" }}>
        <span className="text-sm font-semibold text-fg">Upcoming Appointments</span>
        <span className="badge badge-surface">{rows.length} next</span>
      </div>

      {rows.length === 0 ? (
        <div className="empty-state flex-1">
          <div className="empty-icon">
            <CalendarClock size={22} />
          </div>
          <p className="empty-title">No upcoming appointments</p>
          <p className="empty-desc">Patients will appear here once they book through the AI receptionist.</p>
        </div>
      ) : (
        <ul className="flex-1 divide-y divide-border overflow-y-auto">
          {rows.map((a, i) => (
            <li
              key={a.id}
              className="flex items-center gap-3 px-5 py-3 transition-colors hover:bg-surface-2 animate-fade-in"
              style={{ animationDelay: `${300 + i * 60}ms` }}
            >
              <Initials name={a.patient_name} />
              <div className="min-w-0 flex-1">
                <div className="text-sm font-medium text-fg truncate">{a.patient_name}</div>
                <div className="flex items-center gap-2 text-xs text-faint">
                  <span>{a.patient_age} yrs</span>
                  <span>·</span>
                  <span className="tabular-nums">{a.patient_mobile}</span>
                </div>
              </div>
              <div className="text-right shrink-0">
                <div className="text-xs font-medium text-muted">
                  {format(new Date(a.scheduled_at), "EEE d MMM")}
                </div>
                <span className="badge badge-primary mt-0.5 text-xs tabular-nums">
                  {format(new Date(a.scheduled_at), "h:mm a")}
                </span>
              </div>
              <div className="shrink-0">
                <CancelDialog
                  appointmentId={a.id}
                  patientName={a.patient_name}
                  onCancelled={onCancelled}
                />
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
