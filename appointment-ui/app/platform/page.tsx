"use client";

// Platform-admin revenue dashboard. Standalone (platform_admin has no clinic,
// so it must NOT mount the clinic-scoped staff Sidebar). Shows cross-tenant
// booking-fee + subscription revenue, a per-hospital billing table with a
// "mark subscription paid" action, and a payment ledger with manual-confirm
// and refund-flag actions.

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import {
  ShieldCheck, TrendingUp, CreditCard, Crown, Clock, AlertTriangle,
  LogOut, RefreshCw, Check, Undo2, Building2, Wallet, Coins, Percent, Gift,
} from "lucide-react";
import {
  ApiError, PlatformOverview, PlatformPayment,
  getMe, clearToken, platformOverview, platformPayments,
  platformMarkHospitalPaid, platformMarkPaymentPaid, platformRefundPayment,
  platformSetWalletRate, platformGrantCredits,
} from "@/lib/api";
import { useToast } from "@/lib/toast";

const TAKA = (n: number) => `৳${(n ?? 0).toLocaleString("en-US")}`;

function Tile({ icon, label, value, tone = "default" }: {
  icon: React.ReactNode; label: string; value: string;
  tone?: "default" | "warn";
}) {
  return (
    <div className="rounded-2xl border border-border bg-surface/80 p-5 backdrop-blur-sm">
      <div className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-faint">
        <span className={tone === "warn" ? "text-amber-500" : "text-primary"}>{icon}</span>
        {label}
      </div>
      <p className="mt-2 text-2xl font-extrabold text-fg">{value}</p>
    </div>
  );
}

function billingBadge(status: string): string {
  if (status === "active") return "badge-success";
  if (status === "suspended") return "badge-danger";
  return "badge-warning"; // past_due
}

export default function PlatformDashboardPage() {
  const router = useRouter();
  const toast = useToast();
  const [ok, setOk] = useState<boolean | null>(null);
  const [data, setData] = useState<PlatformOverview | null>(null);
  const [payments, setPayments] = useState<PlatformPayment[]>([]);
  const [busy, setBusy] = useState<string | null>(null);

  const load = useCallback(async () => {
    const [overview, pays] = await Promise.all([
      platformOverview(),
      platformPayments(),
    ]);
    setData(overview);
    setPayments(pays);
  }, []);

  // Gate: only a platform_admin may see this page.
  useEffect(() => {
    let active = true;
    getMe()
      .then((me) => {
        if (!active) return;
        if (me.role !== "platform_admin") { router.replace("/platform-admin"); return; }
        setOk(true);
        load().catch(() => toast.error("Could not load dashboard data."));
      })
      .catch((e) => {
        if (e instanceof ApiError && e.status === 401) router.replace("/platform-admin");
      });
    return () => { active = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function markHospitalPaid(hospitalId: number, name: string) {
    setBusy(`h-${hospitalId}`);
    try {
      await platformMarkHospitalPaid(hospitalId);
      toast.success(`${name} subscription marked paid.`);
      await load();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Failed to mark paid.");
    } finally { setBusy(null); }
  }

  async function confirmPayment(id: string) {
    setBusy(`p-${id}`);
    try {
      await platformMarkPaymentPaid(id);
      toast.success("Payment confirmed.");
      await load();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Failed to confirm.");
    } finally { setBusy(null); }
  }

  async function refund(id: string) {
    setBusy(`r-${id}`);
    try {
      await platformRefundPayment(id);
      toast.success("Payment flagged for refund.");
      await load();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Failed to refund.");
    } finally { setBusy(null); }
  }

  async function editRate(h: { id: number; name: string; credit_rate_bdt: number }) {
    const input = window.prompt(`Set ৳/credit rate for ${h.name}:`, String(h.credit_rate_bdt || 20));
    if (input == null) return;
    const rate = Number(input);
    if (!(rate > 0)) { toast.error("Rate must be a positive number."); return; }
    setBusy(`rate-${h.id}`);
    try {
      await platformSetWalletRate(h.id, rate);
      toast.success(`Rate set to ৳${rate}/credit.`);
      await load();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Failed to set rate.");
    } finally { setBusy(null); }
  }

  async function grantCredits(h: { id: number; name: string }) {
    const input = window.prompt(`Grant credits to ${h.name} (negative to claw back):`, "");
    if (input == null || input.trim() === "") return;
    const credits = Number(input);
    if (!Number.isInteger(credits) || credits === 0) { toast.error("Enter a non-zero whole number."); return; }
    setBusy(`grant-${h.id}`);
    try {
      await platformGrantCredits(h.id, credits);
      toast.success(`${credits > 0 ? "Granted" : "Clawed back"} ${Math.abs(credits)} credits.`);
      await load();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Failed to adjust credits.");
    } finally { setBusy(null); }
  }

  function signOut() { clearToken(); router.replace("/platform-admin"); }

  if (ok === null) {
    return (
      <div className="flex h-screen items-center justify-center bg-bg text-muted">
        <RefreshCw className="animate-spin" size={20} />
      </div>
    );
  }

  return (
    <div className="min-h-screen w-full bg-bg">
      {/* Header */}
      <header className="border-b border-border bg-surface px-5 py-4 lg:px-8">
        <div className="mx-auto flex max-w-6xl items-center justify-between">
          <div className="flex items-center gap-3">
            <span className="flex h-10 w-10 items-center justify-center rounded-2xl text-white shadow"
              style={{ background: "linear-gradient(135deg, #7c3aed, #4f46e5)" }}>
              <ShieldCheck size={20} />
            </span>
            <div>
              <h1 className="text-lg font-extrabold text-fg">Platform Dashboard</h1>
              <p className="text-xs text-muted">Revenue, hospital billing & payments</p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <button onClick={() => load()} className="btn-ghost btn-sm" title="Refresh">
              <RefreshCw size={15} />
            </button>
            <button onClick={signOut} className="btn-ghost btn-sm" title="Sign out">
              <LogOut size={15} />
            </button>
          </div>
        </div>
      </header>

      <main className="mx-auto max-w-6xl space-y-8 px-5 py-8 lg:px-8">
        {data && (
          <>
            {/* Profit & loss — the headline number, plus the revenue vs cost split */}
            <section className="grid grid-cols-1 gap-4 lg:grid-cols-4">
              <div className="rounded-2xl border border-primary/30 bg-surface/80 p-5 backdrop-blur-sm lg:col-span-1"
                style={{ boxShadow: "0 0 40px -12px rgba(99,102,241,0.5)" }}>
                <div className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-faint">
                  <TrendingUp size={13} className="text-primary" /> Net margin
                </div>
                <p className={`mt-2 text-3xl font-extrabold ${data.net_margin >= 0 ? "text-primary" : "text-danger"}`}>
                  {TAKA(data.net_margin)}
                </p>
                <p className="mt-1 text-xs text-faint">revenue − channel cost − gateway</p>
              </div>
              <Tile icon={<TrendingUp size={13} />} label="Gross revenue" value={TAKA(data.gross_revenue)} />
              <Tile icon={<Wallet size={13} />} label="Channel cost (est.)" value={TAKA(data.estimated_channel_cost)} />
              <Tile icon={<Percent size={13} />} label="Gateway fees" value={TAKA(data.gateway_fees)} />
            </section>

            {/* Revenue split + wallet liability */}
            <section className="grid grid-cols-2 gap-4 lg:grid-cols-4">
              <Tile icon={<TrendingUp size={13} />} label="Booking fees" value={TAKA(data.booking_fee_revenue)} />
              <Tile icon={<CreditCard size={13} />} label="Patient subs" value={TAKA(data.patient_sub_revenue)} />
              <Tile icon={<Building2 size={13} />} label="Hospital subs" value={TAKA(data.hospital_sub_revenue)} />
              <Tile icon={<Coins size={13} />} label="Credit sales" value={TAKA(data.credit_topup_revenue)} />
              <Tile icon={<Crown size={13} />} label="Premium patients" value={String(data.subscribers_premium)} />
              <Tile icon={<Clock size={13} />} label="On trial" value={String(data.subscribers_trialing)} />
              <Tile icon={<Coins size={13} />} label="Unused credits" value={String(data.unused_wallet_credits)} />
              <Tile icon={<AlertTriangle size={13} />} label="Wallet debt"
                value={String(data.outstanding_wallet_debt)}
                tone={data.outstanding_wallet_debt ? "warn" : "default"} />
            </section>

            {/* Usage this-period (what's consuming credits) */}
            <section className="grid grid-cols-2 gap-4 lg:grid-cols-4">
              <Tile icon={<Coins size={13} />} label="Credits sold" value={String(data.credits_sold)} />
              <Tile icon={<CreditCard size={13} />} label="SMS sent" value={String(data.usage_sms)} />
              <Tile icon={<Clock size={13} />} label="Voice minutes" value={String(data.usage_voice_minutes)} />
              <Tile icon={<CreditCard size={13} />} label="WhatsApp msgs" value={String(data.usage_whatsapp)} />
              <Tile icon={<CreditCard size={13} />} label="Paid payments" value={String(data.paid_count)} />
              <Tile icon={<AlertTriangle size={13} />} label="Refunds pending"
                value={String(data.refunds_pending)} tone={data.refunds_pending ? "warn" : "default"} />
              <Tile icon={<AlertTriangle size={13} />} label="Open escalations"
                value={String(data.open_platform_escalations)}
                tone={data.open_platform_escalations ? "warn" : "default"} />
            </section>

            {/* Hospitals */}
            <section className="space-y-3">
              <h2 className="flex items-center gap-2 text-sm font-bold uppercase tracking-widest text-faint">
                <Building2 size={14} /> Hospitals
              </h2>
              <div className="overflow-x-auto rounded-2xl border border-border bg-surface/80">
                <table className="w-full min-w-[720px] text-sm">
                  <thead>
                    <tr className="border-b border-border text-left text-xs uppercase tracking-wider text-faint">
                      <th className="px-4 py-3">Hospital</th>
                      <th className="px-4 py-3">Billing</th>
                      <th className="px-4 py-3">Wallet</th>
                      <th className="px-4 py-3">Rate</th>
                      <th className="px-4 py-3">Credit sales</th>
                      <th className="px-4 py-3">Consumed</th>
                      <th className="px-4 py-3">Fee revenue</th>
                      <th className="px-4 py-3">Dues</th>
                      <th className="px-4 py-3"></th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.hospitals.map((h) => (
                      <tr key={h.id} className="border-b border-border/60 last:border-0">
                        <td className="px-4 py-3 font-semibold text-fg">
                          {h.name}
                          {h.wallet_status === "suspended" && (
                            <span className="badge badge-danger ml-2">wallet suspended</span>
                          )}
                        </td>
                        <td className="px-4 py-3">
                          <span className={`badge ${billingBadge(h.billing_status)}`}>{h.billing_status}</span>
                        </td>
                        <td className={`px-4 py-3 font-semibold ${h.wallet_balance < 0 ? "text-danger" : "text-fg"}`}>
                          {h.wallet_balance}
                        </td>
                        <td className="px-4 py-3 text-muted">৳{Number(h.credit_rate_bdt).toFixed(2)}</td>
                        <td className="px-4 py-3 text-muted">{TAKA(h.credit_revenue)}</td>
                        <td className="px-4 py-3 text-muted">{h.credits_consumed}</td>
                        <td className="px-4 py-3 text-muted">{TAKA(h.fee_revenue)}</td>
                        <td className="px-4 py-3 text-muted">{h.dues ? TAKA(h.dues) : "—"}</td>
                        <td className="px-4 py-3">
                          <div className="flex items-center justify-end gap-1.5">
                            <button onClick={() => editRate(h)} disabled={busy === `rate-${h.id}`}
                              className="btn-ghost btn-sm disabled:opacity-50" title="Set ৳/credit rate">
                              <Percent size={13} />
                            </button>
                            <button onClick={() => grantCredits(h)} disabled={busy === `grant-${h.id}`}
                              className="btn-ghost btn-sm disabled:opacity-50" title="Grant / claw back credits">
                              <Gift size={13} />
                            </button>
                            <button
                              onClick={() => markHospitalPaid(h.id, h.name)}
                              disabled={busy === `h-${h.id}`}
                              className="btn-primary btn-sm disabled:opacity-50"
                            >
                              <Check size={13} /> Paid
                            </button>
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>

            {/* Payments ledger */}
            <section className="space-y-3">
              <h2 className="flex items-center gap-2 text-sm font-bold uppercase tracking-widest text-faint">
                <CreditCard size={14} /> Recent payments
              </h2>
              <div className="overflow-x-auto rounded-2xl border border-border bg-surface/80">
                <table className="w-full min-w-[760px] text-sm">
                  <thead>
                    <tr className="border-b border-border text-left text-xs uppercase tracking-wider text-faint">
                      <th className="px-4 py-3">When</th>
                      <th className="px-4 py-3">Kind</th>
                      <th className="px-4 py-3">Hospital</th>
                      <th className="px-4 py-3">Amount</th>
                      <th className="px-4 py-3">Status</th>
                      <th className="px-4 py-3"></th>
                    </tr>
                  </thead>
                  <tbody>
                    {payments.length === 0 && (
                      <tr><td colSpan={6} className="px-4 py-6 text-center text-muted">No payments yet.</td></tr>
                    )}
                    {payments.map((p) => (
                      <tr key={p.id} className="border-b border-border/60 last:border-0">
                        <td className="px-4 py-3 text-muted">{new Date(p.created_at).toLocaleString("en-GB")}</td>
                        <td className="px-4 py-3 text-fg">{p.kind === "booking_fee" ? "Booking fee" : "Subscription"}</td>
                        <td className="px-4 py-3 text-muted">{p.hospital_name ?? "—"}</td>
                        <td className="px-4 py-3 font-semibold text-fg">{TAKA(p.amount)}</td>
                        <td className="px-4 py-3">
                          <span className={`badge ${
                            p.status === "paid" ? "badge-success"
                            : p.status === "refunded" || p.status === "failed" ? "badge-danger"
                            : "badge-warning"}`}>{p.status}</span>
                          {p.refund_needed && (
                            <span className="ml-1 badge badge-danger" title="Slot lost after payment">refund?</span>
                          )}
                        </td>
                        <td className="px-4 py-3 text-right">
                          {(p.status === "initiated" || p.status === "expired") && (
                            <button onClick={() => confirmPayment(p.id)} disabled={busy === `p-${p.id}`}
                              className="btn-secondary btn-sm disabled:opacity-50">
                              <Check size={13} /> Confirm
                            </button>
                          )}
                          {p.status === "paid" && (
                            <button onClick={() => refund(p.id)} disabled={busy === `r-${p.id}`}
                              className="btn-ghost btn-sm text-danger disabled:opacity-50">
                              <Undo2 size={13} /> Refund
                            </button>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
          </>
        )}

        <p className="pt-4 text-center text-xs text-faint">
          <Link href="/hospitals" className="hover:underline">Back to staff console</Link>
        </p>
      </main>
    </div>
  );
}
