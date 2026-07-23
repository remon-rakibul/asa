"use client";

// Hospital → Department → Doctor drill-down (the original portal flow),
// extracted from app/portal/page.tsx when the home became the marketplace
// search. Kept intact — including the department-pick prompt prewarm — as the
// secondary "browse by hospital" path.

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { useLang } from "@/lib/i18n";
import {
  ArrowLeft,
  Building2,
  ChevronRight,
  MapPin,
  Phone,
  Sparkles,
  Stethoscope,
  Users,
} from "lucide-react";
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

export default function HospitalBrowse({
  onVoiceCall,
}: {
  onVoiceCall: (args: { clinicId?: number; hospitalId?: number; label?: string }) => void;
}) {
  const router = useRouter();
  const { t } = useLang();

  const [hospitals, setHospitals] = useState<Hospital[]>([]);
  const [hospital, setHospital] = useState<Hospital | null>(null);
  const [departments, setDepartments] = useState<Department[]>([]);
  const [department, setDepartment] = useState<Department | null>(null);
  const [doctors, setDoctors] = useState<Doctor[]>([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [stepLoading, setStepLoading] = useState(false);

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
    <div className="space-y-5">
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
                    <span className="flex shrink-0 items-center justify-center rounded-2xl text-white shadow-lg transition-transform duration-300 group-hover:scale-110"
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
              onClick={() => onVoiceCall({ hospitalId: hospital.id, label: hospital.name })}
              className="btn-ghost ml-auto flex items-center gap-1.5 px-2.5 py-1.5 text-xs font-semibold text-primary"
              title={t("speakVoice")}
            >
              <Phone size={13} /> {t("speakVoice")}
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
            onClick={() => onVoiceCall({ clinicId: department.id, label: department.name })}
            className="flex w-full items-center justify-center gap-2 rounded-2xl border border-border bg-surface/80 px-6 py-3 text-sm font-semibold text-fg backdrop-blur-sm transition hover:border-indigo-500/40 active:scale-[0.98]"
          >
            <Phone size={15} className="text-primary" />
            {t("bookByVoice")}
          </button>

          {doctors.length > 0 && (
            <p className="text-center text-xs text-faint">Or choose a specific doctor above</p>
          )}
        </section>
      )}
    </div>
  );
}
