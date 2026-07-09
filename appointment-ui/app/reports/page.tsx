"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { format, parseISO, subDays } from "date-fns";
import {
  BarChart3, CalendarCheck, CheckCircle2, UserX, CircleSlash, MessageSquare,
} from "lucide-react";
import TopBar from "@/components/layout/TopBar";
import BarChart, { type BarDatum } from "@/components/reports/BarChart";
import LineChart from "@/components/reports/LineChart";
import { getReportSummary, type ReportSummary } from "@/lib/api";

function StatCard({
  icon: Icon, label, value, accent,
}: {
  icon: React.ElementType; label: string; value: string; accent: string;
}) {
  return (
    <div className="card flex items-center gap-4 p-5">
      <span className="flex h-11 w-11 items-center justify-center rounded-xl" style={{ background: "var(--brand-soft)" }}>
        <Icon size={20} style={{ color: accent }} />
      </span>
      <div>
        <div className="text-2xl font-bold tabular-nums text-fg">{value}</div>
        <div className="text-xs text-muted">{label}</div>
      </div>
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="card p-5">
      <h3 className="mb-4 text-sm font-semibold text-fg">{title}</h3>
      {children}
    </div>
  );
}

const SMS_STATUS_COLORS: Record<string, string> = {
  sent: "var(--success)",
  failed: "var(--danger)",
  skipped: "var(--warning)",
};

export default function ReportsPage() {
  const [dateFrom, setDateFrom] = useState(() => format(subDays(new Date(), 29), "yyyy-MM-dd"));
  const [dateTo, setDateTo] = useState(() => format(new Date(), "yyyy-MM-dd"));
  const [data, setData] = useState<ReportSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState("");

  const load = useCallback(async (from: string, to: string) => {
    setLoading(true);
    setErr("");
    try {
      setData(await getReportSummary(from, to));
    } catch {
      setErr("Could not load reports. You may not have access, or the date range is invalid.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(dateFrom, dateTo); }, [load, dateFrom, dateTo]);

  const a = data?.appointments;

  const trend = useMemo(
    () => (a?.daily ?? []).map((d) => ({ label: format(parseISO(d.day), "d MMM"), value: d.count })),
    [a],
  );
  const perDoctor: BarDatum[] = useMemo(
    () => (a?.per_doctor ?? []).slice(0, 8).map((d) => ({ label: d.name, value: d.count })),
    [a],
  );
  const smsBars: BarDatum[] = useMemo(
    () =>
      Object.entries(data?.sms.by_status ?? {}).map(([status, n]) => ({
        label: status,
        value: n,
        color: SMS_STATUS_COLORS[status] ?? "var(--brand-grad)",
      })),
    [data],
  );
  const smsKindBars: BarDatum[] = useMemo(
    () => (data?.sms.by_kind ?? []).map((k) => ({ label: k.kind.replace(/_/g, " "), value: k.count })),
    [data],
  );

  return (
    <>
      <TopBar title="Reports" />
      <main className="flex-1 overflow-y-auto bg-bg p-6">
        <div className="mx-auto max-w-6xl space-y-4">
          <div className="flex flex-wrap items-center gap-3">
            <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-[var(--brand-soft)]">
              <BarChart3 size={18} className="text-primary" />
            </div>
            <div>
              <h2 className="text-sm font-semibold text-fg">Reports</h2>
              <p className="text-xs text-muted">Appointment, SMS and channel analytics for your department.</p>
            </div>
            <div className="ml-auto flex items-end gap-2">
              <label className="flex flex-col gap-1">
                <span className="text-xs font-medium text-muted">From</span>
                <input type="date" className="input w-40 text-sm" value={dateFrom} max={dateTo} onChange={(e) => setDateFrom(e.target.value)} />
              </label>
              <label className="flex flex-col gap-1">
                <span className="text-xs font-medium text-muted">To</span>
                <input type="date" className="input w-40 text-sm" value={dateTo} min={dateFrom} onChange={(e) => setDateTo(e.target.value)} />
              </label>
            </div>
          </div>

          {err && (
            <div className="rounded-2xl border border-danger/30 bg-[var(--danger-bg)] p-4 text-sm text-danger">{err}</div>
          )}

          {loading ? (
            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
              {Array.from({ length: 4 }).map((_, i) => <div key={i} className="skeleton h-20 rounded-2xl" />)}
            </div>
          ) : a ? (
            <>
              <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
                <StatCard icon={CalendarCheck} label="Total booked" value={String(a.total)} accent="var(--primary)" />
                <StatCard icon={CheckCircle2} label="Completion rate" value={`${a.completion_rate}%`} accent="var(--success)" />
                <StatCard icon={UserX} label="No-show rate" value={`${a.no_show_rate}%`} accent="var(--warning)" />
                <StatCard icon={CircleSlash} label="Cancellations" value={String(a.cancelled)} accent="var(--danger)" />
              </div>

              <Section title="Bookings over time">
                <LineChart data={trend} />
              </Section>

              <div className="grid gap-4 lg:grid-cols-2">
                <Section title="Appointments per doctor">
                  <BarChart data={perDoctor} />
                </Section>
                <Section title="SMS delivery health">
                  <div className="space-y-5">
                    <BarChart data={smsBars} emptyText="No SMS sent in this range." />
                    {smsKindBars.length > 0 && (
                      <div>
                        <div className="mb-2 flex items-center gap-1.5 text-xs font-medium text-faint">
                          <MessageSquare size={12} /> By message type
                        </div>
                        <BarChart data={smsKindBars} />
                      </div>
                    )}
                  </div>
                </Section>
              </div>

              {data!.channels.length > 0 && (
                <Section title="Voice channel mix">
                  <BarChart
                    data={data!.channels.map((c) => ({
                      label: c.label || c.identifier,
                      value: c.appointments_taken,
                    }))}
                    emptyText="No voice channels configured."
                  />
                </Section>
              )}
            </>
          ) : null}
        </div>
      </main>
    </>
  );
}
