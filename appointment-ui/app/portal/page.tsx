"use client";

// Marketplace home (foodpanda-style): hero search across every hospital,
// specialty tiles, filter/sort strip, doctor cards with fee + rating +
// next-available chip, a platform-wide AI assistant entry (chat/voice with
// no hospital chosen), and the original hospital drill-down as a secondary
// collapsible browse path.

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import {
  Building2,
  CalendarCheck,
  CalendarDays,
  ChevronDown,
  LogOut,
  MessageCircle,
  Moon,
  Phone,
  Scale,
  Search,
  Sparkles,
  Stethoscope,
  Sun,
} from "lucide-react";
import { usePatientAuth } from "@/lib/patientAuth";
import { LangToggle, useLang } from "@/lib/i18n";
import { useTheme } from "@/lib/theme";
import VoiceCall from "@/components/portal/VoiceCall";
import HospitalBrowse from "@/components/portal/HospitalBrowse";
import DoctorCard from "@/components/portal/DoctorCard";
import SpecialtyTiles from "@/components/portal/SpecialtyTiles";
import SearchFilters from "@/components/portal/SearchFilters";
import {
  DoctorSort,
  Hospital,
  SearchDoctor,
  Specialty,
  portalListHospitals,
  portalListSpecialties,
  portalSearchDoctors,
  prewarmChat,
} from "@/lib/api";

const PAGE_SIZE = 20;

export default function PortalHomePage() {
  const { account, logout } = usePatientAuth();
  const { theme, toggle: toggleTheme } = useTheme();
  const { t } = useLang();
  const router = useRouter();

  const h = new Date().getHours();
  const greetKey = h < 12 ? "goodMorning" : h < 17 ? "goodAfternoon" : "goodEvening";

  // Search + filters
  const [q, setQ] = useState("");
  const [specialty, setSpecialty] = useState("");
  const [hospitalId, setHospitalId] = useState<number | "">("");
  const [maxFee, setMaxFee] = useState("");
  const [sort, setSort] = useState<DoctorSort>("rating");
  const [results, setResults] = useState<SearchDoctor[]>([]);
  const [searching, setSearching] = useState(true);
  const [moreLoading, setMoreLoading] = useState(false);
  const [hasMore, setHasMore] = useState(false);
  const [error, setError] = useState("");
  const pageRef = useRef(0);
  const searchSeq = useRef(0);

  const [specialties, setSpecialties] = useState<Specialty[]>([]);
  const [hospitals, setHospitals] = useState<Hospital[]>([]);
  const [browseOpen, setBrowseOpen] = useState(false);
  const [voiceCall, setVoiceCall] = useState<
    { clinicId?: number; hospitalId?: number; label?: string } | null
  >(null);

  useEffect(() => {
    portalListSpecialties().then(setSpecialties).catch(() => {});
    portalListHospitals().then(setHospitals).catch(() => {});
    // Heat the platform-wide assistant's prompt cache while the patient
    // browses, so the home chat/voice greeting starts fast.
    prewarmChat();
  }, []);

  // Debounced search — any filter change resets to page 0 and replaces results.
  useEffect(() => {
    const seq = ++searchSeq.current;
    setSearching(true);
    setError("");
    const timer = setTimeout(async () => {
      try {
        const rows = await portalSearchDoctors({
          q: q.trim() || undefined,
          specialty: specialty || undefined,
          hospitalId: hospitalId === "" ? undefined : hospitalId,
          maxFee: maxFee.trim() === "" ? undefined : Number(maxFee),
          sort,
          page: 0,
        });
        if (seq !== searchSeq.current) return; // stale response
        pageRef.current = 0;
        setResults(rows);
        setHasMore(rows.length === PAGE_SIZE);
      } catch (e) {
        if (seq !== searchSeq.current) return;
        setError(e instanceof Error ? e.message : t("searchFailed"));
      } finally {
        if (seq === searchSeq.current) setSearching(false);
      }
    }, 350);
    return () => clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [q, specialty, hospitalId, maxFee, sort]);

  async function loadMore() {
    if (moreLoading) return;
    setMoreLoading(true);
    const seq = searchSeq.current;
    try {
      const rows = await portalSearchDoctors({
        q: q.trim() || undefined,
        specialty: specialty || undefined,
        hospitalId: hospitalId === "" ? undefined : hospitalId,
        maxFee: maxFee.trim() === "" ? undefined : Number(maxFee),
        sort,
        page: pageRef.current + 1,
      });
      if (seq !== searchSeq.current) return; // filters changed meanwhile
      pageRef.current += 1;
      setResults((prev) => [...prev, ...rows]);
      setHasMore(rows.length === PAGE_SIZE);
    } catch {
      /* keep what we have */
    } finally {
      setMoreLoading(false);
    }
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
        <div className="absolute inset-0" style={{ background: "linear-gradient(180deg, rgba(0,0,0,0.05) 0%, rgba(0,0,0,0.25) 100%)" }} />
        <div className="pointer-events-none absolute -right-20 -top-20 h-72 w-72 rounded-full bg-white/10 blur-3xl" />
        <div className="pointer-events-none absolute bottom-0 left-1/4 h-32 w-64 rounded-full bg-white/5 blur-2xl" />

        <div className="relative mx-auto w-full max-w-6xl px-5 pb-10 pt-5 lg:px-8">
          {/* nav */}
          <div className="flex items-center justify-between">
            <div className="flex min-w-0 items-center gap-3">
              {/* ASA brand mark */}
              <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-2xl bg-white text-sm font-black tracking-tight text-indigo-700 shadow-lg">
                {t("brandName")}
              </div>
              <div className="min-w-0">
                <p className="text-sm font-extrabold tracking-wide text-white">{t("brandName")}</p>
                <p className="truncate text-[11px] font-medium text-white/70">{t("brandTagline")}</p>
              </div>
              <div className="ml-3 hidden min-w-0 border-l border-white/25 pl-4 sm:block">
                <p className="text-[11px] font-medium text-white/70">{t(greetKey)}</p>
                <p className="truncate text-sm font-bold text-white">{account?.name}</p>
              </div>
            </div>
            <div className="flex items-center gap-2">
              <Link href="/portal/appointments"
                className="flex items-center gap-1.5 rounded-xl bg-white/20 px-3 py-1.5 text-xs font-semibold text-white backdrop-blur-sm transition hover:bg-white/30">
                <CalendarDays size={13} /> {t("myAppointments")}
              </Link>
              <Link href="/portal/account" aria-label={t("accountTitle")} title={t("accountTitle")}
                className="flex h-8 w-8 items-center justify-center rounded-xl bg-white/20 text-white backdrop-blur-sm transition hover:bg-white/30">
                <Sparkles size={14} />
              </Link>
              <LangToggle onDark />
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

          {/* title + search left, AI assistant right (stacked on mobile) */}
          <div className="mt-8 grid items-center gap-8 lg:grid-cols-[1fr_400px] lg:gap-12">
            <div>
              <h1 className="text-3xl font-extrabold leading-tight tracking-tight text-white drop-shadow sm:text-4xl xl:text-5xl">
                {t("heroTitle1")}
                <br className="hidden sm:block" />
                <span className="text-white/85">{t("heroTitle2")}</span>
              </h1>
              <p className="mt-3 max-w-xl text-sm text-white/70 sm:text-base">
                {t("heroSub")}
              </p>
              {hospitals.length > 0 && specialties.length > 0 && (
                <div className="mt-4 flex flex-wrap gap-2 animate-fade-in">
                  <span className="inline-flex items-center gap-1.5 rounded-full bg-white/15 px-3 py-1 text-[11px] font-semibold text-white/90 backdrop-blur-sm">
                    <Stethoscope size={11} />
                    {t("statDoctors", { n: specialties.reduce((n, s) => n + s.doctor_count, 0) })}
                  </span>
                  <span className="inline-flex items-center gap-1.5 rounded-full bg-white/15 px-3 py-1 text-[11px] font-semibold text-white/90 backdrop-blur-sm">
                    <Building2 size={11} />
                    {t("statHospitals", { n: hospitals.length })}
                  </span>
                  <span className="inline-flex items-center gap-1.5 rounded-full bg-white/15 px-3 py-1 text-[11px] font-semibold text-white/90 backdrop-blur-sm">
                    <Sparkles size={11} />
                    {t("statSpecialties", { n: specialties.length })}
                  </span>
                </div>
              )}

              <div className="relative mt-6">
                <Search size={18} className="pointer-events-none absolute left-4 top-1/2 -translate-y-1/2 text-faint" />
                <input
                  value={q}
                  onChange={(e) => setQ(e.target.value)}
                  placeholder={t("searchPlaceholder")}
                  aria-label={t("searchPlaceholder")}
                  className="w-full rounded-2xl border border-white/20 bg-surface py-4 pl-12 pr-4 text-sm text-fg shadow-xl outline-none transition placeholder:text-faint focus:ring-2 focus:ring-white/50 sm:text-[15px]"
                />
              </div>
            </div>

            {/* Platform-wide AI assistant entry */}
            <div
              className="rounded-3xl p-[1.5px] shadow-[0_8px_40px_-10px_rgba(0,0,0,0.5)]"
              style={{ background: "linear-gradient(120deg, rgba(255,255,255,0.55), rgba(255,255,255,0.12) 60%)" }}
            >
              <div className="rounded-3xl bg-black/25 p-5 backdrop-blur-md">
                <div className="flex items-center gap-3">
                  <span className="relative flex h-12 w-12 shrink-0 items-center justify-center rounded-2xl bg-white/20">
                    <Sparkles size={21} className="text-white" />
                    <span className="absolute -right-0.5 -top-0.5 h-2.5 w-2.5 animate-pulse rounded-full bg-emerald-400 ring-2 ring-black/30" />
                  </span>
                  <div className="min-w-0">
                    <p className="text-base font-extrabold text-white">{t("aiTitle")}</p>
                    <p className="text-[11px] font-semibold uppercase tracking-wider text-emerald-300">{t("aiOnline")}</p>
                  </div>
                </div>
                <p className="mt-3 text-[13px] leading-relaxed text-white/75">
                  {t("aiPitch")}
                </p>
                <div className="mt-4 grid grid-cols-2 gap-2">
                  <button
                    onClick={() => router.push("/portal/book")}
                    className="flex items-center justify-center gap-1.5 rounded-xl bg-white px-4 py-3 text-xs font-bold text-indigo-700 shadow-lg transition hover:bg-white/90 active:scale-95"
                  >
                    <MessageCircle size={14} /> {t("chatNow")}
                  </button>
                  <button
                    onClick={() => setVoiceCall({ label: t("aiTitle") })}
                    className="flex items-center justify-center gap-1.5 rounded-xl bg-white/20 px-4 py-3 text-xs font-bold text-white backdrop-blur-sm transition hover:bg-white/30 active:scale-95"
                  >
                    <Phone size={14} /> {t("voiceCall")}
                  </button>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* ── Content ────────────────────────────────────────────────── */}
      <main className="relative mx-auto w-full max-w-6xl space-y-7 px-5 pb-16 pt-7 lg:px-8">

        {/* How it works — first-visit orientation */}
        <div className="grid gap-3 sm:grid-cols-3">
          {[
            { Icon: Search, title: t("how1Title"), text: t("how1Text") },
            { Icon: Scale, title: t("how2Title"), text: t("how2Text") },
            { Icon: CalendarCheck, title: t("how3Title"), text: t("how3Text") },
          ].map(({ Icon, title, text }, i) => (
            <div key={title}
              className="flex items-start gap-3 rounded-2xl border border-border bg-surface/60 px-4 py-3.5 animate-fade-in-up"
              style={{ animationDelay: `${i * 80}ms` }}>
              <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-[var(--brand-soft)] text-primary">
                <Icon size={16} />
              </span>
              <span>
                <p className="text-[13px] font-bold text-fg">{title}</p>
                <p className="mt-0.5 text-[11px] leading-snug text-faint">{text}</p>
              </span>
            </div>
          ))}
        </div>

        {/* Specialty tiles */}
        <section className="space-y-3">
          <div className="flex items-center gap-2">
            <div className="h-5 w-1 rounded-full" style={{ background: "var(--brand-grad)" }} />
            <h2 className="text-xs font-bold uppercase tracking-widest text-faint">{t("bySpecialty")}</h2>
          </div>
          <SpecialtyTiles specialties={specialties} active={specialty} onPick={setSpecialty} />
        </section>

        {/* Doctors */}
        <section className="space-y-4">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="flex items-center gap-2">
              <div className="h-5 w-1 rounded-full" style={{ background: "var(--brand-grad)" }} />
              <h2 className="text-xs font-bold uppercase tracking-widest text-faint">
                {t("doctorsHeading")}
                {!searching && results.length > 0 && (
                  <span className="ml-1.5 normal-case tracking-normal text-faint/80">
                    {t("doctorsCount", { n: `${results.length}${hasMore ? "+" : ""}` })}
                  </span>
                )}
              </h2>
            </div>
            <SearchFilters
              hospitals={hospitals}
              hospitalId={hospitalId}
              onHospital={setHospitalId}
              maxFee={maxFee}
              onMaxFee={setMaxFee}
              sort={sort}
              onSort={setSort}
            />
          </div>

          {error && (
            <div role="alert" className="rounded-2xl border border-danger/20 bg-danger/10 px-4 py-3 text-sm text-danger animate-fade-in">
              {error}
            </div>
          )}

          {searching ? (
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3" aria-busy="true">
              {[0, 1, 2, 3, 4, 5].map((i) => (
                <div key={i} className="skeleton h-[132px] rounded-2xl" style={{ animationDelay: `${i * 70}ms` }} />
              ))}
            </div>
          ) : results.length === 0 ? (
            <div className="rounded-2xl border border-border/50 bg-surface/50 p-14 text-center animate-fade-in">
              <p className="text-sm font-semibold text-muted">{t("noDoctorsTitle")}</p>
              <p className="mt-1 text-xs text-faint">{t("noDoctorsSub")}</p>
            </div>
          ) : (
            <>
              <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
                {results.map((doc, i) => (
                  <DoctorCard key={doc.id} doc={doc} index={i % PAGE_SIZE} />
                ))}
              </div>
              {hasMore && (
                <button
                  onClick={loadMore}
                  disabled={moreLoading}
                  className="mx-auto flex items-center gap-1.5 rounded-2xl border border-border bg-surface/80 px-6 py-2.5 text-sm font-semibold text-fg transition hover:border-indigo-500/40 disabled:opacity-40"
                >
                  {moreLoading ? t("loading") : t("loadMore")}
                </button>
              )}
            </>
          )}
        </section>

        {/* Secondary: browse by hospital (the original drill-down) */}
        <section className="pt-1">
          <button
            onClick={() => setBrowseOpen((o) => !o)}
            aria-expanded={browseOpen}
            className="flex w-full items-center justify-between rounded-2xl border border-border bg-surface/80 px-5 py-4 text-left transition hover:border-indigo-500/40"
          >
            <span className="flex items-center gap-2.5 text-sm font-bold text-fg">
              <Building2 size={16} className="text-primary" />
              {t("browseByHospital")}
            </span>
            <ChevronDown
              size={16}
              className={`text-faint transition-transform ${browseOpen ? "rotate-180" : ""}`}
            />
          </button>
          {browseOpen && (
            <div className="mt-4 animate-fade-in">
              <HospitalBrowse onVoiceCall={setVoiceCall} />
            </div>
          )}
        </section>
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
