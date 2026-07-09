"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import {
  Phone,
  Stethoscope,
  HeartPulse,
  ChevronRight,
  Sparkles,
  MessageSquare,
  Mic,
  Globe,
  MessageCircle,
  Send,
  Clock,
  Settings,
  Plug,
  X,
} from "lucide-react";
import TopBar from "@/components/layout/TopBar";
import StatsRow from "@/components/dashboard/StatsRow";
import TodayTimeline from "@/components/dashboard/TodayTimeline";
import UpcomingTable from "@/components/dashboard/UpcomingTable";
import { useAuth } from "@/lib/auth";
import { getChannelStats, getSchedule, listAppointments, type ChannelStats } from "@/lib/api";
import {
  buildTodayTimeline,
  dashboardStats,
  upcomingAppointments,
  type TimelineSlot,
} from "@/lib/derive";
import type { Appointment, ScheduleRow, StatsData } from "@/types";

// ── Landing page (shown to unauthenticated visitors) ───────────────────────

function LandingPage() {
  return (
    // `dark` scopes design tokens to their dark values so this premium splash
    // renders consistently regardless of the user's global theme.
    <div
      className="dark relative flex h-full w-full flex-col overflow-hidden"
      style={{
        background:
          "radial-gradient(ellipse 100% 60% at 50% -10%, #131a36 0%, #070b18 45%, #050912 100%)",
      }}
    >

      {/* Background radial glows */}
      <div className="pointer-events-none fixed inset-0 overflow-hidden" aria-hidden>
        <div
          className="absolute -top-40 left-1/2 h-[700px] w-[700px] -translate-x-1/2 rounded-full opacity-20 blur-[100px]"
          style={{ background: "radial-gradient(circle, #6366f1 0%, transparent 70%)" }}
        />
        <div
          className="absolute top-1/3 -left-40 h-[500px] w-[500px] rounded-full opacity-10 blur-[80px]"
          style={{ background: "radial-gradient(circle, #8b5cf6 0%, transparent 70%)" }}
        />
        <div
          className="absolute bottom-0 right-0 h-[400px] w-[400px] rounded-full opacity-10 blur-[80px]"
          style={{ background: "radial-gradient(circle, #a78bfa 0%, transparent 70%)" }}
        />
        <div
          className="absolute inset-0 opacity-[0.02]"
          style={{
            backgroundImage: `linear-gradient(var(--fg) 1px, transparent 1px), linear-gradient(90deg, var(--fg) 1px, transparent 1px)`,
            backgroundSize: "40px 40px",
          }}
        />
      </div>

      {/* Top nav */}
      <nav className="relative z-10 mx-auto flex w-full max-w-5xl shrink-0 items-center justify-between px-6 py-4">
        <div className="flex items-center gap-3">
          <span
            className="flex h-10 w-10 items-center justify-center rounded-2xl text-white shadow-lg"
            style={{ background: "var(--brand-grad)" }}
          >
            <Stethoscope size={18} />
          </span>
          <div>
            <div className="text-sm font-bold leading-tight text-fg">Clinic Console</div>
            <div className="text-[10px] leading-tight text-faint">AI Appointment System</div>
          </div>
        </div>
        <div className="flex items-center gap-3">
          <Link
            href="/login"
            className="flex items-center gap-1.5 rounded-xl border border-white/10 bg-surface/80 px-4 py-2 text-xs font-semibold text-muted backdrop-blur-sm transition hover:border-indigo-500/30 hover:text-fg"
          >
            Staff login
          </Link>
        </div>
      </nav>

      {/* Content scrolls only when the window is too short; otherwise centered. */}
      <div className="relative z-10 min-h-0 flex-1 overflow-y-auto">
      <div className="flex min-h-full flex-col justify-center gap-5 px-4 py-4">

      {/* Hero */}
      <div className="flex flex-col items-center text-center animate-fade-in-up">
        <div className="mb-4 inline-flex items-center gap-2 rounded-full border border-white/10 bg-surface/50 px-4 py-1.5 text-xs font-semibold text-indigo-300 backdrop-blur-sm">
          <Sparkles size={11} />
          AI-powered · Bangla · WhatsApp · Voice
        </div>

        <h1 className="text-4xl font-extrabold tracking-tight text-white drop-shadow-lg sm:text-5xl">
          Smart appointments,
          <br />
          <span className="gradient-text-animated">powered by AI</span>
        </h1>
        <p className="mt-4 max-w-md text-sm text-white/60">
          রোগীদের জন্য সহজ বুকিং, হাসপাতাল স্টাফদের জন্য স্মার্ট ম্যানেজমেন্ট।
        </p>
        <p className="mt-1 max-w-md text-xs text-white/35">
          Simple booking for patients · Smart management for hospitals.
        </p>

        {/* Channel trust strip */}
        <div className="mt-5 flex flex-wrap items-center justify-center gap-x-5 gap-y-2 text-white/45">
          {[
            { icon: MessageCircle, label: "WhatsApp" },
            { icon: Phone, label: "Voice" },
            { icon: Send, label: "SMS" },
            { icon: Globe, label: "Web" },
          ].map(({ icon: Icon, label }) => (
            <span key={label} className="inline-flex items-center gap-1.5 text-xs font-medium">
              <Icon size={13} className="text-indigo-300/70" /> {label}
            </span>
          ))}
        </div>
      </div>

      {/* Role cards */}
      <div className="mx-auto grid w-full max-w-3xl grid-cols-1 gap-4 sm:grid-cols-2">

        {/* Patient card */}
        <Link
          href="/portal"
          className="group relative overflow-hidden rounded-2xl border border-white/5 bg-surface/80 p-6 backdrop-blur-sm transition-all duration-300 hover:border-indigo-500/40 hover:shadow-[0_0_40px_-8px_rgba(99,102,241,0.5)] active:scale-[0.98] animate-fade-in-up"
          style={{ animationDelay: "100ms" }}
        >
          <div className="absolute inset-x-0 top-0 h-[2px]" style={{ background: "var(--brand-grad)" }} />

          <div
            className="flex h-14 w-14 shrink-0 items-center justify-center rounded-2xl text-white shadow-lg transition-transform duration-300 group-hover:scale-110"
            style={{ background: "var(--brand-grad)" }}
          >
            <HeartPulse size={26} />
          </div>

          <h2 className="mt-5 text-xl font-bold text-fg">For Patients</h2>
          <p className="mt-1.5 text-sm leading-relaxed text-muted">
            Book appointments with your doctor in minutes using AI, WhatsApp, or voice.
          </p>
          <p className="mt-1 text-xs text-faint">
            আপনার পছন্দের হাসপাতালে অ্যাপয়েন্টমেন্ট নিন।
          </p>

          <div className="mt-6 flex items-center gap-2 text-sm font-semibold text-indigo-400 transition-all group-hover:gap-3">
            Book an appointment <ChevronRight size={15} />
          </div>
        </Link>

        {/* Staff card */}
        <Link
          href="/login"
          className="group relative overflow-hidden rounded-2xl border border-white/5 bg-surface/80 p-6 backdrop-blur-sm transition-all duration-300 hover:border-indigo-500/40 hover:shadow-[0_0_40px_-8px_rgba(99,102,241,0.5)] active:scale-[0.98] animate-fade-in-up"
          style={{ animationDelay: "200ms" }}
        >
          <div
            className="absolute inset-x-0 top-0 h-[2px]"
            style={{ background: "linear-gradient(135deg, #64748b, #94a3b8)" }}
          />

          <div className="flex h-14 w-14 shrink-0 items-center justify-center rounded-2xl bg-surface-3 text-muted shadow transition-all duration-300 group-hover:scale-110 group-hover:bg-indigo-500/20 group-hover:text-indigo-400">
            <Stethoscope size={26} />
          </div>

          <h2 className="mt-5 text-xl font-bold text-fg">For Hospital Staff</h2>
          <p className="mt-1.5 text-sm leading-relaxed text-muted">
            Manage appointments, patients, and AI channels. Multi-hospital support.
          </p>
          <p className="mt-1 text-xs text-faint">
            ক্লিনিক ম্যানেজমেন্ট, ডাক্তার ও শিডিউল নিয়ন্ত্রণ।
          </p>

          <div className="mt-6 flex items-center gap-2 text-sm font-semibold text-muted transition-all group-hover:gap-3 group-hover:text-indigo-400">
            Staff sign in <ChevronRight size={15} />
          </div>
        </Link>
      </div>

      {/* Feature strip */}
      <div
        className="mx-auto w-full max-w-3xl border-t border-white/5 px-4 pt-5 animate-fade-in-up"
        style={{ animationDelay: "300ms" }}
      >
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
          {[
            { icon: MessageSquare, title: "AI Bangla Assistant", desc: "Conversational booking in Bengali and English" },
            { icon: Mic, title: "WhatsApp & Voice", desc: "Book over WhatsApp, SMS, or phone call" },
            { icon: Globe, title: "Multi-hospital", desc: "One account across every hospital on the platform" },
          ].map(({ icon: Icon, title, desc }) => (
            <div key={title} className="flex items-start gap-3">
              <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-indigo-500/10 text-indigo-400">
                <Icon size={16} />
              </div>
              <div>
                <div className="text-sm font-semibold text-fg">{title}</div>
                <div className="mt-0.5 text-xs text-faint">{desc}</div>
              </div>
            </div>
          ))}
        </div>
      </div>

      </div>
      </div>
    </div>
  );
}

// ── Admin dashboard (shown to authenticated clinic users) ──────────────────

function DashboardContent() {
  const [appts, setAppts] = useState<Appointment[]>([]);
  const [schedule, setSchedule] = useState<ScheduleRow[]>([]);
  const [channelStats, setChannelStats] = useState<ChannelStats[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    setError("");
    try {
      const [a, s, cs] = await Promise.all([
        listAppointments({}),
        getSchedule(),
        getChannelStats(),
      ]);
      setAppts(a);
      setSchedule(s);
      setChannelStats(cs);
    } catch (ex: unknown) {
      setError(ex instanceof Error ? ex.message : "Could not load dashboard data.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const timeline: TimelineSlot[] = buildTodayTimeline(appts, schedule);
  const stats: StatsData = dashboardStats(appts, timeline);
  const upcoming = upcomingAppointments(appts, 5);
  const voiceChannels = channelStats.filter((c) => c.calls_received > 0 || c.appointments_taken > 0);

  return (
    <>
      <TopBar title="Dashboard" />
      <main className="flex flex-1 flex-col gap-6 overflow-hidden bg-bg p-6">
        {loading ? (
          <div className="space-y-6">
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
              {[1, 2, 3, 4].map((i) => (
                <div key={i} className="skeleton-card rounded-2xl" />
              ))}
            </div>
            <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
              <div className="skeleton-card rounded-2xl h-96" />
              <div className="skeleton-card rounded-2xl h-96" />
            </div>
          </div>
        ) : error ? (
          <div className="card p-4 text-sm text-danger">{error}</div>
        ) : (
          <>
            {appts.length === 0 && <GettingStarted />}
            <StatsRow stats={stats} />
            <div className="grid min-h-0 flex-1 auto-rows-fr grid-cols-1 gap-6 lg:grid-cols-2">
              <TodayTimeline slots={timeline} />
              <UpcomingTable rows={upcoming} onCancelled={load} />
            </div>

            {voiceChannels.length > 0 && (
              <section className="space-y-3">
                <h2 className="text-sm font-semibold uppercase tracking-wide text-faint">
                  Voice numbers
                </h2>
                <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
                  {voiceChannels.map((ch) => (
                    <div key={ch.id} className="card flex items-start gap-3 p-4">
                      <span className="rounded-lg bg-surface-3 p-2 text-muted">
                        <Phone size={18} />
                      </span>
                      <div className="min-w-0 flex-1">
                        <div className="font-medium text-fg">
                          {ch.label || ch.identifier}
                        </div>
                        {ch.label && (
                          <div className="truncate text-xs text-muted">
                            {ch.identifier}
                          </div>
                        )}
                        <div className="mt-2 flex gap-4 text-sm">
                          <span className="text-fg">
                            <strong>{ch.calls_received}</strong>{" "}
                            <span className="text-muted">calls</span>
                          </span>
                          <span className="text-fg">
                            <strong>{ch.appointments_taken}</strong>{" "}
                            <span className="text-muted">appointments</span>
                          </span>
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              </section>
            )}
          </>
        )}
      </main>
    </>
  );
}

// ── Getting-started checklist (shown on a fresh tenant) ────────────────────

function GettingStarted() {
  const [dismissed, setDismissed] = useState(
    () => typeof window !== "undefined" && localStorage.getItem("gs_dismissed") === "1"
  );
  if (dismissed) return null;

  const steps = [
    { icon: Clock, title: "Set your weekly schedule", desc: "Define working hours so patients can book slots.", href: "/schedule" },
    { icon: Settings, title: "Personalize greetings & SMS", desc: "Edit the AI greeting and message templates.", href: "/settings" },
    { icon: Plug, title: "Connect a channel", desc: "Map your WhatsApp, SMS or voice number.", href: "/integrations" },
  ];

  function dismiss() {
    localStorage.setItem("gs_dismissed", "1");
    setDismissed(true);
  }

  return (
    <div className="card relative overflow-hidden p-5 animate-fade-in-up">
      <div className="absolute inset-x-0 top-0 h-[2px]" style={{ background: "var(--brand-grad)" }} />
      <button onClick={dismiss} aria-label="Dismiss" className="modal-close absolute right-3 top-3">
        <X size={15} />
      </button>
      <div className="mb-4 flex items-center gap-2">
        <Sparkles size={16} className="text-primary" />
        <h2 className="text-sm font-bold text-fg">Getting started</h2>
      </div>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
        {steps.map(({ icon: Icon, title, desc, href }) => (
          <Link
            key={href}
            href={href}
            className="group flex flex-col gap-1.5 rounded-xl border border-border bg-surface-2 p-4 transition-all hover:border-primary/30 hover:shadow-sm"
          >
            <span className="flex h-9 w-9 items-center justify-center rounded-lg bg-[var(--brand-soft)] text-primary">
              <Icon size={16} />
            </span>
            <span className="mt-1 text-sm font-semibold text-fg">{title}</span>
            <span className="text-xs text-muted">{desc}</span>
            <span className="mt-1 flex items-center gap-1 text-xs font-medium text-primary opacity-0 transition-opacity group-hover:opacity-100">
              Open <ChevronRight size={12} />
            </span>
          </Link>
        ))}
      </div>
    </div>
  );
}

// ── Root page — landing for visitors, dashboard for staff ──────────────────

export default function HomePage() {
  const { clinic } = useAuth();
  if (!clinic) return <LandingPage />;
  return <DashboardContent />;
}
