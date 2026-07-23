"use client";

// Patient account & plan page: shows the current tier (free / trial / premium),
// a trial countdown or premium-until date, the free-tier monthly AI-booking
// usage, and an upgrade/renew button that runs the subscription checkout (opens
// the gateway if needed, then polls until premium activates).

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { ArrowLeft, Sparkles, Check, CalendarClock, Crown, Phone, ShieldCheck, Zap } from "lucide-react";
import {
  PatientAccount,
  getPatientMe,
  portalPhoneVerifyConfirm,
  portalPhoneVerifyStart,
  portalSubscribe,
} from "@/lib/api";
import { useToast } from "@/lib/toast";
import { LangToggle, useLang } from "@/lib/i18n";

function daysUntil(iso: string): number {
  return Math.max(0, Math.ceil((new Date(iso).getTime() - Date.now()) / 86_400_000));
}

export default function AccountPage() {
  const toast = useToast();
  const { t, dateLocale } = useLang();
  const [me, setMe] = useState<PatientAccount | null>(null);
  const [loading, setLoading] = useState(true);
  const [subscribing, setSubscribing] = useState(false);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const load = useCallback(() => {
    return getPatientMe()
      .then(setMe)
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => { load(); }, [load]);
  useEffect(() => () => { if (pollRef.current) clearInterval(pollRef.current); }, []);

  async function upgrade() {
    setSubscribing(true);
    try {
      const res = await portalSubscribe();
      if (res.payment?.pay_url) {
        window.open(res.payment.pay_url, "_blank", "noopener,noreferrer");
        // Poll /me until premium activates (or give up after ~2 min).
        if (pollRef.current) clearInterval(pollRef.current);
        let ticks = 0;
        pollRef.current = setInterval(async () => {
          ticks += 1;
          const fresh = await getPatientMe().catch(() => null);
          if (fresh) {
            setMe(fresh);
            if (fresh.tier === "premium") {
              if (pollRef.current) clearInterval(pollRef.current);
              pollRef.current = null;
              setSubscribing(false);
              toast.success(t("subSuccess"));
            }
          }
          if (ticks >= 40 && pollRef.current) {
            clearInterval(pollRef.current);
            pollRef.current = null;
            setSubscribing(false);
          }
        }, 3000);
      } else {
        // Manual provider auto-paid — premium is already live.
        await load();
        setSubscribing(false);
        toast.success(t("subSuccess"));
      }
    } catch (e) {
      setSubscribing(false);
      toast.error(e instanceof Error ? e.message : t("apptsLoadFailed"));
    }
  }

  const [pvPhone, setPvPhone] = useState("");
  const [pvCode, setPvCode] = useState("");
  const [pvSent, setPvSent] = useState(false);
  const [pvBusy, setPvBusy] = useState(false);

  async function sendCode() {
    setPvBusy(true);
    try {
      await portalPhoneVerifyStart(pvPhone || me?.phone || "");
      setPvSent(true);
      toast.success(t("pvCodeSent"));
    } catch (e) {
      toast.error(e instanceof Error ? e.message : t("pvFailed"));
    } finally {
      setPvBusy(false);
    }
  }

  async function confirmCode() {
    setPvBusy(true);
    try {
      await portalPhoneVerifyConfirm(pvCode.trim());
      toast.success(t("pvSuccess"));
      setPvSent(false);
      setPvCode("");
      await load();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : t("pvFailed"));
    } finally {
      setPvBusy(false);
    }
  }

  const tier = me?.tier ?? "free";
  const isPremium = tier === "premium";
  const isTrial = tier === "trial";

  return (
    <div className="relative min-h-screen w-full overflow-y-auto bg-bg">
      {/* Gradient hero */}
      <div className="relative overflow-hidden" style={{ background: "var(--brand-grad)" }}>
        <div className="absolute inset-0" style={{ background: "linear-gradient(180deg, rgba(0,0,0,0.05) 0%, rgba(0,0,0,0.25) 100%)" }} />
        <div className="pointer-events-none absolute -right-20 -top-20 h-72 w-72 rounded-full bg-white/10 blur-3xl" />
        <div className="relative mx-auto w-full max-w-3xl px-5 pb-7 pt-5 lg:px-8">
          <div className="flex items-center justify-between">
            <Link href="/portal" aria-label={t("back")}
              className="flex h-9 w-9 items-center justify-center rounded-xl bg-white/20 text-white backdrop-blur-sm transition hover:bg-white/30">
              <ArrowLeft size={16} />
            </Link>
            <LangToggle onDark />
          </div>
          <div className="mt-5">
            <h1 className="text-2xl font-extrabold tracking-tight text-white drop-shadow">{t("accountTitle")}</h1>
            <p className="mt-1 text-sm text-white/65">{t("accountSub")}</p>
          </div>
        </div>
      </div>

      <main className="relative mx-auto w-full max-w-3xl space-y-6 px-5 pb-14 pt-6 lg:px-8">
        {loading && <div className="skeleton-card h-56 rounded-2xl" />}

        {!loading && me && (
          <>
            {/* Plan card */}
            <div className="overflow-hidden rounded-2xl border border-border bg-surface/80 backdrop-blur-sm animate-fade-in-up">
              <div className="flex items-center gap-3 px-5 py-4"
                style={{ background: isPremium || isTrial ? "var(--brand-soft)" : "var(--surface-3)" }}>
                <span className="flex h-11 w-11 items-center justify-center rounded-2xl text-white shadow"
                  style={{ background: "var(--brand-grad)" }}>
                  {isPremium ? <Crown size={20} /> : isTrial ? <Zap size={20} /> : <Sparkles size={20} />}
                </span>
                <div className="min-w-0 flex-1">
                  <p className="text-xs font-bold uppercase tracking-widest text-faint">{t("accountPlanTitle")}</p>
                  <p className="text-lg font-extrabold text-fg">
                    {isPremium ? t("planPremium") : isTrial ? t("planTrialBadge") : t("planFree")}
                  </p>
                </div>
              </div>

              <div className="space-y-3 px-5 py-4">
                {isTrial && me.trial_ends_at && (
                  <p className="flex items-center gap-2 text-sm text-muted">
                    <CalendarClock size={15} className="text-primary" />
                    {t("planTrialEndsIn", { d: daysUntil(me.trial_ends_at) })}
                  </p>
                )}
                {isPremium && me.premium_until && (
                  <p className="flex items-center gap-2 text-sm text-muted">
                    <CalendarClock size={15} className="text-primary" />
                    {t("premiumUntil", { date: new Date(me.premium_until).toLocaleDateString(dateLocale) })}
                  </p>
                )}
                {tier === "free" && (
                  <p className="flex items-center gap-2 text-sm text-muted">
                    <Zap size={15} className="text-primary" />
                    {me.agent_bookings_cap < 0
                      ? t("planUnlimited")
                      : t("planFreeCapLine", { used: me.agent_bookings_used, cap: me.agent_bookings_cap })}
                  </p>
                )}
              </div>
            </div>

            {/* Premium perks + upgrade CTA (hidden once premium) */}
            {!isPremium && (
              <div className="rounded-2xl border border-primary/30 bg-[var(--brand-soft)] p-5 animate-fade-in-up">
                <p className="text-sm font-bold text-fg">{t("planPremium")}</p>
                <p className="mt-1 text-2xl font-extrabold text-primary">{t("subPriceLine", { n: me.subscription_fee })}</p>
                <p className="mt-3 flex items-start gap-2 text-sm text-fg">
                  <Check size={15} className="mt-0.5 shrink-0 text-primary" /> {t("premiumPerks")}
                </p>
                <button
                  onClick={upgrade}
                  disabled={subscribing}
                  className="btn-primary mt-4 w-full justify-center disabled:opacity-60"
                >
                  <Sparkles size={15} />
                  {subscribing ? t("subscribing") : t("upgradeCta")}
                </button>
              </div>
            )}

            {isPremium && (
              <div className="rounded-2xl border border-border bg-surface/80 p-5 animate-fade-in-up">
                <p className="flex items-start gap-2 text-sm text-fg">
                  <Check size={15} className="mt-0.5 shrink-0 text-primary" /> {t("premiumPerks")}
                </p>
                <button
                  onClick={upgrade}
                  disabled={subscribing}
                  className="btn-secondary mt-4 w-full justify-center disabled:opacity-60"
                >
                  {subscribing ? t("subscribing") : t("renewCta")}
                </button>
              </div>
            )}

            {/* One-time phone verification — unlocks calling the platform number */}
            <div className="rounded-2xl border border-border bg-surface/80 p-5 animate-fade-in-up">
              <p className="flex items-center gap-2 text-sm font-bold text-fg">
                <Phone size={15} className="shrink-0 text-primary" /> {t("pvTitle")}
              </p>
              {me.phone_verified ? (
                <p className="mt-3 flex items-start gap-2 text-sm text-muted" data-testid="pv-verified">
                  <ShieldCheck size={15} className="mt-0.5 shrink-0 text-primary" />
                  {t("pvVerifiedLine", { phone: me.phone })}
                </p>
              ) : (
                <>
                  <p className="mt-2 text-sm text-muted">{t("pvBody")}</p>
                  {!pvSent ? (
                    <div className="mt-3 flex gap-2">
                      <input
                        type="tel"
                        value={pvPhone}
                        onChange={(e) => setPvPhone(e.target.value)}
                        placeholder={me.phone || t("pvPhonePlaceholder")}
                        aria-label={t("pvPhonePlaceholder")}
                        className="input flex-1"
                      />
                      <button onClick={sendCode} disabled={pvBusy}
                        className="btn-primary shrink-0 disabled:opacity-60">
                        {t("pvSendCode")}
                      </button>
                    </div>
                  ) : (
                    <div className="mt-3 flex gap-2">
                      <input
                        inputMode="numeric"
                        value={pvCode}
                        onChange={(e) => setPvCode(e.target.value)}
                        placeholder={t("pvCodePlaceholder")}
                        aria-label={t("pvCodePlaceholder")}
                        className="input flex-1"
                      />
                      <button onClick={confirmCode} disabled={pvBusy || pvCode.trim().length < 4}
                        className="btn-primary shrink-0 disabled:opacity-60">
                        {t("pvConfirm")}
                      </button>
                    </div>
                  )}
                </>
              )}
            </div>
          </>
        )}
      </main>
    </div>
  );
}
