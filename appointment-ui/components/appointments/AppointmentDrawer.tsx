"use client";

import { useCallback, useEffect, useState } from "react";
import { format, formatDistanceToNow } from "date-fns";
import {
  CalendarDays, Phone, User, Clock, Ticket, Activity, CalendarClock,
  LogIn, CheckCircle2, UserX, History,
} from "lucide-react";
import type { Appointment, AppointmentEvent, AppointmentStatus } from "@/types";
import Drawer from "@/components/ui/Drawer";
import StatusBadge from "@/components/ui/StatusBadge";
import CancelDialog from "@/components/appointments/CancelDialog";
import RescheduleModal from "@/components/appointments/RescheduleModal";
import {
  getAppointmentEvents, getAvailability, rescheduleAppointment,
  setAppointmentStatus, type LifecycleStatus,
} from "@/lib/api";
import { useToast } from "@/lib/toast";

function Row({ icon: Icon, label, value }: { icon: React.ElementType; label: string; value: React.ReactNode }) {
  return (
    <div className="flex items-start gap-3 py-2.5">
      <span className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-surface-2 text-muted">
        <Icon size={14} />
      </span>
      <div className="min-w-0">
        <div className="text-xs text-faint">{label}</div>
        <div className="text-sm font-medium text-fg" dir="auto">{value}</div>
      </div>
    </div>
  );
}

const EVENT_LABEL: Record<string, string> = {
  created: "Booked",
  checked_in: "Checked in",
  completed: "Marked completed",
  no_show: "Marked no-show",
  rescheduled: "Rescheduled",
  cancelled: "Cancelled",
  reminder_sent: "Reminder sent",
};

function actorLabel(e: AppointmentEvent): string {
  if (e.actor_email) return e.actor_email;
  if (e.actor_role === "agent") return "AI agent";
  if (e.actor_role === "patient") return "Patient";
  if (e.actor_role) return e.actor_role.replace(/_/g, " ");
  return "System";
}

function ActivityTimeline({ events, loading }: { events: AppointmentEvent[]; loading: boolean }) {
  if (loading) {
    return <div className="px-1 py-3 text-sm text-faint">Loading activity…</div>;
  }
  if (events.length === 0) {
    return <div className="px-1 py-3 text-sm text-faint">No activity recorded yet.</div>;
  }
  return (
    <ol className="space-y-3 px-1 py-2">
      {events.map((e) => (
        <li key={e.id} className="flex gap-3">
          <span className="mt-1 h-2 w-2 shrink-0 rounded-full" style={{ background: "var(--primary)" }} />
          <div className="min-w-0">
            <div className="text-sm font-medium text-fg">
              {EVENT_LABEL[e.event_type] ?? e.event_type}
              {e.event_type === "rescheduled" && e.from_time && e.to_time && (
                <span className="font-normal text-muted">
                  {" "}· {format(new Date(e.from_time), "d MMM h:mm a")} → {format(new Date(e.to_time), "d MMM h:mm a")}
                </span>
              )}
            </div>
            <div className="text-xs text-faint">
              {actorLabel(e)} · {formatDistanceToNow(new Date(e.created_at), { addSuffix: true })}
              {e.note ? ` · ${e.note}` : ""}
            </div>
          </div>
        </li>
      ))}
    </ol>
  );
}

export default function AppointmentDrawer({
  appointment,
  onClose,
  onChanged,
}: {
  appointment: Appointment | null;
  onClose: () => void;
  onChanged?: () => void;
}) {
  const a = appointment;
  const toast = useToast();
  const [rescheduleOpen, setRescheduleOpen] = useState(false);
  const [events, setEvents] = useState<AppointmentEvent[]>([]);
  const [loadingEvents, setLoadingEvents] = useState(false);
  const [busy, setBusy] = useState(false);
  // Reflect status changes immediately even though `appointment` is parent-owned.
  const [statusOverride, setStatusOverride] = useState<AppointmentStatus | null>(null);

  const id = a?.id ?? null;
  const status: AppointmentStatus | undefined = statusOverride ?? a?.status;

  const loadEvents = useCallback(async () => {
    if (!id) return;
    setLoadingEvents(true);
    try {
      setEvents(await getAppointmentEvents(id));
    } catch {
      setEvents([]);
    } finally {
      setLoadingEvents(false);
    }
  }, [id]);

  useEffect(() => {
    setStatusOverride(null);
    if (id) loadEvents();
    else setEvents([]);
  }, [id, loadEvents]);

  async function changeStatus(next: LifecycleStatus, label: string) {
    if (!a || busy) return;
    setBusy(true);
    try {
      await setAppointmentStatus(a.id, next);
      setStatusOverride(next);
      toast.success(label);
      onChanged?.();
      await loadEvents();
    } catch (ex: unknown) {
      toast.error(ex instanceof Error ? ex.message : "Could not update status");
    } finally {
      setBusy(false);
    }
  }

  const active = status === "confirmed" || status === "checked_in";

  return (
    <Drawer
      open={a !== null}
      title={a?.patient_name ?? "Appointment"}
      subtitle={a ? format(new Date(a.scheduled_at), "EEE d MMM yyyy, h:mm a") : undefined}
      onClose={onClose}
      footer={
        a && active ? (
          <div className="flex flex-wrap items-center gap-2">
            {status === "confirmed" && (
              <button onClick={() => changeStatus("checked_in", "Patient checked in")} disabled={busy} className="btn-outline btn-sm">
                <LogIn size={14} /> Check in
              </button>
            )}
            <button onClick={() => changeStatus("completed", "Appointment completed")} disabled={busy} className="btn-outline btn-sm">
              <CheckCircle2 size={14} /> Complete
            </button>
            <button onClick={() => changeStatus("no_show", "Marked as no-show")} disabled={busy} className="btn-outline btn-sm">
              <UserX size={14} /> No-show
            </button>
            <button onClick={() => setRescheduleOpen(true)} disabled={busy} className="btn-outline btn-sm">
              <CalendarClock size={14} /> Reschedule
            </button>
            <CancelDialog
              appointmentId={a.id}
              patientName={a.patient_name}
              onCancelled={() => { setStatusOverride("cancelled"); onChanged?.(); loadEvents(); }}
            />
          </div>
        ) : undefined
      }
    >
      {a && (
        <div className="divide-y divide-border">
          <Row icon={Activity} label="Status" value={<StatusBadge status={status ?? a.status} />} />
          <Row icon={Ticket} label="Serial number" value={a.serial_number ?? "—"} />
          <Row icon={User} label="Patient" value={`${a.patient_name} · age ${a.patient_age}`} />
          <Row icon={Phone} label="Mobile" value={a.patient_mobile} />
          <Row icon={CalendarDays} label="Scheduled" value={format(new Date(a.scheduled_at), "EEE d MMM yyyy, h:mm a")} />
          <Row icon={Clock} label="Duration" value={`${a.duration_mins} min`} />
          <Row icon={CalendarDays} label="Booked" value={format(new Date(a.created_at), "d MMM yyyy, h:mm a")} />
        </div>
      )}

      {a && (
        <div className="mt-4">
          <div className="flex items-center gap-2 px-1 text-xs font-semibold uppercase tracking-wide text-faint">
            <History size={13} /> Activity
          </div>
          <ActivityTimeline events={events} loading={loadingEvents} />
        </div>
      )}

      {a && (
        <RescheduleModal
          open={rescheduleOpen}
          title="Reschedule appointment"
          fetchSlots={() => getAvailability(14)}
          onConfirm={(slot) => rescheduleAppointment(a.id, slot).then(() => {})}
          onClose={() => setRescheduleOpen(false)}
          onDone={() => { toast.success("Appointment rescheduled"); onChanged?.(); loadEvents(); }}
        />
      )}
    </Drawer>
  );
}
