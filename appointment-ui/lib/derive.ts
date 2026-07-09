// Client-side derivation of dashboard data from the API's appointment +
// schedule responses (replaces the old direct-Postgres queries).

import type { Appointment, ScheduleRow, StatsData } from "@/types";

export type TimelineSlot = {
  time: string;
  iso: string;
  booked: boolean;
  patientName?: string;
  patientAge?: number;
  past: boolean;
};

/** JS weekday (0=Sun..6=Sat) -> schema (0=Mon..6=Sun). */
export function toDayOfWeek(date: Date): number {
  return (date.getDay() + 6) % 7;
}

function isSameDay(a: Date, b: Date): boolean {
  return (
    a.getFullYear() === b.getFullYear() &&
    a.getMonth() === b.getMonth() &&
    a.getDate() === b.getDate()
  );
}

export function todaysAppointments(rows: Appointment[]): Appointment[] {
  const now = new Date();
  return rows
    .filter((a) => a.status === "confirmed" && isSameDay(new Date(a.scheduled_at), now))
    .sort((x, y) => +new Date(x.scheduled_at) - +new Date(y.scheduled_at));
}

export function upcomingAppointments(rows: Appointment[], limit = 5): Appointment[] {
  const now = Date.now();
  return rows
    .filter((a) => a.status === "confirmed" && +new Date(a.scheduled_at) >= now)
    .sort((x, y) => +new Date(x.scheduled_at) - +new Date(y.scheduled_at))
    .slice(0, limit);
}

export function buildTodayTimeline(
  rows: Appointment[],
  schedule: ScheduleRow[]
): TimelineSlot[] {
  const now = new Date();
  const day = schedule.find((s) => s.day_of_week === toDayOfWeek(now));
  if (!day || !day.active) return [];

  const booked = new Map(
    todaysAppointments(rows).map((a) => [
      new Date(a.scheduled_at).toISOString().slice(0, 16),
      a,
    ])
  );

  const [sh, sm] = day.start_time.split(":").map(Number);
  const [eh, em] = day.end_time.split(":").map(Number);
  const cursor = new Date(now);
  cursor.setHours(sh, sm, 0, 0);
  const end = new Date(now);
  end.setHours(eh, em, 0, 0);

  const slots: TimelineSlot[] = [];
  while (cursor.getTime() + day.slot_duration * 60000 <= end.getTime()) {
    const appt = booked.get(cursor.toISOString().slice(0, 16));
    slots.push({
      time: cursor.toTimeString().slice(0, 5),
      iso: cursor.toISOString(),
      booked: Boolean(appt),
      patientName: appt?.patient_name,
      patientAge: appt?.patient_age,
      past: cursor.getTime() < now.getTime(),
    });
    cursor.setMinutes(cursor.getMinutes() + day.slot_duration);
  }
  return slots;
}

export function dashboardStats(
  rows: Appointment[],
  timeline: TimelineSlot[]
): StatsData {
  const now = Date.now();
  const weekEnd = now + 7 * 86400000;
  const weekAgo = now - 7 * 86400000;

  return {
    today_count: todaysAppointments(rows).length,
    week_count: rows.filter(
      (a) =>
        a.status === "confirmed" &&
        +new Date(a.scheduled_at) >= now &&
        +new Date(a.scheduled_at) < weekEnd
    ).length,
    available_today: timeline.filter((s) => !s.booked && !s.past).length,
    cancellations_week: rows.filter(
      (a) => a.status === "cancelled" && +new Date(a.created_at) >= weekAgo
    ).length,
  };
}
