"use client";

// Thin client for the FastAPI backend. The admin UI no longer touches Postgres
// directly — every read/write goes through the clinic-scoped, authenticated API.

import type {
  Appointment,
  AppointmentEvent,
  AppointmentFilters,
  ScheduleRow,
} from "@/types";

const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
const TOKEN_KEY = "clinic_token";

export function getToken(): string | null {
  if (typeof window === "undefined") return null;
  return window.localStorage.getItem(TOKEN_KEY);
}

export function setToken(token: string): void {
  window.localStorage.setItem(TOKEN_KEY, token);
}

export function clearToken(): void {
  window.localStorage.removeItem(TOKEN_KEY);
}

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const token = getToken();
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(init.headers as Record<string, string> | undefined),
  };
  if (token) headers.Authorization = `Bearer ${token}`;

  const res = await fetch(`${API}${path}`, { ...init, headers });
  if (res.status === 401) {
    clearToken();
    throw new ApiError(401, "Unauthorized");
  }
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    let message = text || `HTTP ${res.status}`;
    try {
      const json = JSON.parse(text);
      if (typeof json.detail === "string") message = json.detail;
      else if (Array.isArray(json.detail))
        message = json.detail.map((e: { msg: string }) => e.msg).join("; ");
    } catch { /* not JSON — keep raw text */ }
    throw new ApiError(res.status, message);
  }
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

// --- Public (unauthenticated) ---

export type PublicPricing = {
  patient_subscription_fee: number;
  hospital_subscription_fee: number;
  free_agent_bookings_per_month: number;
  patient_trial_days: number;
  currency: string;
};

/** Published plan pricing for the landing page — no auth required. */
export function getPublicPricing(): Promise<PublicPricing> {
  return request<PublicPricing>("/public/pricing");
}

// --- Auth ---

export type SmsTemplates = {
  confirmation?: string;
  reminder?: string;
  doctor_alert?: string;
  token?: string;
};

export type Clinic = {
  id: number;
  slug: string;
  name: string;
  doctor_name: string;
  doctor_phone: string;
  timezone: string;
  availability_days_ahead: number;
  status: string;
  greeting_instructions: string | null;
  sms_sender_id: string | null;
  sms_templates: SmsTemplates;
  role: string | null;
};

export type ClinicUpdate = {
  name?: string;
  doctor_name?: string;
  doctor_phone?: string;
  availability_days_ahead?: number;
  timezone?: string;
  greeting_instructions?: string;
  sms_sender_id?: string;
  sms_templates?: SmsTemplates;
};

export async function login(email: string, password: string): Promise<number | null> {
  const data = await request<{ access_token: string; clinic_id: number | null }>(
    "/auth/login",
    { method: "POST", body: JSON.stringify({ email, password }) }
  );
  setToken(data.access_token);
  return data.clinic_id;
}

export type HospitalSignupInput = {
  slug: string;
  name: string;
  doctor_name?: string;
  doctor_phone?: string;
  admin_email: string;
  admin_password: string;
};

/** Public hospital admin self-signup — no platform key needed. Creates the
 *  hospital, its first department, and the admin login in one step, and
 *  starts a free first-month subscription server-side. */
export async function hospitalSignup(body: HospitalSignupInput): Promise<number | null> {
  const data = await request<{ access_token: string; clinic_id: number | null }>(
    "/hospitals/signup",
    { method: "POST", body: JSON.stringify(body) }
  );
  setToken(data.access_token);
  return data.clinic_id;
}

export async function logout(): Promise<void> {
  await request("/auth/logout", { method: "POST" }).catch(() => {});
  clearToken();
}

export function getMe(): Promise<Clinic> {
  return request<Clinic>("/auth/me");
}

// --- Platform-admin dashboard (staff token, platform_admin role) ---

export type PlatformHospital = {
  id: number;
  name: string;
  slug: string;
  billing_status: string;
  booking_fee: number | null;
  subscription_status: string | null;
  monthly_fee: number | null;
  current_period_end: string | null;
  fee_revenue: number;
  paid_bookings: number;
  dues: number;
};

export type PlatformOverview = {
  booking_fee_revenue: number;
  patient_sub_revenue: number;
  paid_count: number;
  refunds_pending: number;
  subscribers_premium: number;
  subscribers_trialing: number;
  open_platform_escalations: number;
  hospitals: PlatformHospital[];
};

export type PlatformPayment = {
  id: string;
  kind: string;
  amount: number;
  currency: string;
  status: string;
  provider: string;
  hospital_id: number | null;
  hospital_name: string | null;
  appointment_id: string | null;
  account_id: number | null;
  created_at: string;
  paid_at: string | null;
  refund_needed: boolean;
};

export function platformOverview(): Promise<PlatformOverview> {
  return request<PlatformOverview>("/platform/overview");
}

export function platformPayments(params: {
  kind?: string; status?: string; hospitalId?: number;
} = {}): Promise<PlatformPayment[]> {
  const q = new URLSearchParams();
  if (params.kind) q.set("kind", params.kind);
  if (params.status) q.set("status", params.status);
  if (params.hospitalId != null) q.set("hospital_id", String(params.hospitalId));
  const qs = q.toString();
  return request<PlatformPayment[]>(`/platform/payments${qs ? `?${qs}` : ""}`);
}

export function platformMarkHospitalPaid(hospitalId: number): Promise<{ ok: boolean }> {
  return request(`/platform/hospitals/${hospitalId}/subscription/mark-paid`, { method: "POST" });
}

export function platformMarkPaymentPaid(paymentId: string): Promise<{ ok: boolean }> {
  return request(`/platform/payments/${paymentId}/mark-paid`, { method: "POST" });
}

export function platformRefundPayment(paymentId: string, note = ""): Promise<{ ok: boolean }> {
  const qs = note ? `?note=${encodeURIComponent(note)}` : "";
  return request(`/platform/payments/${paymentId}/refund${qs}`, { method: "POST" });
}

export function updateClinic(patch: ClinicUpdate): Promise<Clinic> {
  return request<Clinic>("/clinics/me", {
    method: "PATCH",
    body: JSON.stringify(patch),
  });
}

// --- Appointments ---

export function listAppointments(filters: AppointmentFilters = {}): Promise<Appointment[]> {
  const params = new URLSearchParams();
  if (filters.date_from) params.set("date_from", filters.date_from);
  if (filters.date_to) params.set("date_to", filters.date_to);
  if (filters.status && filters.status !== "all") params.set("status", filters.status);
  if (filters.q) params.set("q", filters.q);
  const qs = params.toString();
  return request<Appointment[]>(`/appointments${qs ? `?${qs}` : ""}`);
}

export type Slot = { datetime: string; label: string };

export function getAvailability(daysAhead = 14): Promise<Slot[]> {
  return request<Slot[]>(`/availability?days_ahead=${daysAhead}`);
}

export function rescheduleAppointment(id: string, slotDatetime: string): Promise<{ ok: boolean }> {
  return request(`/appointments/${id}/reschedule`, {
    method: "POST",
    body: JSON.stringify({ slot_datetime: slotDatetime }),
  });
}

export function cancelAppointment(id: string, reason?: string): Promise<{ ok: boolean }> {
  return request(`/appointments/${id}`, {
    method: "PATCH",
    body: JSON.stringify({ status: "cancelled", reason: reason || null }),
  });
}

export type LifecycleStatus = "checked_in" | "completed" | "no_show" | "cancelled";

export function setAppointmentStatus(
  id: string,
  status: LifecycleStatus,
  reason?: string,
): Promise<{ ok: boolean }> {
  return request(`/appointments/${id}/status`, {
    method: "POST",
    body: JSON.stringify({ status, reason: reason || null }),
  });
}

export async function getAppointmentEvents(id: string): Promise<AppointmentEvent[]> {
  const rows = await request<Array<Record<string, unknown>>>(`/appointments/${id}/events`);
  return rows.map((r) => ({
    id: r.id as number,
    event_type: r.event_type as string,
    from_status: (r.from_status as string) ?? null,
    to_status: (r.to_status as string) ?? null,
    from_time: r.from_time ? new Date(r.from_time as string) : null,
    to_time: r.to_time ? new Date(r.to_time as string) : null,
    actor_user_id: (r.actor_user_id as number) ?? null,
    actor_role: (r.actor_role as string) ?? "",
    actor_email: (r.actor_email as string) ?? null,
    note: (r.note as string) ?? null,
    created_at: new Date(r.created_at as string),
  }));
}

// --- Reports / analytics ---

export type ReportSummary = {
  date_from: string;
  date_to: string;
  appointments: {
    total: number;
    status_counts: Record<string, number>;
    completed: number;
    no_show: number;
    cancelled: number;
    no_show_rate: number;
    completion_rate: number;
    per_doctor: { doctor_id: number | null; name: string; count: number }[];
    daily: { day: string; count: number }[];
  };
  sms: {
    by_status: Record<string, number>;
    by_kind: { kind: string; count: number }[];
  };
  channels: { id: number; identifier: string; label: string | null; calls_received: number; appointments_taken: number }[];
};

export function getReportSummary(dateFrom?: string, dateTo?: string): Promise<ReportSummary> {
  const params = new URLSearchParams();
  if (dateFrom) params.set("date_from", dateFrom);
  if (dateTo) params.set("date_to", dateTo);
  const qs = params.toString();
  return request(`/reports/summary${qs ? `?${qs}` : ""}`);
}

// --- Schedule ---

type ScheduleApiRow = Omit<ScheduleRow, "active">;

export async function getSchedule(): Promise<ScheduleRow[]> {
  const rows = await request<ScheduleApiRow[]>("/schedule");
  const byDay = new Map(rows.map((r) => [r.day_of_week, r]));
  // Materialise all 7 days; days without a row are inactive.
  return Array.from({ length: 7 }, (_, dow) => {
    const r = byDay.get(dow);
    return r
      ? {
          ...r,
          start_time: String(r.start_time).slice(0, 5),
          end_time: String(r.end_time).slice(0, 5),
          active: true,
        }
      : {
          day_of_week: dow,
          start_time: "09:00",
          end_time: "17:00",
          slot_duration: 30,
          active: false,
        };
  });
}

// --- Doctors ---

export type Doctor = {
  id: number;
  clinic_id: number;
  name: string;
  specialty: string;
  degrees: string;
  description: string;
  phone: string;
  is_primary: boolean;
  has_photo: boolean;
  /** Consultation fees in ৳; null = not set (portal shows "নির্ধারিত হয়নি"). */
  fee_new: number | null;
  fee_followup: number | null;
  created_at: string;
};

export type DoctorInput = {
  name?: string;
  specialty?: string;
  degrees?: string;
  description?: string;
  phone?: string;
  is_primary?: boolean;
  /** Explicit null clears the fee; omit to leave unchanged. */
  fee_new?: number | null;
  fee_followup?: number | null;
};

export function listDoctors(): Promise<Doctor[]> {
  return request("/doctors");
}

export function addDoctor(input: DoctorInput): Promise<Doctor> {
  return request("/doctors", { method: "POST", body: JSON.stringify(input) });
}

export function updateDoctor(id: number, patch: DoctorInput): Promise<Doctor> {
  return request(`/doctors/${id}`, { method: "PATCH", body: JSON.stringify(patch) });
}

export function deleteDoctor(id: number): Promise<void> {
  return request(`/doctors/${id}`, { method: "DELETE" });
}

/** Upload/replace a doctor's profile photo (jpeg/png/webp, max 2 MB).
 *  Multipart, so it can't go through `request` (which forces JSON). */
export async function uploadDoctorPhoto(id: number, file: File): Promise<void> {
  const token = getToken();
  const form = new FormData();
  form.append("file", file);
  const res = await fetch(`${API}/doctors/${id}/photo`, {
    method: "PUT",
    headers: token ? { Authorization: `Bearer ${token}` } : undefined,
    body: form,
  });
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    let message = text || `HTTP ${res.status}`;
    try {
      const json = JSON.parse(text);
      if (typeof json.detail === "string") message = json.detail;
    } catch { /* not JSON — keep raw text */ }
    throw new ApiError(res.status, message);
  }
}

export function deleteDoctorPhoto(id: number): Promise<void> {
  return request(`/doctors/${id}/photo`, { method: "DELETE" });
}

/** URL for a doctor photo <img src>. The endpoint is public (an <img> can't
 *  send the Bearer header). `version` cache-busts after an upload/removal. */
export function doctorPhotoUrl(id: number, version?: number): string {
  return `${API}/doctors/${id}/photo${version ? `?v=${version}` : ""}`;
}

// --- Reviews (admin moderation, scoped to the logged-in clinic) ---

export type AdminReview = {
  id: number;
  doctor_id: number;
  doctor_name: string;
  account_id: number;
  reviewer_name: string;
  rating: number;
  text: string;
  status: "published" | "hidden";
  created_at: string;
  updated_at: string;
};

export function listClinicReviews(status?: "published" | "hidden"): Promise<AdminReview[]> {
  return request(`/reviews${status ? `?status=${status}` : ""}`);
}

export function setReviewStatus(id: number, status: "published" | "hidden"): Promise<void> {
  return request(`/reviews/${id}`, {
    method: "PATCH",
    body: JSON.stringify({ status }),
  });
}

// --- Integrations & Channels ---

export type IntegrationItem = {
  key: string;
  name: string;
  configured: boolean;
  detail: string;
};

export type Channel = {
  id: number;
  clinic_id: number | null;
  hospital_id: number | null;
  kind: "web" | "whatsapp" | "sms" | "voice" | "voice_sip" | "phone" | "voice_ivr";
  identifier: string;
  label: string | null;
  created_at: string;
};

export function getIntegrations(): Promise<{ items: IntegrationItem[] }> {
  return request("/integrations");
}

export function listChannels(): Promise<Channel[]> {
  return request("/channels");
}

export type ChannelStats = {
  id: number;
  identifier: string;
  label: string | null;
  calls_received: number;
  appointments_taken: number;
};

export function getChannelStats(): Promise<ChannelStats[]> {
  return request("/channels/stats");
}

export function addChannel(input: {
  kind: Channel["kind"];
  identifier: string;
  label?: string;
}): Promise<Channel> {
  return request("/channels", { method: "POST", body: JSON.stringify(input) });
}

export function deleteChannel(id: number): Promise<void> {
  return request(`/channels/${id}`, { method: "DELETE" });
}

// --- Conversations ---

export type ConversationSummary = {
  session_id: string;
  channel: string;
  turns: number;
  last_text: string;
  started_at: string;
  last_at: string;
};

export type ConversationMessage = {
  role: "user" | "assistant";
  text: string;
  channel: string;
  created_at: string;
};

export function listConversations(): Promise<ConversationSummary[]> {
  return request("/conversations");
}

export function replyToConversation(sessionId: string, text: string): Promise<{ ok: boolean }> {
  return request(`/conversations/${encodeURIComponent(sessionId)}/reply`, {
    method: "POST",
    body: JSON.stringify({ text }),
  });
}

/** Ask the AI to compose a suggested staff reply from the conversation context.
 *  Returns the draft for the staff member to edit — never auto-sent. */
export function draftReply(sessionId: string): Promise<{ draft: string }> {
  return request(`/conversations/${encodeURIComponent(sessionId)}/draft`, {
    method: "POST",
  });
}

// --- Escalations (agent → human handoff queue) ---

export type Escalation = {
  id: number;
  clinic_id: number | null;
  session_id: string;
  channel: string;
  reason: string;
  status: "open" | "resolved";
  created_at: string;
  resolved_at: string | null;
};

export function listEscalations(status: "open" | "resolved" | "" = "open"): Promise<Escalation[]> {
  return request(`/escalations?status=${status}`);
}

export function resolveEscalation(id: number): Promise<{ ok: boolean }> {
  return request(`/escalations/${id}`, { method: "PATCH" });
}

export function getConversation(sessionId: string): Promise<ConversationMessage[]> {
  return request(`/conversations/${encodeURIComponent(sessionId)}`);
}

// --- Schedule (cont.) ---

export function saveSchedule(rows: ScheduleRow[]): Promise<unknown> {
  const active = rows
    .filter((r) => r.active)
    .map(({ day_of_week, start_time, end_time, slot_duration }) => ({
      day_of_week,
      start_time,
      end_time,
      slot_duration,
    }));
  return request("/schedule", { method: "PUT", body: JSON.stringify(active) });
}

// --- Hospitals ---

export type Hospital = {
  id: number;
  slug: string;
  name: string;
  address: string;
  license_number: string;
  timezone: string;
  status: string;
  created_at: string;
};

export type Department = {
  id: number;
  slug: string;
  name: string;
  doctor_name: string;
  specialty_code: string;
  floor: string;
  phone_ext: string;
};

export function listHospitals(): Promise<Hospital[]> {
  return request("/hospitals");
}

export function getHospital(id: number): Promise<Hospital> {
  return request(`/hospitals/${id}`);
}

export function createHospital(body: {
  slug: string;
  name: string;
  address?: string;
  license_number?: string;
  timezone?: string;
}): Promise<Hospital> {
  return request("/hospitals", { method: "POST", body: JSON.stringify(body) });
}

export type HospitalUpdate = {
  name?: string;
  address?: string;
  license_number?: string;
  timezone?: string;
  status?: string;
};

export function updateHospital(id: number, patch: HospitalUpdate): Promise<Hospital> {
  return request(`/hospitals/${id}`, { method: "PATCH", body: JSON.stringify(patch) });
}

// --- Outbound SMS log (Messages view) ---

export type SmsLogRow = {
  id: number;
  clinic_id: number | null;
  to_number: string;
  body: string;
  kind: string;
  status: string;
  provider: string | null;
  error: string | null;
  created_at: string;
};

export function listMessages(params: { kind?: string; status?: string } = {}): Promise<SmsLogRow[]> {
  const qs = new URLSearchParams();
  if (params.kind) qs.set("kind", params.kind);
  if (params.status) qs.set("status", params.status);
  const suffix = qs.toString() ? `?${qs}` : "";
  return request(`/messages${suffix}`);
}

export type SmsTemplateDefaults = {
  defaults: Required<SmsTemplates>;
  placeholders: Record<string, string[]>;
};

export function getSmsTemplateDefaults(): Promise<SmsTemplateDefaults> {
  return request("/messages/templates");
}

export function resendMessage(id: number): Promise<{ ok: boolean }> {
  return request(`/messages/${id}/resend`, { method: "POST" });
}

export function listDepartments(hospitalId: number): Promise<Department[]> {
  return request(`/hospitals/${hospitalId}/departments`);
}

export function createDepartment(
  hospitalId: number,
  body: {
    slug: string;
    name: string;
    doctor_name?: string;
    specialty_code?: string;
    floor?: string;
    phone_ext?: string;
  }
): Promise<Department> {
  return request(`/hospitals/${hospitalId}/departments`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

// --- Patients ---

export type Patient = {
  id: number;
  hospital_id: number;
  mrn: string;
  name: string;
  phone: string;
  age: number | null;
  gender: "male" | "female" | "other" | null;
  created_at: string;
};

export function listPatients(q?: string, hospitalId?: number): Promise<Patient[]> {
  const params = new URLSearchParams();
  if (q) params.set("q", q);
  if (hospitalId) params.set("hospital_id", String(hospitalId));
  const qs = params.size ? `?${params}` : "";
  return request(`/patients${qs}`);
}

export function getPatient(mrn: string, hospitalId?: number): Promise<Patient> {
  const qs = hospitalId ? `?hospital_id=${hospitalId}` : "";
  return request(`/patients/${encodeURIComponent(mrn)}${qs}`);
}

export function registerPatient(body: {
  name: string;
  phone: string;
  age?: number;
  gender?: "male" | "female" | "other";
}, hospitalId?: number): Promise<Patient> {
  const qs = hospitalId ? `?hospital_id=${hospitalId}` : "";
  return request(`/patients${qs}`, { method: "POST", body: JSON.stringify(body) });
}

// --- Queue / Tokens ---

export type Token = {
  id: number;
  appointment_id: string;
  hospital_id: number;
  department_id: number;
  doctor_id: number | null;
  token_date: string;
  token_number: number;
  token_prefix: string;
  status: "waiting" | "called" | "in_progress" | "completed" | "skipped";
  patient_name: string | null;
  patient_mobile: string | null;
  called_at: string | null;
  completed_at: string | null;
  created_at: string;
};

export type QueueStatus = {
  current_token: number | null;
  waiting_count: number;
  tokens: Token[];
};

export function getTodayQueue(departmentId: number): Promise<QueueStatus> {
  return request(`/queue/${departmentId}/today`);
}

export function callToken(tokenId: number): Promise<Token> {
  return request(`/queue/${tokenId}/call`, { method: "POST" });
}

export function completeToken(tokenId: number): Promise<{ ok: boolean }> {
  return request(`/queue/${tokenId}/complete`, { method: "POST" });
}

// --- Audit Log ---

export type AuditEntry = {
  id: number;
  hospital_id: number | null;
  clinic_id: number | null;
  user_id: number | null;
  actor_role: string;
  actor_email: string | null;
  action: string;
  entity_type: string;
  entity_id: string | null;
  old_value: Record<string, unknown> | null;
  new_value: Record<string, unknown> | null;
  ip_address: string | null;
  created_at: string;
};

export type AuditFilters = {
  entity_type?: string;
  action?: string;
  user_id?: number;
  date_from?: string;
  date_to?: string;
};

export function getAuditLog(filters: AuditFilters = {}): Promise<AuditEntry[]> {
  const params = new URLSearchParams();
  if (filters.entity_type) params.set("entity_type", filters.entity_type);
  if (filters.action) params.set("action", filters.action);
  if (filters.user_id !== undefined) params.set("user_id", String(filters.user_id));
  if (filters.date_from) params.set("date_from", filters.date_from);
  if (filters.date_to) params.set("date_to", filters.date_to);
  const qs = params.toString();
  return request(`/audit${qs ? `?${qs}` : ""}`);
}

export function getAuditActions(): Promise<{ actions: string[]; entity_types: string[] }> {
  return request(`/audit/actions`);
}

// =========================================================================== //
// Patient self-service portal — separate auth (its own token) and API surface.
// =========================================================================== //

export const API_BASE = API;
const PATIENT_TOKEN_KEY = "patient_token";

export function getPatientToken(): string | null {
  if (typeof window === "undefined") return null;
  return window.localStorage.getItem(PATIENT_TOKEN_KEY);
}
export function setPatientToken(token: string): void {
  window.localStorage.setItem(PATIENT_TOKEN_KEY, token);
}
export function clearPatientToken(): void {
  window.localStorage.removeItem(PATIENT_TOKEN_KEY);
}

async function patientRequest<T>(path: string, init: RequestInit = {}): Promise<T> {
  const token = getPatientToken();
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(init.headers as Record<string, string> | undefined),
  };
  if (token) headers.Authorization = `Bearer ${token}`;

  const res = await fetch(`${API}${path}`, { ...init, headers });
  if (res.status === 401) {
    clearPatientToken();
    throw new ApiError(401, "Unauthorized");
  }
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    let message = text || `HTTP ${res.status}`;
    try {
      const json = JSON.parse(text);
      if (typeof json.detail === "string") message = json.detail;
      else if (Array.isArray(json.detail))
        message = json.detail.map((e: { msg: string }) => e.msg).join("; ");
    } catch { /* not JSON — keep raw text */ }
    throw new ApiError(res.status, message);
  }
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

export type PatientAccount = {
  id: number;
  email: string;
  name: string;
  phone: string;
  created_at: string;
  /** Freemium plan. `tier` is the effective access level: premium|trial|free. */
  plan: string;
  tier: string;
  trial_ends_at: string | null;
  premium_until: string | null;
  agent_bookings_used: number;
  /** -1 = unlimited (premium/trial); otherwise the free monthly cap. */
  agent_bookings_cap: number;
  subscription_fee: number;
  /** One-time SMS-OTP phone verification — unlocks calling the platform number. */
  phone_verified: boolean;
};

export type PatientAppointment = {
  id: string;
  hospital_id: number | null;
  hospital_name: string | null;
  clinic_id: number | null;
  department_name: string | null;
  doctor_id: number | null;
  doctor_name: string | null;
  patient_name: string;
  patient_mobile: string;
  scheduled_at: string;
  duration_mins: number;
  status: string;
  serial_number: number | null;
  created_at: string;
  payment_expires_at: string | null;
};

export async function patientSignup(body: {
  email: string;
  password: string;
  name: string;
  phone: string;
}): Promise<void> {
  const data = await patientRequest<{ access_token: string; account_id: number }>(
    "/patient/signup",
    { method: "POST", body: JSON.stringify(body) }
  );
  setPatientToken(data.access_token);
}

export async function patientLogin(email: string, password: string): Promise<void> {
  const data = await patientRequest<{ access_token: string; account_id: number }>(
    "/patient/login",
    { method: "POST", body: JSON.stringify({ email, password }) }
  );
  setPatientToken(data.access_token);
}

export function getPatientMe(): Promise<PatientAccount> {
  return patientRequest<PatientAccount>("/patient/me");
}

export function portalForgotPassword(identifier: string): Promise<{ ok: boolean }> {
  return patientRequest("/patient/password/forgot", {
    method: "POST",
    body: JSON.stringify({ identifier }),
  });
}

/** SMS a one-time verification code to the patient's phone (premium calling). */
export function portalPhoneVerifyStart(phone: string): Promise<{ ok: boolean }> {
  return patientRequest("/patient/phone/verify/start", {
    method: "POST",
    body: JSON.stringify({ phone }),
  });
}

export function portalPhoneVerifyConfirm(
  code: string
): Promise<{ ok: boolean; phone: string; phone_verified: boolean }> {
  return patientRequest("/patient/phone/verify/confirm", {
    method: "POST",
    body: JSON.stringify({ code }),
  });
}

export async function portalResetPassword(
  identifier: string, code: string, newPassword: string
): Promise<void> {
  const data = await patientRequest<{ access_token: string; account_id: number }>(
    "/patient/password/reset",
    { method: "POST", body: JSON.stringify({ identifier, code, new_password: newPassword }) }
  );
  setPatientToken(data.access_token);
}

export function portalListHospitals(): Promise<Hospital[]> {
  return patientRequest<Hospital[]>("/patient/hospitals");
}

export function portalListDepartments(hospitalId: number): Promise<Department[]> {
  return patientRequest<Department[]>(`/patient/hospitals/${hospitalId}/departments`);
}

export function portalListDoctors(clinicId: number): Promise<Doctor[]> {
  return patientRequest<Doctor[]>(`/patient/departments/${clinicId}/doctors`);
}

// --- Marketplace: cross-hospital doctor search + reviews (foodpanda-style) ---

export type Specialty = { specialty: string; doctor_count: number };

export type SearchDoctor = {
  id: number;
  clinic_id: number;
  name: string;
  specialty: string;
  degrees: string;
  description: string;
  has_photo: boolean;
  fee_new: number | null;
  fee_followup: number | null;
  department_name: string;
  hospital_id: number;
  hospital_name: string;
  avg_rating: number;
  review_count: number;
  next_slot: ChatSlot | null;
};

export type DoctorDetail = SearchDoctor & { slots: ChatSlot[] };

export type Review = {
  id: number;
  rating: number;
  text: string;
  reviewer_name: string;
  created_at: string;
  updated_at: string;
};

export type MyReview = {
  id: number;
  doctor_id: number;
  rating: number;
  text: string;
  status: "published" | "hidden";
  created_at: string;
  updated_at: string;
};

export type DoctorSort = "rating" | "fee" | "available";

export function portalListSpecialties(): Promise<Specialty[]> {
  return patientRequest<Specialty[]>("/patient/specialties");
}

export function portalSearchDoctors(params: {
  q?: string;
  specialty?: string;
  hospitalId?: number;
  maxFee?: number;
  sort?: DoctorSort;
  page?: number;
} = {}): Promise<SearchDoctor[]> {
  const qs = new URLSearchParams();
  if (params.q) qs.set("q", params.q);
  if (params.specialty) qs.set("specialty", params.specialty);
  if (params.hospitalId) qs.set("hospital_id", String(params.hospitalId));
  if (params.maxFee !== undefined) qs.set("max_fee", String(params.maxFee));
  if (params.sort) qs.set("sort", params.sort);
  if (params.page) qs.set("page", String(params.page));
  const suffix = qs.size ? `?${qs}` : "";
  return patientRequest<SearchDoctor[]>(`/patient/doctors/search${suffix}`);
}

export function portalDoctorDetail(doctorId: number): Promise<DoctorDetail> {
  return patientRequest<DoctorDetail>(`/patient/doctors/${doctorId}`);
}

export type PaymentPrompt = {
  payment_id: string;
  amount: number;
  currency: string;
  /** null when the manual provider auto-paid — nothing left to do. */
  pay_url: string | null;
  expires_at: string | null;
};

export type DirectBookingResult = {
  id: string;
  serial_number: number | null;
  slot_label: string;
  /** "confirmed" | "pending_payment" */
  status: string;
  payment: PaymentPrompt | null;
};

/** Direct booking (no agent): the patient tapped a slot on a doctor page.
 *  409 = the slot was taken meanwhile (refetch slots and re-pick). When a
 *  booking fee applies, `status` comes back "pending_payment" with a
 *  `payment` prompt instead of confirming immediately. */
export function portalBookAppointment(body: {
  clinic_id: number;
  doctor_id?: number;
  slot_datetime: string;
  slot_label: string;
  patient_name: string;
  patient_age: number;
  patient_mobile: string;
}): Promise<DirectBookingResult> {
  return patientRequest(`/patient/appointments`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export type PatientPayment = {
  id: string;
  status: string;   // "initiated" | "paid" | "failed" | "refunded" | "expired"
  amount: number;
  currency: string;
  appointment_id: string | null;
  appointment_status: string | null;
};

/** Poll a payment's status — the booking sheet's "waiting for payment" step. */
export function portalGetPayment(paymentId: string): Promise<PatientPayment> {
  return patientRequest<PatientPayment>(`/patient/payments/${paymentId}`);
}

/** Re-initiate payment for a still-held appointment (my-appointments'
 *  "Pay now" — e.g. the patient closed the gateway tab the first time). */
export function portalPayAgain(appointmentId: string): Promise<PaymentPrompt> {
  return patientRequest<PaymentPrompt>(`/patient/appointments/${appointmentId}/pay`, {
    method: "POST",
  });
}

export function portalDoctorReviews(doctorId: number): Promise<Review[]> {
  return patientRequest<Review[]>(`/patient/doctors/${doctorId}/reviews`);
}

/** The logged-in patient's own review of this doctor (null if none yet). */
export function portalMyReview(doctorId: number): Promise<MyReview | null> {
  return patientRequest<MyReview | null>(`/patient/doctors/${doctorId}/review`);
}

/** Create or update a review. 403 unless the patient had a real appointment
 *  with this doctor (completed, or a confirmed one already in the past). */
export function portalSubmitReview(
  doctorId: number, rating: number, text: string
): Promise<MyReview> {
  return patientRequest<MyReview>(`/patient/doctors/${doctorId}/review`, {
    method: "PUT",
    body: JSON.stringify({ rating, text }),
  });
}

export type MyAppointments = {
  items: PatientAppointment[];
  /** True when older bookings are hidden behind the free-tier history limit. */
  truncated: boolean;
  total: number;
};

export function listMyAppointments(): Promise<MyAppointments> {
  return patientRequest<MyAppointments>("/patient/appointments");
}

export type SubscriptionResult = {
  tier: string;
  premium_until: string | null;
  payment: PaymentPrompt | null;
};

/** Start (or renew) a premium subscription. When the manual provider auto-pays,
 *  `payment.pay_url` is null and `tier` is already "premium". */
export function portalSubscribe(): Promise<SubscriptionResult> {
  return patientRequest<SubscriptionResult>("/patient/subscription/checkout", {
    method: "POST",
  });
}

export function portalCancelAppointment(id: string): Promise<{ ok: boolean }> {
  return patientRequest<{ ok: boolean }>(`/patient/appointments/${id}/cancel`, {
    method: "POST",
  });
}

export function portalDepartmentAvailability(clinicId: number): Promise<Slot[]> {
  return patientRequest<Slot[]>(`/patient/departments/${clinicId}/availability`);
}

export function portalRescheduleAppointment(id: string, slotDatetime: string): Promise<{ ok: boolean }> {
  return patientRequest<{ ok: boolean }>(`/patient/appointments/${id}/reschedule`, {
    method: "POST",
    body: JSON.stringify({ slot_datetime: slotDatetime }),
  });
}

export type ChatSlot = { label: string; datetime: string };
/** Pending agent confirm question (cancel/reschedule) awaiting yes/no. */
export type ChatConfirm = {
  kind: string;
  question: string;
  appointment?: {
    id: string;
    label: string;
    doctor_name?: string | null;
    serial_number?: number | null;
  };
  slot_label?: string;
};
export type ChatHistoryMessage = {
  role: "user" | "assistant";
  text: string;
  /** Tappable slot picker re-attached to the last assistant turn, if pending. */
  slots?: ChatSlot[];
  /** Pending confirm card re-attached after a reload, if the thread is paused. */
  confirm?: ChatConfirm;
};

/** Deterministic "pay the booking fee" card streamed by the agent when a held
 *  booking needs an online payment before it confirms. The URL never passes
 *  through the LLM — it arrives as a `payment` custom stream event. */
export type ChatPayment = {
  appointment_id: string;
  payment_id: string;
  amount: number;
  currency: string;
  pay_url: string | null;
  expires_at: string | null;
};

/** `clinicId = "platform"` targets the marketplace-wide assistant thread. */
export function getChatHistory(clinicId: number | "platform"): Promise<ChatHistoryMessage[]> {
  return patientRequest<ChatHistoryMessage[]>(`/patient/chat/history/${clinicId}`);
}

/** Delete this patient+clinic (or platform) thread so the next chat starts fresh. */
export function clearChatHistory(clinicId: number | "platform"): Promise<{ ok: boolean }> {
  return patientRequest<{ ok: boolean }>(`/patient/chat/history/${clinicId}`, {
    method: "DELETE",
  });
}

/** Fire-and-forget: heat the LLM's prompt cache for this department while the
 *  patient is still on the doctor step, so the chat greeting starts fast.
 *  Pass `doctorId` once a doctor is picked — the doctor section of the prompt
 *  differs, so a doctor-less warm doesn't match a pre-selected-doctor turn.
 *  Omit `clinicId` to warm the platform-wide assistant (marketplace home).
 *  Failures are irrelevant (first turn is just cold), hence swallowed. */
export function prewarmChat(clinicId?: number, doctorId?: number): void {
  patientRequest<{ ok: boolean }>("/patient/chat/prewarm", {
    method: "POST",
    body: JSON.stringify({ clinic_id: clinicId, doctor_id: doctorId }),
  }).catch(() => {});
}

/** The ONE conversation thread for this account — every chat and voice
 *  session, from any page, continues it (matches backend
 *  _platform_session_id / _voice_session_id). Display-only on the client:
 *  the server derives the real thread id from the JWT. */
export function stablePlatformSessionId(accountId: number): string {
  return `pt-acc${accountId}-platform`;
}

// --- Browser voice call (LiveKit) ---

export type VoiceToken = {
  /** LiveKit server URL to connect to. */
  serverUrl: string;
  /** Short-lived join token; the agent is dispatched into the room. */
  token: string;
  /** The unique room name minted for this call. */
  roomName: string;
};

/**
 * Mint a LiveKit room token for a patient voice call. Pass `clinicId` for a
 * department-level call, `hospitalId` for a hospital-level ("talk to us")
 * call, or neither for the platform-wide assistant (marketplace home).
 */
export async function portalVoiceToken(
  args: { clinicId?: number; hospitalId?: number; doctorId?: number },
): Promise<VoiceToken> {
  const res = await patientRequest<{
    server_url: string;
    participant_token: string;
    room_name: string;
  }>("/patient/voice/token", {
    method: "POST",
    body: JSON.stringify({
      clinic_id: args.clinicId,
      hospital_id: args.hospitalId,
      doctor_id: args.doctorId,
    }),
  });
  return { serverUrl: res.server_url, token: res.participant_token, roomName: res.room_name };
}

// --- Hospital documents (RAG) ---

export type HospitalDocument = {
  id: number;
  hospital_id: number;
  filename: string;
  content_type: string;
  chunk_count: number;
  created_at: string;
};

export function listHospitalDocuments(hospitalId: number): Promise<HospitalDocument[]> {
  return request<HospitalDocument[]>(`/hospitals/${hospitalId}/documents`);
}

export async function uploadHospitalDocument(
  hospitalId: number,
  file: File
): Promise<HospitalDocument> {
  const token = getToken();
  const form = new FormData();
  form.append("file", file);
  const res = await fetch(`${API}/hospitals/${hospitalId}/documents`, {
    method: "POST",
    headers: token ? { Authorization: `Bearer ${token}` } : {},
    body: form,
  });
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    let message = text || `HTTP ${res.status}`;
    try { message = JSON.parse(text).detail ?? message; } catch { /* */ }
    throw new ApiError(res.status, message);
  }
  return res.json();
}

export async function deleteHospitalDocument(
  hospitalId: number,
  docId: number
): Promise<void> {
  const token = getToken();
  const res = await fetch(`${API}/hospitals/${hospitalId}/documents/${docId}`, {
    method: "DELETE",
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  if (!res.ok && res.status !== 204) {
    const text = await res.text().catch(() => "");
    let message = text || `HTTP ${res.status}`;
    try { message = JSON.parse(text).detail ?? message; } catch { /* */ }
    throw new ApiError(res.status, message);
  }
}
