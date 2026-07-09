"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import {
  Building2,
  Stethoscope,
  ChevronRight,
  CalendarDays,
  LogOut,
  ArrowLeft,
  Users,
  Sparkles,
  MapPin,
  Sun,
  Moon,
  Phone,
} from "lucide-react";
import { usePatientAuth } from "@/lib/patientAuth";
import { useTheme } from "@/lib/theme";
import VoiceCall from "@/components/portal/VoiceCall";
import {
  Department,
  Doctor,
  Hospital,
  doctorPhotoUrl,
  portalListDepartments,
  portalListDoctors,
  portalListHospitals,
  prewarmChat,
} from "@/lib/api";

function greeting() {
  const h = new Date().getHours();
  if (h < 12) return "Good morning";
  if (h < 17) return "Good afternoon";
  return "Good evening";
}

function initials(name: string) {
  return name.split(" ").slice(0, 2).map((w) => w[0]).join("").toUpperCase();
}

const STEPS = ["Hospital", "Department", "Doctor"] as const;

function CardGridSkeleton() {
  return (
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2" aria-busy="true">
      {[0, 1, 2, 3].map((i) => (
        <div
          key={i}
          className="skeleton h-[92px] rounded-2xl"
          style={{ animationDelay: `${i * 70}ms` }}
        />
      ))}
    </div>
  );
}

export default function PortalHomePage() {
  const { account, logout } = usePatientAuth();
  const { theme, toggle: toggleTheme } = useTheme();
  const router = useRouter();

  const [hospitals, setHospitals] = useState<Hospital[]>([]);
  const [hospital, setHospital] = useState<Hospital | null>(null);
  const [departments, setDepartments] = useState<Department[]>([]);
  const [department, setDepartment] = useState<Department | null>(null);
  const [doctors, setDoctors] = useState<Doctor[]>([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [stepLoading, setStepLoading] = useState(false);
  const [voiceCall, setVoiceCall] = useState<
    { clinicId?: number; hospitalId?: number; label?: string } | null
  >(null);

  const step = !hospital ? 0 : !department ? 1 : 2;

  useEffect(() => {
    portalListHospitals()
      .then((h) => { setHospitals(h); setLoading(false); })
      .catch((e) => { setError(e instanceof Error ? e.message : "Could not load hospitals."); setLoading(false); });
  }, []);

  async function pickHospital(h: Hospital) {
    setHospital(h); setDepartment(null); setDepartments([]); setDoctors([]); setError("");
    setStepLoading(true);
    try { setDepartments(await portalListDepartments(h.id)); }
    catch (e) { setError(e instanceof Error ? e.message : "Could not load departments."); }
    finally { setStepLoading(false); }
  }

  async function pickDepartment(d: Department) {
    setDepartment(d); setDoctors([]); setError("");
    setStepLoading(true);
    try {
      const docs = await portalListDoctors(d.id);
      setDoctors(docs);
      // While the patient reads the doctor list, heat the LLM's prompt cache
      // so the chat greeting starts in seconds, not minutes. A single-doctor
      // department warms with that doctor pre-selected — tapping their tile is
      // the dominant path, and the prompt's doctor section must match for the
      // warm prefix to count. (The book page can't prewarm itself: its
      // greeting turn starts immediately on mount and would only be delayed.)
      prewarmChat(d.id, docs.length === 1 ? docs[0].id : undefined);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not load doctors.");
      prewarmChat(d.id);
    } finally {
      setStepLoading(false);
    }
  }

  // Jump back to an earlier step by clicking its pill.
  function goToStep(target: number) {
    if (target >= step) return;
    setError("");
    if (target === 0) { setHospital(null); setDepartment(null); setDepartments([]); setDoctors([]); }
    else if (target === 1) { setDepartment(null); setDoctors([]); }
  }

  function startBooking(doctor?: Doctor) {
    if (!department) return;
    const p = new URLSearchParams({ clinic: String(department.id) });
    if (hospital) {
      p.set("hospital", hospital.name);
      p.set("hospitalId", String(hospital.id));
    }
    p.set("department", department.name);
    if (doctor) {
      p.set("doctor", doctor.name);
      p.set("doctorId", String(doctor.id));
      if (doctor.degrees) p.set("degrees", doctor.degrees);
      if (doctor.specialty) p.set("specialty", doctor.specialty);
    }
    router.push(`/portal/book?${p}`);
  }

  return (
    <div className="relative min-h-screen w-full overflow-y-auto bg-bg">

      {/* ── Background radial glows ─────────────────────────────────── */}
      <div className="pointer-events-none fixed inset-0 overflow-hidden">
        <div className="absolute -top-40 left-1/2 h-[600px] w-[600px] -translate-x-1/2 rounded-full opacity-20 blur-[100px]"
          style={{ background: "radial-gradient(circle, #6366f1 0%, transparent 70%)" }} />
        <div className="absolute top-1/2 -left-32 h-[400px] w-[400px] rounded-full opacity-10 blur-[80px]"
          style={{ background: "radial-gradient(circle, #8b5cf6 0%, transparent 70%)" }} />
        <div className="absolute bottom-0 right-0 h-[300px] w-[300px] rounded-full opacity-10 blur-[80px]"
          style={{ background: "radial-gradient(circle, #a78bfa 0%, transparent 70%)" }} />
      </div>

      {/* ── Hero header ────────────────────────────────────────────── */}
      <div className="relative overflow-hidden" style={{ background: "var(--brand-grad)" }}>
        {/* decorative overlays */}
        <div className="absolute inset-0" style={{ background: "linear-gradient(180deg, rgba(0,0,0,0.05) 0%, rgba(0,0,0,0.25) 100%)" }} />
        <div className="pointer-events-none absolute -right-20 -top-20 h-72 w-72 rounded-full bg-white/10 blur-3xl" />
        <div className="pointer-events-none absolute bottom-0 left-1/4 h-32 w-64 rounded-full bg-white/5 blur-2xl" />

        <div className="relative mx-auto max-w-3xl px-5 pb-8 pt-5">
          {/* nav */}
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-3">
              <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-2xl bg-white/20 text-sm font-bold text-white shadow-lg backdrop-blur-sm">
                {account ? initials(account.name) : "?"}
              </div>
              <div>
                <p className="text-[11px] font-medium text-white/70">{greeting()}</p>
                <p className="text-sm font-bold text-white">{account?.name}</p>
              </div>
            </div>
            <div className="flex items-center gap-2">
              <Link href="/portal/appointments"
                className="flex items-center gap-1.5 rounded-xl bg-white/20 px-3 py-1.5 text-xs font-semibold text-white backdrop-blur-sm transition hover:bg-white/30">
                <CalendarDays size={13} /> My appointments
              </Link>
              <button onClick={toggleTheme}
                aria-label={theme === "dark" ? "Switch to light mode" : "Switch to dark mode"}
                className="flex h-8 w-8 items-center justify-center rounded-xl bg-white/20 text-white backdrop-blur-sm transition hover:bg-white/30">
                {theme === "dark" ? <Sun size={14} /> : <Moon size={14} />}
              </button>
              <button onClick={logout} aria-label="Sign out" title="Sign out"
                className="flex h-8 w-8 items-center justify-center rounded-xl bg-white/20 text-white backdrop-blur-sm transition hover:bg-white/30">
                <LogOut size={14} />
              </button>
            </div>
          </div>

          {/* title */}
          <div className="mt-6">
            <h1 className="text-3xl font-extrabold tracking-tight text-white drop-shadow">
              Book an Appointment
            </h1>
            <p className="mt-1 text-sm text-white/65">Fast, simple, and hassle-free</p>
          </div>

          {/* step pills */}
          <div className="mt-5 flex items-center gap-2">
            {STEPS.map((label, i) => {
              const done = i < step;
              const active = i === step;
              return (
                <div key={label} className="flex items-center gap-1.5">
                  <button
                    type="button"
                    onClick={() => goToStep(i)}
                    disabled={!done}
                    aria-label={done ? `Back to ${label}` : label}
                    className={`flex items-center gap-1.5 rounded-full px-3 py-1.5 text-xs font-semibold transition-all ${
                      done ? "bg-white text-indigo-600 shadow hover:brightness-95 cursor-pointer" :
                      active ? "bg-white/30 text-white ring-1 ring-white/60 shadow cursor-default" :
                      "bg-white/10 text-white/40 cursor-default"
                    }`}>
                    <span className={`flex h-4 w-4 items-center justify-center rounded-full text-[10px] font-black ${
                      done ? "bg-indigo-100 text-indigo-600" :
                      active ? "bg-white/30" : "bg-white/10"
                    }`}>{i + 1}</span>
                    {label}
                  </button>
                  {i < 2 && <ChevronRight size={10} className="shrink-0 text-white/30" />}
                </div>
              );
            })}
          </div>
        </div>
      </div>

      {/* ── Content ────────────────────────────────────────────────── */}
      <main className="relative mx-auto max-w-3xl px-4 pb-12 pt-7 space-y-5">

        {error && (
          <div role="alert" className="rounded-2xl border border-danger/20 bg-danger/10 px-4 py-3 text-sm text-danger animate-fade-in">
            {error}
          </div>
        )}

        {/* Step 0 — Hospital */}
        {step === 0 && (
          <section className="space-y-4 animate-fade-in-up">
            <div className="flex items-center gap-2">
              <div className="h-5 w-1 rounded-full" style={{ background: "var(--brand-grad)" }} />
              <p className="text-xs font-bold uppercase tracking-widest text-faint">Choose a Hospital</p>
            </div>

            {loading ? (
              <div className="flex justify-center gap-2 py-16">
                {[0,1,2].map(i => (
                  <span key={i} className="h-3 w-3 animate-bounce rounded-full"
                    style={{ background: "var(--brand-grad)", animationDelay: `${i*150}ms` }} />
                ))}
              </div>
            ) : hospitals.length === 0 ? (
              <div className="rounded-2xl border border-border/50 bg-surface/50 p-12 text-center text-sm text-faint">
                No hospitals available yet.
              </div>
            ) : (
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                {hospitals.map((h, idx) => (
                  <button key={h.id} onClick={() => pickHospital(h)}
                    className="group relative overflow-hidden rounded-2xl border border-border bg-surface/80 p-5 text-left backdrop-blur-sm transition-all duration-300 hover:border-indigo-500/40 hover:shadow-[0_0_40px_-8px_rgba(99,102,241,0.5)] active:scale-[0.98] animate-fade-in-up"
                    style={{ animationDelay: `${idx * 70}ms` }}>
                    {/* gradient top accent */}
                    <div className="absolute inset-x-0 top-0 h-[2px]" style={{ background: "var(--brand-grad)" }} />

                    <div className="flex items-center gap-4">
                      <span className="flex h-13 w-13 shrink-0 items-center justify-center rounded-2xl text-white shadow-lg transition-transform duration-300 group-hover:scale-110"
                        style={{ background: "var(--brand-grad)", height: "52px", width: "52px" }}>
                        <Building2 size={22} />
                      </span>
                      <div className="min-w-0 flex-1">
                        <p className="truncate text-base font-bold text-fg">{h.name}</p>
                        {h.address && (
                          <p className="mt-0.5 flex items-center gap-1 truncate text-xs text-faint">
                            <MapPin size={10} className="shrink-0" />{h.address}
                          </p>
                        )}
                      </div>
                      <ChevronRight size={18} className="shrink-0 text-faint/50 transition-all duration-300 group-hover:translate-x-1 group-hover:text-indigo-400" />
                    </div>
                  </button>
                ))}
              </div>
            )}
          </section>
        )}

        {/* Step 1 — Department */}
        {step === 1 && hospital && (
          <section className="space-y-4 animate-slide-up">
            <div className="flex items-center gap-3">
              <button onClick={() => { setHospital(null); setDepartments([]); }}
                aria-label="Back to hospitals"
                className="flex h-8 w-8 items-center justify-center rounded-xl border border-border bg-surface/80 text-muted transition hover:border-indigo-500/50 hover:text-indigo-400">
                <ArrowLeft size={14} />
              </button>
              <div className="flex items-center gap-2">
                <div className="h-5 w-1 rounded-full" style={{ background: "var(--brand-grad)" }} />
                <p className="text-xs font-bold uppercase tracking-widest text-faint">
                  Departments · <span className="normal-case font-semibold text-fg">{hospital.name}</span>
                </p>
              </div>
              <button
                onClick={() => setVoiceCall({ hospitalId: hospital.id, label: hospital.name })}
                className="btn-ghost ml-auto flex items-center gap-1.5 px-2.5 py-1.5 text-xs font-semibold text-primary"
                title="ভয়েসে কথা বলুন"
              >
                <Phone size={13} /> ভয়েসে কথা বলুন
              </button>
            </div>

            {stepLoading ? (
              <CardGridSkeleton />
            ) : departments.length === 0 ? (
              <div className="rounded-2xl border border-border bg-surface/50 p-10 text-center text-sm text-faint">
                No departments found.
              </div>
            ) : (
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                {departments.map((d, idx) => (
                  <button key={d.id} onClick={() => pickDepartment(d)}
                    className="group relative overflow-hidden rounded-2xl border border-border bg-surface/80 p-5 text-left backdrop-blur-sm transition-all duration-300 hover:border-indigo-500/40 hover:shadow-[0_0_40px_-8px_rgba(99,102,241,0.5)] active:scale-[0.98] animate-fade-in-up"
                    style={{ animationDelay: `${idx * 70}ms` }}>
                    <div className="absolute inset-x-0 top-0 h-[2px]" style={{ background: "var(--brand-grad)" }} />
                    <div className="flex items-center gap-4">
                      <span className="flex h-[52px] w-[52px] shrink-0 items-center justify-center rounded-2xl bg-indigo-500/15 text-indigo-400 transition-transform duration-300 group-hover:scale-110 group-hover:bg-indigo-500/25">
                        <Users size={22} />
                      </span>
                      <div className="min-w-0 flex-1">
                        <p className="truncate text-base font-bold text-fg">{d.name}</p>
                        {d.floor && <p className="mt-0.5 text-xs text-faint">Floor {d.floor}</p>}
                      </div>
                      <ChevronRight size={18} className="shrink-0 text-faint/50 transition-all duration-300 group-hover:translate-x-1 group-hover:text-indigo-400" />
                    </div>
                  </button>
                ))}
              </div>
            )}
          </section>
        )}

        {/* Step 2 — Doctor + start */}
        {step === 2 && department && (
          <section className="space-y-4 animate-slide-up">
            <div className="flex items-center gap-3">
              <button onClick={() => { setDepartment(null); setDoctors([]); }}
                aria-label="Back to departments"
                className="flex h-8 w-8 items-center justify-center rounded-xl border border-border bg-surface/80 text-muted transition hover:border-indigo-500/50 hover:text-indigo-400">
                <ArrowLeft size={14} />
              </button>
              <div className="flex items-center gap-2">
                <div className="h-5 w-1 rounded-full" style={{ background: "var(--brand-grad)" }} />
                <p className="text-xs font-bold uppercase tracking-widest text-faint">
                  Doctors · <span className="normal-case font-semibold text-fg">{department.name}</span>
                </p>
              </div>
            </div>

            {stepLoading && <CardGridSkeleton />}

            {!stepLoading && doctors.length === 0 && (
              <div className="rounded-2xl border border-border bg-surface/50 p-6 text-center text-sm text-faint">
                No specific doctors listed for this department — you can still start booking below and the assistant will help.
              </div>
            )}

            {!stepLoading && doctors.length > 0 && (
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                {doctors.map((doc, idx) => (
                  <button key={doc.id} onClick={() => startBooking(doc)}
                    className="group relative overflow-hidden rounded-2xl border border-border bg-surface/80 p-5 text-left backdrop-blur-sm transition-all duration-300 hover:border-indigo-500/40 hover:shadow-[0_0_40px_-8px_rgba(99,102,241,0.5)] active:scale-[0.98] animate-fade-in-up"
                    style={{ animationDelay: `${idx * 70}ms` }}>
                    <div className="absolute inset-x-0 top-0 h-[2px]" style={{ background: "var(--brand-grad)" }} />
                    <div className="flex items-start gap-4">
                      {doc.has_photo ? (
                        // eslint-disable-next-line @next/next/no-img-element
                        <img
                          src={doctorPhotoUrl(doc.id)}
                          alt={doc.name}
                          className="h-[52px] w-[52px] shrink-0 rounded-2xl object-cover shadow-lg transition-transform duration-300 group-hover:scale-110"
                        />
                      ) : (
                        <span className="flex h-[52px] w-[52px] shrink-0 items-center justify-center rounded-2xl text-white shadow-lg transition-transform duration-300 group-hover:scale-110"
                          style={{ background: "var(--brand-grad)" }}>
                          <Stethoscope size={20} />
                        </span>
                      )}
                      <div className="min-w-0 flex-1">
                        <p className="truncate text-base font-bold text-fg">{doc.name}</p>
                        {doc.degrees && (
                          <p className="mt-0.5 truncate text-xs font-semibold text-primary">{doc.degrees}</p>
                        )}
                        {doc.specialty && <p className="mt-0.5 truncate text-xs text-faint">{doc.specialty}</p>}
                        {doc.description && (
                          <p className="mt-1.5 text-xs leading-relaxed text-faint line-clamp-2">{doc.description}</p>
                        )}
                      </div>
                      <ChevronRight size={18} className="mt-4 shrink-0 text-faint/50 transition-all duration-300 group-hover:translate-x-1 group-hover:text-indigo-400" />
                    </div>
                  </button>
                ))}
              </div>
            )}

            {/* Primary CTA */}
            <button onClick={() => startBooking()}
              className="group relative w-full overflow-hidden rounded-2xl p-[1.5px] shadow-[0_0_30px_-5px_rgba(99,102,241,0.6)] transition-all hover:shadow-[0_0_50px_-5px_rgba(99,102,241,0.8)] active:scale-[0.98]"
              style={{ background: "var(--brand-grad)" }}>
              <div className="flex w-full items-center justify-center gap-3 rounded-2xl bg-surface px-6 py-4 transition-all duration-300 group-hover:bg-transparent">
                <Sparkles size={17} className="text-indigo-300 transition group-hover:text-white" />
                <span className="text-sm font-bold text-fg transition group-hover:text-white">
                  Start booking with the AI assistant
                </span>
                <ChevronRight size={16} className="ml-auto text-faint transition-all group-hover:translate-x-1 group-hover:text-white" />
              </div>
            </button>

            {/* Voice alternative */}
            <button
              onClick={() => setVoiceCall({ clinicId: department.id, label: department.name })}
              className="flex w-full items-center justify-center gap-2 rounded-2xl border border-border bg-surface/80 px-6 py-3 text-sm font-semibold text-fg backdrop-blur-sm transition hover:border-indigo-500/40 active:scale-[0.98]"
            >
              <Phone size={15} className="text-primary" />
              ফোনে কথা বলে বুক করুন
            </button>

            {doctors.length > 0 && (
              <p className="text-center text-xs text-faint">Or choose a specific doctor above</p>
            )}
          </section>
        )}
      </main>

      {voiceCall && (
        <VoiceCall
          clinicId={voiceCall.clinicId}
          hospitalId={voiceCall.hospitalId}
          label={voiceCall.label}
          onClose={() => setVoiceCall(null)}
        />
      )}
    </div>
  );
}
