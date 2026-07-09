"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import {
  ArrowLeft,
  CalendarDays,
  Building2,
  Stethoscope,
  Clock,
  Plus,
  CalendarPlus,
  CalendarClock,
  X,
} from "lucide-react";
import {
  PatientAppointment,
  listMyAppointments,
  portalCancelAppointment,
  portalDepartmentAvailability,
  portalRescheduleAppointment,
} from "@/lib/api";
import { useToast } from "@/lib/toast";
import ConfirmDialog from "@/components/ui/ConfirmDialog";
import RescheduleModal from "@/components/appointments/RescheduleModal";

function fmt(iso: string) {
  return new Date(iso).toLocaleString("en-GB", {
    day: "2-digit",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/** Human-friendly relative day label, e.g. "Today", "Tomorrow", "in 3 days". */
function relativeDay(iso: string): string {
  const d = new Date(iso);
  const today = new Date();
  const startOf = (x: Date) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
  const days = Math.round((startOf(d) - startOf(today)) / 86_400_000);
  if (days === 0) return "Today";
  if (days === 1) return "Tomorrow";
  if (days === -1) return "Yesterday";
  if (days > 1) return `in ${days} days`;
  return `${Math.abs(days)} days ago`;
}

/** Build and download an .ics calendar invite for an appointment. */
function downloadIcs(a: PatientAppointment) {
  const start = new Date(a.scheduled_at);
  const end = new Date(start.getTime() + (a.duration_mins ?? 30) * 60_000);
  const toUtc = (d: Date) => d.toISOString().replace(/[-:]/g, "").split(".")[0] + "Z";
  const title = `Appointment — ${a.department_name ?? "Doctor"}${a.doctor_name ? ` (${a.doctor_name})` : ""}`;
  const loc = a.hospital_name ?? "";
  const lines = [
    "BEGIN:VCALENDAR",
    "VERSION:2.0",
    "PRODID:-//Clinic Console//Appointment//EN",
    "BEGIN:VEVENT",
    `UID:${a.id}@clinic-console`,
    `DTSTAMP:${toUtc(new Date())}`,
    `DTSTART:${toUtc(start)}`,
    `DTEND:${toUtc(end)}`,
    `SUMMARY:${title}`,
    `LOCATION:${loc}`,
    a.serial_number != null ? `DESCRIPTION:Serial #${a.serial_number}` : "",
    "END:VEVENT",
    "END:VCALENDAR",
  ].filter(Boolean);
  const blob = new Blob([lines.join("\r\n")], { type: "text/calendar" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `appointment-${a.serial_number ?? a.id}.ics`;
  link.click();
  URL.revokeObjectURL(url);
}

function StatusBadge({ status }: { status: string }) {
  const cls =
    status === "confirmed"
      ? "badge-success"
      : status === "cancelled"
      ? "badge-danger"
      : "badge-warning";
  return <span className={`badge capitalize ${cls}`}>{status}</span>;
}

function AppointmentCard({
  a,
  idx,
  canCancel,
  onCancel,
  onReschedule,
}: {
  a: PatientAppointment;
  idx: number;
  canCancel: boolean;
  onCancel: () => void;
  onReschedule?: () => void;
}) {
  return (
    <div
      className="group relative overflow-hidden rounded-2xl border border-border bg-surface/80 p-5 backdrop-blur-sm transition-all duration-300 hover:border-indigo-500/30 hover:shadow-[0_0_30px_-8px_rgba(99,102,241,0.4)] animate-fade-in-up"
      style={{ animationDelay: `${idx * 60}ms` }}
    >
      <div
        className="absolute inset-x-0 top-0 h-[2px]"
        style={{
          background:
            a.status === "cancelled"
              ? "linear-gradient(90deg, #ef4444, #f87171)"
              : "var(--brand-grad)",
        }}
      />

      <div className="flex items-start gap-4">
        <div
          className="flex h-12 w-12 shrink-0 items-center justify-center rounded-2xl text-white shadow-lg"
          style={{
            background:
              a.status === "cancelled"
                ? "linear-gradient(135deg, #ef4444, #f87171)"
                : "var(--brand-grad)",
          }}
        >
          <CalendarDays size={20} />
        </div>

        <div className="min-w-0 flex-1">
          <div className="flex items-start justify-between gap-2">
            <div className="min-w-0">
              <p className="truncate font-bold text-fg">{a.hospital_name ?? "Appointment"}</p>
              <p className="mt-0.5 flex flex-wrap items-center gap-x-1.5 gap-y-0.5 text-xs text-muted">
                <Building2 size={10} className="shrink-0" />
                {a.department_name ?? "—"}
                {a.doctor_name && (
                  <>
                    <span className="text-faint">·</span>
                    <Stethoscope size={10} className="shrink-0" />
                    {a.doctor_name}
                  </>
                )}
              </p>
            </div>
            <StatusBadge status={a.status} />
          </div>

          <div className="mt-3 flex flex-wrap items-center justify-between gap-2">
            <div className="flex items-center gap-1.5 text-xs text-muted">
              <Clock size={11} className="shrink-0" />
              <span className="font-semibold text-fg">{relativeDay(a.scheduled_at)}</span>
              <span className="text-faint">·</span>
              {fmt(a.scheduled_at)}
            </div>
            {a.serial_number != null && (
              <span className="rounded-full border border-border bg-surface-3 px-2.5 py-0.5 text-xs font-semibold text-faint">
                Serial #{a.serial_number}
              </span>
            )}
          </div>

          {/* Actions */}
          {a.status === "confirmed" && (
            <div className="mt-3 flex items-center gap-2 border-t border-border pt-3">
              <button
                onClick={() => downloadIcs(a)}
                className="inline-flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-xs font-semibold text-muted transition-colors hover:bg-surface-3 hover:text-fg"
              >
                <CalendarPlus size={13} /> Add to calendar
              </button>
              {canCancel && onReschedule && (
                <button
                  onClick={onReschedule}
                  className="inline-flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-xs font-semibold text-muted transition-colors hover:bg-surface-3 hover:text-fg"
                >
                  <CalendarClock size={13} /> Reschedule
                </button>
              )}
              {canCancel && (
                <button
                  onClick={onCancel}
                  className="inline-flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-xs font-semibold text-muted transition-colors hover:bg-danger/10 hover:text-danger"
                >
                  <X size={13} /> Cancel
                </button>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

export default function MyAppointmentsPage() {
  const toast = useToast();
  const [rows, setRows] = useState<PatientAppointment[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [pendingCancel, setPendingCancel] = useState<PatientAppointment | null>(null);
  const [cancelling, setCancelling] = useState(false);
  const [pendingReschedule, setPendingReschedule] = useState<PatientAppointment | null>(null);

  const load = useCallback((withSpinner = false) => {
    if (withSpinner) setLoading(true);
    return listMyAppointments()
      .then((r) => { setRows(r); setError(""); })
      .catch((e) => setError(e instanceof Error ? e.message : "Could not load appointments."))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => { load(); }, [load]);

  // Refresh when the tab regains focus so a booking made in chat shows up.
  useEffect(() => {
    const onFocus = () => load();
    window.addEventListener("focus", onFocus);
    return () => window.removeEventListener("focus", onFocus);
  }, [load]);

  async function confirmCancel() {
    if (!pendingCancel) return;
    setCancelling(true);
    try {
      await portalCancelAppointment(pendingCancel.id);
      await load();
      toast.success("Appointment cancelled");
      setPendingCancel(null);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Could not cancel appointment");
    } finally {
      setCancelling(false);
    }
  }

  const now = Date.now();
  const upcoming = rows
    .filter((a) => a.status === "confirmed" && new Date(a.scheduled_at).getTime() >= now)
    .sort((a, b) => +new Date(a.scheduled_at) - +new Date(b.scheduled_at));
  const past = rows
    .filter((a) => !(a.status === "confirmed" && new Date(a.scheduled_at).getTime() >= now))
    .sort((a, b) => +new Date(b.scheduled_at) - +new Date(a.scheduled_at));

  return (
    <div className="relative min-h-screen w-full overflow-y-auto bg-bg">
      {/* Background radial glows */}
      <div className="pointer-events-none fixed inset-0 overflow-hidden" aria-hidden>
        <div className="absolute -top-40 left-1/2 h-[600px] w-[600px] -translate-x-1/2 rounded-full opacity-20 blur-[100px]"
          style={{ background: "radial-gradient(circle, #6366f1 0%, transparent 70%)" }} />
        <div className="absolute top-1/2 -left-32 h-[400px] w-[400px] rounded-full opacity-10 blur-[80px]"
          style={{ background: "radial-gradient(circle, #8b5cf6 0%, transparent 70%)" }} />
        <div className="absolute bottom-0 right-0 h-[300px] w-[300px] rounded-full opacity-10 blur-[80px]"
          style={{ background: "radial-gradient(circle, #a78bfa 0%, transparent 70%)" }} />
      </div>

      {/* Gradient hero header */}
      <div className="relative overflow-hidden" style={{ background: "var(--brand-grad)" }}>
        <div className="absolute inset-0" style={{ background: "linear-gradient(180deg, rgba(0,0,0,0.05) 0%, rgba(0,0,0,0.25) 100%)" }} />
        <div className="pointer-events-none absolute -right-20 -top-20 h-72 w-72 rounded-full bg-white/10 blur-3xl" />
        <div className="pointer-events-none absolute bottom-0 left-1/4 h-32 w-64 rounded-full bg-white/5 blur-2xl" />

        <div className="relative mx-auto max-w-3xl px-5 pb-7 pt-5">
          <div className="flex items-center justify-between">
            <Link href="/portal" aria-label="Back to portal"
              className="flex h-9 w-9 items-center justify-center rounded-xl bg-white/20 text-white backdrop-blur-sm transition hover:bg-white/30">
              <ArrowLeft size={16} />
            </Link>
            <Link href="/portal"
              className="flex items-center gap-1.5 rounded-xl bg-white/20 px-3 py-1.5 text-xs font-semibold text-white backdrop-blur-sm transition hover:bg-white/30">
              <Plus size={13} /> New appointment
            </Link>
          </div>

          <div className="mt-5">
            <h1 className="text-2xl font-extrabold tracking-tight text-white drop-shadow">My Appointments</h1>
            <p className="mt-1 text-sm text-white/65">All your upcoming and past bookings</p>
          </div>
        </div>
      </div>

      {/* Content */}
      <main className="relative mx-auto max-w-3xl space-y-6 px-4 pb-12 pt-6">
        {error && (
          <div role="alert" className="rounded-2xl border border-danger/20 bg-danger/10 px-4 py-3 text-sm text-danger animate-fade-in">
            {error}
          </div>
        )}

        {loading && !error && (
          <div className="space-y-3">
            {[1, 2, 3].map((i) => (
              <div key={i} className="skeleton-card h-28 rounded-2xl" />
            ))}
          </div>
        )}

        {!loading && !error && rows.length === 0 && (
          <div className="flex flex-col items-center gap-4 rounded-2xl border border-border bg-surface/80 p-12 text-center backdrop-blur-sm animate-fade-in-up">
            <div className="flex h-16 w-16 items-center justify-center rounded-2xl bg-indigo-500/10 text-indigo-400">
              <CalendarDays size={28} />
            </div>
            <div>
              <p className="text-base font-bold text-fg">No appointments yet</p>
              <p className="mt-1 text-sm text-muted">Book your first appointment with a doctor.</p>
            </div>
            <Link href="/portal"
              className="mt-2 rounded-xl px-6 py-2.5 text-sm font-bold text-white shadow transition-all hover:opacity-90 active:scale-95"
              style={{ background: "var(--brand-grad)" }}>
              Book now
            </Link>
          </div>
        )}

        {!loading && !error && upcoming.length > 0 && (
          <section className="space-y-3">
            <div className="flex items-center gap-2">
              <div className="h-5 w-1 rounded-full" style={{ background: "var(--brand-grad)" }} />
              <p className="text-xs font-bold uppercase tracking-widest text-faint">Upcoming · {upcoming.length}</p>
            </div>
            {upcoming.map((a, idx) => (
              <AppointmentCard
                key={a.id}
                a={a}
                idx={idx}
                canCancel
                onCancel={() => setPendingCancel(a)}
                onReschedule={a.clinic_id ? () => setPendingReschedule(a) : undefined}
              />
            ))}
          </section>
        )}

        {!loading && !error && past.length > 0 && (
          <section className="space-y-3">
            <div className="flex items-center gap-2">
              <div className="h-5 w-1 rounded-full bg-faint/40" />
              <p className="text-xs font-bold uppercase tracking-widest text-faint">Past & cancelled · {past.length}</p>
            </div>
            {past.map((a, idx) => (
              <AppointmentCard key={a.id} a={a} idx={idx} canCancel={false} onCancel={() => {}} />
            ))}
          </section>
        )}
      </main>

      <ConfirmDialog
        open={pendingCancel !== null}
        destructive
        title="Cancel appointment"
        subtitle={pendingCancel ? `${pendingCancel.department_name ?? ""} · ${fmt(pendingCancel.scheduled_at)}` : undefined}
        description="Are you sure you want to cancel this appointment? This cannot be undone."
        confirmLabel="Cancel appointment"
        cancelLabel="Keep it"
        busyLabel="Cancelling…"
        busy={cancelling}
        onConfirm={confirmCancel}
        onClose={() => { if (!cancelling) setPendingCancel(null); }}
      />

      {pendingReschedule && pendingReschedule.clinic_id && (
        <RescheduleModal
          open={pendingReschedule !== null}
          title="Reschedule appointment"
          fetchSlots={() => portalDepartmentAvailability(pendingReschedule.clinic_id as number)}
          onConfirm={(slot) => portalRescheduleAppointment(pendingReschedule.id, slot).then(() => {})}
          onClose={() => setPendingReschedule(null)}
          onDone={() => { toast.success("Appointment rescheduled"); load(); }}
        />
      )}
    </div>
  );
}
