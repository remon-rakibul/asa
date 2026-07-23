"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { Building2, CalendarDays, CheckCircle2, Clock, ExternalLink, Loader2, Sparkles, Ticket, X } from "lucide-react";
import {
  ApiError, ChatSlot, DoctorDetail, PaymentPrompt, portalBookAppointment,
  portalDoctorDetail, portalGetPayment,
} from "@/lib/api";
import { usePatientAuth } from "@/lib/patientAuth";
import { useLang } from "@/lib/i18n";
import { avatarGradient, initialsOf } from "@/lib/avatar";

interface BookingSheetProps {
  doc: DoctorDetail;
  /** Slot the patient tapped; null = opened from the main CTA (pick inside). */
  slot: ChatSlot | null;
  onClose: () => void;
  /** Secondary path: hand over to the AI assistant instead. */
  onBookWithAI: () => void;
  /** Fired after a successful booking so the page can refresh its slots. */
  onBooked?: () => void;
}

function useCountdown(expiresAt: string | null): string {
  const [label, setLabel] = useState("");
  useEffect(() => {
    if (!expiresAt) return;
    const end = new Date(expiresAt).getTime();
    const tick = () => {
      const ms = end - Date.now();
      if (ms <= 0) { setLabel("00:00"); return; }
      const m = Math.floor(ms / 60000);
      const s = Math.floor((ms % 60000) / 1000);
      setLabel(`${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`);
    };
    tick();
    const id = setInterval(tick, 1000);
    return () => clearInterval(id);
  }, [expiresAt]);
  return label;
}

/** Direct-booking confirmation sheet — book a tapped slot in one step, no
 *  agent. Name/phone prefill from the account; only age is typed. When a
 *  booking fee applies, shows a pay step (gateway link + live poll) instead
 *  of confirming immediately. */
export default function BookingSheet({ doc, slot, onClose, onBookWithAI, onBooked }: BookingSheetProps) {
  const { account } = usePatientAuth();
  const { t } = useLang();

  const [slots, setSlots] = useState<ChatSlot[]>(doc.slots);
  const [chosen, setChosen] = useState<ChatSlot | null>(slot ?? doc.slots[0] ?? null);
  const [name, setName] = useState(account?.name ?? "");
  const [phone, setPhone] = useState(account?.phone ?? "");
  const [age, setAge] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState("");
  const [slotTaken, setSlotTaken] = useState(false);
  const [success, setSuccess] = useState<{ serial: number | null; slot: string } | null>(null);
  const [pending, setPending] = useState<PaymentPrompt | null>(null);
  const [payFailed, setPayFailed] = useState(false);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const countdown = useCountdown(pending?.expires_at ?? null);

  const ageNum = Number(age);
  const ageValid = Number.isInteger(ageNum) && ageNum >= 1 && ageNum <= 120;
  const phoneDigits = phone.replace(/\D/g, "");
  const phoneValid = phoneDigits.length >= 10 && phoneDigits.length <= 11;
  const canSubmit = !!chosen && name.trim().length > 0 && ageValid && phoneValid && !submitting;

  // Poll while a payment is pending — the gateway confirms asynchronously
  // (IPN), so this tab finds out by asking, not by any push from the server.
  useEffect(() => {
    if (!pending) return;
    pollRef.current = setInterval(async () => {
      try {
        const p = await portalGetPayment(pending.payment_id);
        if (p.appointment_status === "confirmed") {
          setPending(null);
          setSuccess({ serial: null, slot: chosen?.label ?? "" });
          onBooked?.();
        } else if (p.status === "expired" || p.status === "failed") {
          setPending(null);
          setPayFailed(true);
        }
      } catch {
        // transient network hiccup — keep polling, the next tick may succeed
      }
    }, 3000);
    return () => { if (pollRef.current) clearInterval(pollRef.current); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pending?.payment_id]);

  async function submit() {
    if (!chosen || !canSubmit) return;
    setSubmitting(true);
    setError("");
    setSlotTaken(false);
    setPayFailed(false);
    try {
      const res = await portalBookAppointment({
        clinic_id: doc.clinic_id,
        doctor_id: doc.id,
        slot_datetime: chosen.datetime,
        slot_label: chosen.label,
        patient_name: name.trim(),
        patient_age: ageNum,
        patient_mobile: phone.trim(),
      });
      if (res.status === "pending_payment" && res.payment) {
        setPending(res.payment);
        if (res.payment.pay_url) window.open(res.payment.pay_url, "_blank", "noopener");
      } else {
        setSuccess({ serial: res.serial_number, slot: res.slot_label });
        onBooked?.();
      }
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) setSlotTaken(true);
      else setError(e instanceof Error ? e.message : t("bsFailed"));
    } finally {
      setSubmitting(false);
    }
  }

  // The tapped slot got taken meanwhile — refetch and let the patient re-pick.
  async function refreshSlots() {
    setRefreshing(true);
    try {
      const fresh = await portalDoctorDetail(doc.id);
      setSlots(fresh.slots);
      setChosen(fresh.slots[0] ?? null);
      setSlotTaken(false);
      setPayFailed(false);
      setError("");
    } catch {
      setError(t("bsFailed"));
    } finally {
      setRefreshing(false);
    }
  }

  const field = "input w-full";
  const label = "text-[11px] font-bold uppercase tracking-widest text-faint";

  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center bg-black/60 backdrop-blur-sm animate-fade-in sm:items-center" onClick={onClose}>
      <div
        className="max-h-[92dvh] w-full max-w-md overflow-y-auto rounded-t-3xl border border-border bg-surface shadow-2xl animate-slide-up sm:rounded-3xl"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="relative overflow-hidden px-5 py-4" style={{ background: "var(--brand-grad)" }}>
          <div className="flex items-center gap-3">
            <span
              className="flex h-11 w-11 shrink-0 items-center justify-center rounded-2xl text-sm font-extrabold text-white ring-2 ring-white/40"
              style={{ background: avatarGradient(doc.name) }}
            >
              {initialsOf(doc.name)}
            </span>
            <div className="min-w-0 flex-1">
              <p className="truncate text-sm font-extrabold text-white">{doc.name}</p>
              <p className="truncate text-[11px] text-white/75">
                <Building2 size={10} className="mr-1 inline" />
                {doc.hospital_name} · {doc.department_name}
              </p>
            </div>
            <button onClick={onClose} aria-label={t("bsCancel")}
              className="flex h-8 w-8 shrink-0 items-center justify-center rounded-xl bg-white/20 text-white transition hover:bg-white/30">
              <X size={15} />
            </button>
          </div>
        </div>

        {success ? (
          /* Success card */
          <div className="space-y-4 px-5 py-6 text-center">
            <span className="mx-auto flex h-14 w-14 items-center justify-center rounded-full"
              style={{ background: "var(--success-bg)" }}>
              <CheckCircle2 size={28} className="text-success" />
            </span>
            <div>
              <p className="text-base font-extrabold text-fg">{t("bsSuccessTitle")}</p>
              <p className="mt-1 text-sm text-muted" dir="auto">{success.slot}</p>
              {success.serial != null && (
                <p className="mt-2 inline-flex items-center gap-1.5 rounded-full bg-[var(--brand-soft)] px-3 py-1.5 text-sm font-bold text-primary">
                  <Ticket size={14} /> {t("bsSerial")} #{success.serial}
                </p>
              )}
            </div>
            <Link href="/portal/appointments" className="btn-primary w-full justify-center">
              <CalendarDays size={15} /> {t("bsViewAppts")}
            </Link>
          </div>
        ) : pending ? (
          /* Pay step — waiting for the gateway to confirm (polled). */
          <div className="space-y-4 px-5 py-6 text-center">
            <span className="mx-auto flex h-14 w-14 items-center justify-center rounded-full"
              style={{ background: "var(--brand-soft)" }}>
              <Loader2 size={26} className="animate-spin text-primary" />
            </span>
            <div>
              <p className="text-base font-extrabold text-fg">{t("payFeeTitle")}</p>
              <p className="mt-1 text-sm text-muted">
                {t("payFeeBody", { n: String(pending.amount) })}
              </p>
              <p className="mt-2 text-sm text-faint">
                {t("payHoldNote", { m: countdown || "—" })}
              </p>
            </div>
            {pending.pay_url && (
              <a href={pending.pay_url} target="_blank" rel="noopener noreferrer"
                className="btn-primary w-full justify-center">
                <ExternalLink size={15} /> {t("payNowCta")}
              </a>
            )}
          </div>
        ) : (
          <div className="space-y-4 px-5 py-5">
            {/* Slot picker (single tapped slot shows as the selected chip) */}
            <div className="space-y-2">
              <p className={label}>{t("bsSlot")}</p>
              {slots.length === 0 ? (
                <p className="text-sm text-faint">{t("noSlots7d")}</p>
              ) : (
                <div className="flex flex-wrap gap-2">
                  {slots.map((s) => {
                    const active = chosen?.datetime === s.datetime;
                    return (
                      <button key={s.datetime} onClick={() => { setChosen(s); setSlotTaken(false); }}
                        className={`inline-flex items-center gap-1.5 rounded-xl border px-3 py-2 text-sm shadow-sm transition-colors ${
                          active
                            ? "border-transparent text-white"
                            : "border-border bg-surface text-fg hover:border-primary/40"
                        }`}
                        style={active ? { background: "var(--brand-grad)" } : undefined}
                      >
                        <Clock size={13} className={active ? "" : "text-primary"} />
                        <span dir="auto">{s.label}</span>
                      </button>
                    );
                  })}
                </div>
              )}
            </div>

            {/* Fee line */}
            {doc.fee_new != null && (
              <div className="flex items-center justify-between rounded-2xl border border-border bg-surface/80 px-4 py-3">
                <span className="text-sm text-muted">{t("bsFee")}</span>
                <span className="text-base font-extrabold text-fg">৳{doc.fee_new}</span>
              </div>
            )}

            {/* Patient details */}
            <div className="space-y-3">
              <div className="space-y-1.5">
                <p className={label}>{t("bsName")}</p>
                <input className={field} value={name} onChange={(e) => setName(e.target.value)} dir="auto" />
              </div>
              <div className="grid grid-cols-[1fr_110px] gap-3">
                <div className="space-y-1.5">
                  <p className={label}>{t("bsPhone")}</p>
                  <input className={field} value={phone} inputMode="tel"
                    onChange={(e) => setPhone(e.target.value)} />
                  {phone && !phoneValid && (
                    <p className="text-[11px] text-danger">{t("bsPhoneInvalid")}</p>
                  )}
                </div>
                <div className="space-y-1.5">
                  <p className={label}>{t("bsAge")}</p>
                  <input className={field} value={age} inputMode="numeric" placeholder="—"
                    onChange={(e) => setAge(e.target.value)} />
                  {age && !ageValid && (
                    <p className="text-[11px] text-danger">{t("bsAgeInvalid")}</p>
                  )}
                </div>
              </div>
            </div>

            {/* Slot-taken conflict */}
            {slotTaken && (
              <div className="space-y-2 rounded-2xl border border-danger/30 bg-danger/10 p-3.5">
                <p className="text-sm font-semibold text-danger">{t("bsSlotTaken")}</p>
                <button onClick={refreshSlots} disabled={refreshing}
                  className="btn-secondary btn-sm disabled:opacity-40">
                  {refreshing ? <Loader2 size={13} className="animate-spin" /> : null}
                  {t("bsRefreshSlots")}
                </button>
              </div>
            )}
            {payFailed && (
              <div className="space-y-2 rounded-2xl border border-danger/30 bg-danger/10 p-3.5">
                <p className="text-sm font-semibold text-danger">{t("payFailed")}</p>
                <button onClick={refreshSlots} disabled={refreshing}
                  className="btn-secondary btn-sm disabled:opacity-40">
                  {refreshing ? <Loader2 size={13} className="animate-spin" /> : null}
                  {t("bsRefreshSlots")}
                </button>
              </div>
            )}
            {error && <p className="text-sm text-danger">{error}</p>}

            {/* Actions */}
            <button onClick={submit} disabled={!canSubmit}
              className="btn-primary w-full justify-center py-3 text-sm font-bold disabled:opacity-40">
              {submitting
                ? <><Loader2 size={15} className="animate-spin" /> {t("bsBooking")}</>
                : <><CheckCircle2 size={15} /> {t("bsConfirm")}</>}
            </button>
            <button onClick={onBookWithAI}
              className="flex w-full items-center justify-center gap-2 rounded-2xl border border-border bg-surface/80 px-4 py-2.5 text-sm font-semibold text-fg transition hover:border-indigo-500/40">
              <Sparkles size={14} className="text-primary" /> {t("bsWithAI")}
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
