"use client";

// Hospital credit wallet: current balance, a low-balance warning, a buy-credits
// form (priced at the hospital's negotiated ৳/credit rate — opens the gateway if
// needed, then polls until the balance rises), and the recent ledger. Metering
// is off unless the platform enables credits; until then the balance simply
// stays at 0 and nothing is ever drawn down.

import { useCallback, useEffect, useRef, useState } from "react";
import { Wallet, Coins, AlertTriangle, RefreshCw, ArrowDownRight, ArrowUpRight } from "lucide-react";
import TopBar from "@/components/layout/TopBar";
import { getWallet, topupWallet, type Wallet as WalletT } from "@/lib/api";
import { useToast } from "@/lib/toast";

const TAKA = (n: number) => `৳${n.toLocaleString("en-US")}`;

const REASON_LABEL: Record<string, string> = {
  booking: "Booking", sms: "SMS", voice: "Voice call", whatsapp: "WhatsApp",
  topup: "Top-up", grant: "Grant", adjustment: "Adjustment", refund: "Refund",
};

export default function WalletPage() {
  const toast = useToast();
  const [wallet, setWallet] = useState<WalletT | null>(null);
  const [loading, setLoading] = useState(true);
  const [credits, setCredits] = useState(100);
  const [buying, setBuying] = useState(false);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const load = useCallback(() => {
    return getWallet().then(setWallet).catch(() => {}).finally(() => setLoading(false));
  }, []);

  useEffect(() => { load(); }, [load]);
  useEffect(() => () => { if (pollRef.current) clearInterval(pollRef.current); }, []);

  async function buy() {
    if (!(credits > 0)) { toast.error("Enter a positive number of credits."); return; }
    setBuying(true);
    try {
      const res = await topupWallet(credits);
      if (res.payment?.pay_url) {
        window.open(res.payment.pay_url, "_blank", "noopener,noreferrer");
        const startBal = wallet?.balance ?? 0;
        if (pollRef.current) clearInterval(pollRef.current);
        let ticks = 0;
        pollRef.current = setInterval(async () => {
          ticks += 1;
          const fresh = await getWallet().catch(() => null);
          if (fresh) {
            setWallet(fresh);
            if (fresh.balance > startBal) {
              clearInterval(pollRef.current!); pollRef.current = null;
              setBuying(false);
              toast.success("Credits added.");
            }
          }
          if (ticks >= 40 && pollRef.current) {
            clearInterval(pollRef.current); pollRef.current = null;
            setBuying(false);
          }
        }, 3000);
      } else {
        // Manual provider auto-paid — credits are already loaded.
        await load();
        setBuying(false);
        toast.success("Credits added.");
      }
    } catch (e) {
      setBuying(false);
      toast.error(e instanceof Error ? e.message : "Top-up failed.");
    }
  }

  const rate = wallet?.credit_rate_bdt ?? 0;
  const cost = Math.round(credits * rate);

  return (
    <>
      <TopBar title="Credit Wallet" />
      <main className="flex flex-1 flex-col gap-6 overflow-y-auto bg-bg p-6">
        {loading ? (
          <div className="flex flex-1 items-center justify-center text-muted">
            <RefreshCw className="animate-spin" size={20} />
          </div>
        ) : (
          <div className="mx-auto w-full max-w-3xl space-y-6">

            {/* Balance + buy */}
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
              <div className="card p-5">
                <div className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-faint">
                  <Wallet size={14} className="text-primary" /> Balance
                </div>
                <p className={`mt-2 text-4xl font-extrabold ${(wallet?.balance ?? 0) < 0 ? "text-danger" : "text-fg"}`}>
                  {wallet?.balance ?? 0}
                  <span className="ml-1 text-sm font-medium text-faint">credits</span>
                </p>
                <p className="mt-1 text-xs text-muted">Rate: ৳{rate.toFixed(2)} / credit</p>
                {wallet?.low_balance && (
                  <p className="mt-3 flex items-start gap-1.5 rounded-lg bg-amber-500/10 p-2 text-xs text-amber-600">
                    <AlertTriangle size={14} className="mt-0.5 shrink-0" />
                    Low balance — top up so your AI bookings and messages keep running.
                  </p>
                )}
              </div>

              <div className="card p-5">
                <div className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-faint">
                  <Coins size={14} className="text-primary" /> Buy credits
                </div>
                <label className="mt-3 block text-xs text-muted">Credits</label>
                <input
                  type="number" min={1} value={credits}
                  onChange={(e) => setCredits(Math.max(0, Math.floor(Number(e.target.value) || 0)))}
                  className="input mt-1 w-full"
                />
                <div className="mt-2 flex gap-2">
                  {[100, 500, 2000].map((n) => (
                    <button key={n} onClick={() => setCredits(n)}
                      className="btn-ghost btn-sm flex-1">{n}</button>
                  ))}
                </div>
                <button onClick={buy} disabled={buying}
                  className="btn-primary mt-3 w-full disabled:opacity-50">
                  {buying ? "Processing…" : `Pay ${TAKA(cost)}`}
                </button>
              </div>
            </div>

            {/* Ledger */}
            <section className="space-y-3">
              <h2 className="text-sm font-bold uppercase tracking-widest text-faint">Recent activity</h2>
              <div className="overflow-x-auto rounded-2xl border border-border bg-surface/80">
                <table className="w-full min-w-[560px] text-sm">
                  <thead>
                    <tr className="border-b border-border text-left text-xs uppercase tracking-wider text-faint">
                      <th className="px-4 py-3">When</th>
                      <th className="px-4 py-3">Type</th>
                      <th className="px-4 py-3 text-right">Change</th>
                      <th className="px-4 py-3 text-right">Balance</th>
                      <th className="px-4 py-3">Note</th>
                    </tr>
                  </thead>
                  <tbody>
                    {wallet && wallet.ledger.length === 0 && (
                      <tr><td colSpan={5} className="px-4 py-6 text-center text-muted">No activity yet.</td></tr>
                    )}
                    {wallet?.ledger.map((e) => (
                      <tr key={e.id} className="border-b border-border/60 last:border-0">
                        <td className="px-4 py-3 text-muted">{new Date(e.created_at).toLocaleString("en-GB")}</td>
                        <td className="px-4 py-3 text-fg">{REASON_LABEL[e.reason] ?? e.reason}</td>
                        <td className={`px-4 py-3 text-right font-semibold ${e.delta < 0 ? "text-danger" : "text-emerald-600"}`}>
                          <span className="inline-flex items-center gap-1 justify-end">
                            {e.delta < 0 ? <ArrowDownRight size={13} /> : <ArrowUpRight size={13} />}
                            {e.delta > 0 ? `+${e.delta}` : e.delta}
                          </span>
                        </td>
                        <td className="px-4 py-3 text-right text-muted">{e.balance_after}</td>
                        <td className="px-4 py-3 text-muted">{e.note ?? "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
          </div>
        )}
      </main>
    </>
  );
}
