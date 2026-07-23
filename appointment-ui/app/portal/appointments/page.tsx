"use client";

import { useCallback, useEffect, useRef, useState } from "react";
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
  Star,
  Sparkles,
  X,
} from "lucide-react";
import {
  MyReview,
  PatientAppointment,
  listMyAppointments,
  portalCancelAppointment,
  portalDepartmentAvailability,
  portalMyReview,
  portalPayAgain,
  portalRescheduleAppointment,
} from "@/lib/api";
import { useToast } from "@/lib/toast";
import { LangToggle, StringKey, useLang } from "@/lib/i18n";
import ConfirmDialog from "@/components/ui/ConfirmDialog";
import RescheduleModal from "@/components/appointments/RescheduleModal";
import ReviewModal from "@/components/portal/ReviewModal";

type TFn = ReturnType<typeof useLang>["t"];

/** Live-ticking mm:ss remaining until `expiresAt`; null once elapsed/absent. */
function useCountdown(expiresAt: string | null | undefined): string | null {
  const [label, setLabel] = useState<string | null>(null);
  useEffect(() => {
    if (!expiresAt) { setLabel(null); return; }
    const target = new Date(expiresAt).getTime();
    const tick = () => {
      const ms = target - Date.now();
      if (ms <= 0) { setLabel(null); return false; }
      const s = Math.floor(ms / 1000);
      setLabel(`${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`);
      return true;
    };
    if (!tick()) return;
    const id = setInterval(() => { if (!tick()) clearInterval(id); }, 1000);
    return () => clearInterval(id);
  }, [expiresAt]);
  return label;
}

function fmt(iso: string, locale: string) {
  return new Date(iso).toLocaleString(locale, {
    day: "2-digit",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/** Human-friendly relative day label, e.g. "Today", "Tomorrow", "in 3 days". */
function relativeDay(iso: string, t: TFn): string {
  const d = new Date(iso);
  const today = new Date();
  const startOf = (x: Date) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
  const days = Math.round((startOf(d) - startOf(today)) / 86_400_000);
  if (days === 0) return t("today");
  if (days === 1) return t("tomorrow");
  if (days === -1) return t("yesterday");
  if (days > 1) return t("inDays", { n: days });
  return t("daysAgo", { n: Math.abs(days) });
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

const STATUS_KEY: Record<string, StringKey> = {
  confirmed: "statusConfirmed",
  completed: "statusCompleted",
  checked_in: "statusCheckedIn",
  cancelled: "statusCancelled",
  no_show: "statusNoShow",
  pending_payment: "paymentPendingBadge",
};

function StatusBadge({ status }: { status: string }) {
  const { t } = useLang();
  const cls =
    status === "confirmed" || status === "completed" || status === "checked_in"
      ? "badge-success"
      : status === "cancelled" || status === "no_show"
      ? "badge-danger"
      : "badge-warning";
  const key = STATUS_KEY[status];
  return (
    <span className={`badge capitalize ${cls}`}>
      {key ? t(key) : status.replace("_", " ")}
    </span>
  );
}

/** A review is allowed for visits that actually happened: marked completed by
 *  the front desk, or a confirmed/checked-in appointment already in the past
 *  (many desks never press "completed"). Mirrors the backend eligibility. */
function canReview(a: PatientAppointment): boolean {
  if (a.doctor_id == null) return false;
  if (a.status === "completed") return true;
  return (
    (a.status === "confirmed" || a.status === "checked_in") &&
    new Date(a.scheduled_at).getTime() < Date.now()
  );
}

function AppointmentCard({
  a,
  idx,
  canCancel,
  onCancel,
  onReschedule,
  onReview,
  onPay,
  paying,
}: {
  a: PatientAppointment;
  idx: number;
  canCancel: boolean;
  onCancel: () => void;
  onReschedule?: () => void;
  onReview?: () => void;
  onPay?: () => void;
  paying?: boolean;
}) {
  const { t, dateLocale } = useLang();
  const countdown = useCountdown(a.status === "pending_payment" ? a.payment_expires_at : null);
  const held = a.status === "pending_payment" && countdown !== null;
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
              <span className="font-semibold text-fg">{relativeDay(a.scheduled_at, t)}</span>
              <span className="text-faint">·</span>
              {fmt(a.scheduled_at, dateLocale)}
            </div>
            {a.serial_number != null && (
              <span className="rounded-full border border-border bg-surface-3 px-2.5 py-0.5 text-xs font-semibold text-faint">
                {t("serialN", { n: a.serial_number })}
              </span>
            )}
          </div>

          {/* Held booking — needs payment to confirm */}
          {a.status === "pending_payment" && (
            <div className="mt-3 flex flex-wrap items-center gap-2 border-t border-border pt-3">
              <span className="text-xs text-muted">
                {held ? `${t("payHoldNote", { m: countdown as string })}` : t("payExpired")}
              </span>
              {onPay && held && (
                <button
                  onClick={onPay}
                  disabled={paying}
                  className="ml-auto inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-bold text-white shadow transition-all hover:opacity-90 active:scale-95 disabled:opacity-60"
                  style={{ background: "var(--brand-grad)" }}
                >
                  {paying ? t("payWaiting") : t("payNowCta")}
                </button>
              )}
            </div>
          )}

          {/* Actions */}
          {(a.status === "confirmed" || onReview) && (
            <div className="mt-3 flex flex-wrap items-center gap-2 border-t border-border pt-3">
              {onReview && (
                <button
                  onClick={onReview}
                  className="inline-flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-xs font-semibold text-primary transition-colors hover:bg-[var(--brand-soft)]"
                >
                  <Star size={13} /> {t("writeReview")}
                </button>
              )}
              {a.status === "confirmed" && (
                <button
                  onClick={() => downloadIcs(a)}
                  className="inline-flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-xs font-semibold text-muted transition-colors hover:bg-surface-3 hover:text-fg"
                >
                  <CalendarPlus size={13} /> {t("addToCalendar")}
                </button>
              )}
              {canCancel && onReschedule && (
                <button
                  onClick={onReschedule}
                  className="inline-flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-xs font-semibold text-muted transition-colors hover:bg-surface-3 hover:text-fg"
                >
                  <CalendarClock size={13} /> {t("reschedule")}
                </button>
              )}
              {canCancel && a.status === "confirmed" && (
                <button
                  onClick={onCancel}
                  className="inline-flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-xs font-semibold text-muted transition-colors hover:bg-danger/10 hover:text-danger"
                >
                  <X size={13} /> {t("cancel")}
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
  const { t, dateLocale } = useLang();
  const [rows, setRows] = useState<PatientAppointment[]>([]);
  const [truncated, setTruncated] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [pendingCancel, setPendingCancel] = useState<PatientAppointment | null>(null);
  const [cancelling, setCancelling] = useState(false);
  const [pendingReschedule, setPendingReschedule] = useState<PatientAppointment | null>(null);
  // Review modal target + the patient's existing review of that doctor (prefill).
  const [reviewFor, setReviewFor] = useState<PatientAppointment | null>(null);
  const [existingReview, setExistingReview] = useState<MyReview | null>(null);
  // Which held appointment is mid-payment (re-initiating + polling for confirm).
  const [payingId, setPayingId] = useState<string | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  async function openReview(a: PatientAppointment) {
    if (a.doctor_id == null) return;
    let existing: MyReview | null = null;
    try { existing = await portalMyReview(a.doctor_id); } catch { /* fresh form */ }
    setExistingReview(existing);
    setReviewFor(a);
  }

  const load = useCallback((withSpinner = false) => {
    if (withSpinner) setLoading(true);
    return listMyAppointments()
      .then((r) => { setRows(r.items); setTruncated(r.truncated); setError(""); })
      .catch((e) => setError(e instanceof Error ? e.message : t("apptsLoadFailed")))
      .finally(() => setLoading(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
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
      toast.success(t("apptCancelled"));
      setPendingCancel(null);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : t("apptCancelFailed"));
    } finally {
      setCancelling(false);
    }
  }

  // Clear any live poll on unmount.
  useEffect(() => () => { if (pollRef.current) clearInterval(pollRef.current); }, []);

  /** Re-open the gateway for a held booking, then poll until it confirms
   *  (or the manual provider auto-paid and it's already confirmed). */
  async function handlePay(a: PatientAppointment) {
    setPayingId(a.id);
    try {
      const prompt = await portalPayAgain(a.id);
      if (prompt.pay_url) window.open(prompt.pay_url, "_blank", "noopener,noreferrer");
      // Poll the list until this appointment is no longer held, or give up after ~2min.
      if (pollRef.current) clearInterval(pollRef.current);
      let ticks = 0;
      pollRef.current = setInterval(async () => {
        ticks += 1;
        const fresh = await listMyAppointments().catch(() => null);
        if (fresh) {
          setRows(fresh.items);
          setTruncated(fresh.truncated);
          const still = fresh.items.find((x) => x.id === a.id);
          if (!still || still.status !== "pending_payment") {
            if (still?.status === "confirmed") toast.success(t("paySuccess"));
            if (pollRef.current) clearInterval(pollRef.current);
            pollRef.current = null;
            setPayingId(null);
          }
        }
        if (ticks >= 40 && pollRef.current) {
          clearInterval(pollRef.current);
          pollRef.current = null;
          setPayingId(null);
        }
      }, 3000);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : t("payFailed"));
      setPayingId(null);
    }
  }

  const now = Date.now();
  // A held (pending_payment) booking still needs the patient's attention, so it
  // belongs in "upcoming" alongside confirmed ones — not buried under "past".
  const isUpcoming = (a: PatientAppointment) =>
    (a.status === "confirmed" || a.status === "pending_payment") &&
    new Date(a.scheduled_at).getTime() >= now;
  const upcoming = rows
    .filter(isUpcoming)
    .sort((a, b) => +new Date(a.scheduled_at) - +new Date(b.scheduled_at));
  const past = rows
    .filter((a) => !isUpcoming(a))
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

        <div className="relative mx-auto w-full max-w-5xl px-5 pb-7 pt-5 lg:px-8">
          <div className="flex items-center justify-between">
            <Link href="/portal" aria-label={t("back")}
              className="flex h-9 w-9 items-center justify-center rounded-xl bg-white/20 text-white backdrop-blur-sm transition hover:bg-white/30">
              <ArrowLeft size={16} />
            </Link>
            <div className="flex items-center gap-2">
              <LangToggle onDark />
              <Link href="/portal"
                className="flex items-center gap-1.5 rounded-xl bg-white/20 px-3 py-1.5 text-xs font-semibold text-white backdrop-blur-sm transition hover:bg-white/30">
                <Plus size={13} /> {t("newAppointment")}
              </Link>
            </div>
          </div>

          <div className="mt-5">
            <h1 className="text-2xl font-extrabold tracking-tight text-white drop-shadow">{t("apptsTitle")}</h1>
            <p className="mt-1 text-sm text-white/65">{t("apptsSub")}</p>
          </div>
        </div>
      </div>

      {/* Content */}
      <main className="relative mx-auto w-full max-w-5xl space-y-6 px-5 pb-14 pt-6 lg:px-8">
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
              <p className="text-base font-bold text-fg">{t("noAppts")}</p>
              <p className="mt-1 text-sm text-muted">{t("noApptsSub")}</p>
            </div>
            <Link href="/portal"
              className="mt-2 rounded-xl px-6 py-2.5 text-sm font-bold text-white shadow transition-all hover:opacity-90 active:scale-95"
              style={{ background: "var(--brand-grad)" }}>
              {t("bookNowCta")}
            </Link>
          </div>
        )}

        {!loading && !error && upcoming.length > 0 && (
          <section className="space-y-3">
            <div className="flex items-center gap-2">
              <div className="h-5 w-1 rounded-full" style={{ background: "var(--brand-grad)" }} />
              <p className="text-xs font-bold uppercase tracking-widest text-faint">{t("upcoming")} · {upcoming.length}</p>
            </div>
            <div className="grid gap-3 lg:grid-cols-2">
              {upcoming.map((a, idx) => (
                <AppointmentCard
                  key={a.id}
                  a={a}
                  idx={idx}
                  canCancel
                  onCancel={() => setPendingCancel(a)}
                  onReschedule={a.clinic_id ? () => setPendingReschedule(a) : undefined}
                  onPay={() => handlePay(a)}
                  paying={payingId === a.id}
                />
              ))}
            </div>
          </section>
        )}

        {!loading && !error && past.length > 0 && (
          <section className="space-y-3">
            <div className="flex items-center gap-2">
              <div className="h-5 w-1 rounded-full bg-faint/40" />
              <p className="text-xs font-bold uppercase tracking-widest text-faint">{t("pastCancelled")} · {past.length}</p>
            </div>
            <div className="grid gap-3 lg:grid-cols-2">
              {past.map((a, idx) => (
                <AppointmentCard
                  key={a.id}
                  a={a}
                  idx={idx}
                  canCancel={false}
                  onCancel={() => {}}
                  onReview={canReview(a) ? () => openReview(a) : undefined}
                />
              ))}
            </div>
          </section>
        )}

        {!loading && !error && truncated && (
          <Link
            href="/portal/account"
            className="flex items-center justify-between gap-3 rounded-2xl border border-primary/30 bg-[var(--brand-soft)] px-4 py-3 text-sm text-fg transition hover:border-primary/50 animate-fade-in"
          >
            <span className="flex items-center gap-2">
              <Sparkles size={15} className="shrink-0 text-primary" />
              {t("historyLimitedNote", { n: rows.length })}
            </span>
            <span className="shrink-0 font-semibold text-primary">{t("upgradeCta")}</span>
          </Link>
        )}
      </main>

      <ConfirmDialog
        open={pendingCancel !== null}
        destructive
        title={t("cancelApptTitle")}
        subtitle={pendingCancel ? `${pendingCancel.department_name ?? ""} · ${fmt(pendingCancel.scheduled_at, dateLocale)}` : undefined}
        description={t("cancelApptDesc")}
        confirmLabel={t("cancelApptTitle")}
        cancelLabel={t("keepIt")}
        busyLabel={t("cancelling")}
        busy={cancelling}
        onConfirm={confirmCancel}
        onClose={() => { if (!cancelling) setPendingCancel(null); }}
      />

      {reviewFor && reviewFor.doctor_id != null && (
        <ReviewModal
          doctorId={reviewFor.doctor_id}
          doctorName={reviewFor.doctor_name ?? t("doctorFallback")}
          existing={existingReview}
          onClose={() => setReviewFor(null)}
          onSaved={() => toast.success(t("reviewSaved"))}
        />
      )}

      {pendingReschedule && pendingReschedule.clinic_id && (
        <RescheduleModal
          open={pendingReschedule !== null}
          title={t("rescheduleTitle")}
          fetchSlots={() => portalDepartmentAvailability(pendingReschedule.clinic_id as number)}
          onConfirm={(slot) => portalRescheduleAppointment(pendingReschedule.id, slot).then(() => {})}
          onClose={() => setPendingReschedule(null)}
          onDone={() => { toast.success(t("apptRescheduled")); load(); }}
        />
      )}
    </div>
  );
}
