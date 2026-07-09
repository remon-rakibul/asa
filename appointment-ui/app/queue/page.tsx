"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import TopBar from "@/components/layout/TopBar";
import { useToast } from "@/lib/toast";
import { useAuth } from "@/lib/auth";
import { callToken, completeToken, getTodayQueue, type QueueStatus, type Token } from "@/lib/api";
import { CheckCircle, PhoneCall, RefreshCw, Users, Hourglass, ClipboardCheck } from "lucide-react";

// Token-based badge variants so status tints adapt to light/dark theme.
const STATUS_BADGE: Record<Token["status"], string> = {
  waiting:     "badge-surface",
  called:      "badge-warning",
  in_progress: "badge-info",
  completed:   "badge-success",
  skipped:     "badge-danger",
};

function TokenCard({ token, onCall, onComplete }: {
  token: Token;
  onCall: (id: number) => void;
  onComplete: (id: number) => void;
}) {
  const label = `${token.token_prefix}${token.token_number}`;
  const isCalled = token.status === "called" || token.status === "in_progress";

  return (
    <div className="card flex items-center gap-4 px-4 py-3">
      <span className="text-2xl font-bold font-mono w-14 shrink-0 text-center text-fg">{label}</span>

      <div className="flex-1 min-w-0">
        <div className="text-sm font-medium text-fg truncate">{token.patient_name ?? "—"}</div>
        <div className="text-xs text-faint">{token.patient_mobile ?? ""}</div>
      </div>

      <span className={`badge capitalize ${STATUS_BADGE[token.status]}`}>{token.status.replace("_", " ")}</span>

      <div className="flex items-center gap-2">
        {token.status === "waiting" && (
          <button onClick={() => onCall(token.id)} className="btn-primary btn-sm">
            <PhoneCall size={12} />
            Call
          </button>
        )}
        {isCalled && (
          <button onClick={() => onComplete(token.id)} className="btn-success btn-sm">
            <CheckCircle size={12} />
            Done
          </button>
        )}
      </div>
    </div>
  );
}

export default function QueuePage() {
  const toast = useToast();
  const { clinic } = useAuth();
  const [deptId, setDeptId] = useState<string>("");
  const [queue, setQueue] = useState<QueueStatus | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState("");
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Default to the signed-in staff's own department so they never type a raw ID.
  useEffect(() => {
    if (clinic?.id && !deptId) {
      setDeptId(String(clinic.id));
      load(String(clinic.id));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [clinic?.id]);

  const load = useCallback(async (id?: string) => {
    const target = id ?? deptId;
    if (!target) return;
    setLoading(true);
    setErr("");
    try {
      setQueue(await getTodayQueue(parseInt(target)));
    } catch {
      setErr("Could not load queue. Check department ID and login.");
    } finally {
      setLoading(false);
    }
  }, [deptId]);

  useEffect(() => {
    if (pollRef.current) clearInterval(pollRef.current);
    if (deptId) {
      pollRef.current = setInterval(() => load(), 10_000);
    }
    return () => { if (pollRef.current) clearInterval(pollRef.current); };
  }, [deptId, load]);

  async function handleCall(tokenId: number) {
    try {
      await callToken(tokenId);
      await load();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Could not call token");
    }
  }

  async function handleComplete(tokenId: number) {
    try {
      await completeToken(tokenId);
      await load();
      toast.success("Token completed");
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Could not complete token");
    }
  }

  const waiting = queue?.tokens.filter((t) => t.status === "waiting") ?? [];
  const active  = queue?.tokens.filter((t) => ["called","in_progress"].includes(t.status)) ?? [];
  const done    = queue?.tokens.filter((t) => ["completed","skipped"].includes(t.status)) ?? [];

  return (
    <>
      <TopBar title="Queue" />
      <main className="flex-1 overflow-y-auto bg-bg p-6 space-y-6">
        {/* Department selector */}
        <div className="card p-4 animate-fade-in-up">
          <div className="flex flex-wrap items-center gap-4">
            <div className="flex-1 min-w-[12rem]">
              <label className="mb-1 block text-xs font-medium text-muted">
                Department{clinic?.name ? ` · ${clinic.name}` : ""}
              </label>
              <div className="flex items-center gap-2">
                <input className="input w-32 text-sm" type="number" min={1} placeholder="e.g. 2"
                  aria-label="Department ID"
                  value={deptId} onChange={(e) => setDeptId(e.target.value)}
                  onBlur={() => deptId && load()} onKeyDown={(e) => e.key === "Enter" && load()} />
                {clinic?.id && String(clinic.id) !== deptId && (
                  <button
                    onClick={() => { setDeptId(String(clinic.id)); load(String(clinic.id)); }}
                    className="btn-ghost btn-sm whitespace-nowrap"
                  >
                    My department
                  </button>
                )}
              </div>
            </div>
            <button onClick={() => load()} disabled={!deptId || loading} className="btn-outline btn-sm mt-5">
              <RefreshCw size={13} className={loading ? "animate-spin" : ""} />
              Refresh
            </button>
            {queue && (
              <div className="flex gap-6 mt-5">
                <Stat label="Now serving" value={queue.current_token != null ? `${queue.current_token}` : "—"} />
                <Stat label="Waiting" value={String(queue.waiting_count)} />
                <Stat label="Done today" value={String(done.length)} />
              </div>
            )}
          </div>
        </div>

        {err && <div className="card p-4 text-sm text-danger animate-fade-in">{err}</div>}

        {!deptId && !err && (
          <div className="table-wrap">
            <div className="empty-state">
              <div className="empty-icon"><Hourglass size={24} /></div>
              <p className="empty-title">Enter a department ID</p>
              <p className="empty-desc">Enter a department ID above to load today&apos;s queue.</p>
            </div>
          </div>
        )}

        {queue && (
          <div className="grid gap-4 lg:grid-cols-3 animate-fade-in-up delay-150">
            <Section title="Now Serving" icon={PhoneCall} count={active.length}>
              {active.length === 0 ? (
                <p className="text-xs text-faint px-1 py-4 text-center">No active token.</p>
              ) : (
                active.map((t) => <TokenCard key={t.id} token={t} onCall={handleCall} onComplete={handleComplete} />)
              )}
            </Section>

            <Section title="Waiting" icon={Users} count={waiting.length}>
              {waiting.length === 0 ? (
                <p className="text-xs text-faint px-1 py-4 text-center">Queue is empty.</p>
              ) : (
                waiting.map((t) => <TokenCard key={t.id} token={t} onCall={handleCall} onComplete={handleComplete} />)
              )}
            </Section>

            <Section title="Completed" icon={ClipboardCheck} count={done.length}>
              {done.length === 0 ? (
                <p className="text-xs text-faint px-1 py-4 text-center">None yet today.</p>
              ) : (
                done.map((t) => <TokenCard key={t.id} token={t} onCall={handleCall} onComplete={handleComplete} />)
              )}
            </Section>
          </div>
        )}
      </main>
    </>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="text-center">
      <div className="text-xl font-bold text-fg">{value}</div>
      <div className="text-xs text-faint">{label}</div>
    </div>
  );
}

function Section({ title, icon: Icon, count, children }: {
  title: string;
  icon: React.ElementType;
  count: number;
  children: React.ReactNode;
}) {
  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2 px-1">
        <Icon size={14} className="text-primary" />
        <h3 className="text-sm font-semibold text-fg">{title}</h3>
        <span className="badge badge-surface">{count}</span>
      </div>
      <div className="space-y-2">{children}</div>
    </div>
  );
}
