export type AppointmentStatus =
  | "confirmed"
  | "pending"
  | "checked_in"
  | "completed"
  | "no_show"
  | "cancelled";

export type Appointment = {
  id: string;
  patient_name: string;
  patient_age: number;
  patient_mobile: string;
  scheduled_at: Date;
  duration_mins: number;
  status: AppointmentStatus;
  created_at: Date;
  serial_number: number | null;
  doctor_id?: number | null;
  /** Set when the patient replied "১" to the 24h reminder (two-way SMS). */
  patient_confirmed_at?: Date | string | null;
};

export type AppointmentEvent = {
  id: number;
  event_type: string;
  from_status: string | null;
  to_status: string | null;
  from_time: Date | null;
  to_time: Date | null;
  actor_user_id: number | null;
  actor_role: string;
  actor_email: string | null;
  note: string | null;
  created_at: Date;
};

export type ScheduleRow = {
  id?: number;
  day_of_week: number; // 0=Mon ... 6=Sun
  start_time: string; // "09:00"
  end_time: string; // "17:00"
  slot_duration: number; // minutes
  active: boolean; // UI-only; an inactive day has no DB row
};

export type StatsData = {
  today_count: number;
  week_count: number;
  available_today: number;
  cancellations_week: number;
};

export type AppointmentFilters = {
  date_from?: string;
  date_to?: string;
  status?: AppointmentStatus | "all";
  q?: string;
};

export const WEEKDAY_LABELS = [
  "Monday",
  "Tuesday",
  "Wednesday",
  "Thursday",
  "Friday",
  "Saturday",
  "Sunday",
] as const;
