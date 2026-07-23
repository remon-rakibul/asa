"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { Building2, Eye, EyeOff, Sparkles, ChevronRight, ArrowLeft, Gift } from "lucide-react";
import { ApiError, hospitalSignup } from "@/lib/api";
import ThemeToggle from "@/components/layout/ThemeToggle";

function slugify(name: string): string {
  return name
    .toLowerCase()
    .trim()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
}

export default function HospitalSignupPage() {
  const [hospitalName, setHospitalName] = useState("");
  const [slug, setSlug] = useState("");
  const [slugTouched, setSlugTouched] = useState(false);
  const [doctorName, setDoctorName] = useState("");
  const [doctorPhone, setDoctorPhone] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showPw, setShowPw] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const router = useRouter();

  function onHospitalNameChange(v: string) {
    setHospitalName(v);
    if (!slugTouched) setSlug(slugify(v));
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      await hospitalSignup({
        slug,
        name: hospitalName,
        doctor_name: doctorName || undefined,
        doctor_phone: doctorPhone || undefined,
        admin_email: email,
        admin_password: password,
      });
      router.push("/");
    } catch (err) {
      setError(
        err instanceof ApiError
          ? err.message
          : "Could not create your account. Is the server running?"
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="relative flex min-h-screen w-full items-center justify-center overflow-hidden bg-bg px-4 py-10">
      <div aria-hidden className="pointer-events-none absolute inset-0 overflow-hidden">
        <div
          className="absolute rounded-full animate-orb"
          style={{
            width: "600px", height: "600px",
            top: "-200px", right: "-100px",
            background: "radial-gradient(circle, rgba(99,102,241,0.18) 0%, transparent 70%)",
            filter: "blur(40px)",
            animationDuration: "14s",
          }}
        />
        <div
          className="absolute rounded-full animate-orb"
          style={{
            width: "500px", height: "500px",
            bottom: "-150px", left: "-100px",
            background: "radial-gradient(circle, rgba(139,92,246,0.15) 0%, transparent 70%)",
            filter: "blur(40px)",
            animationDuration: "18s",
            animationDelay: "-6s",
          }}
        />
      </div>

      <div className="absolute right-5 top-5 z-10">
        <ThemeToggle />
      </div>
      <div className="absolute left-5 top-5 z-10">
        <Link href="/login" className="btn-ghost btn-sm flex items-center gap-1.5">
          <ArrowLeft size={14} /> Sign in
        </Link>
      </div>

      <div className="relative z-10 w-full max-w-md animate-scale-in" style={{ animationDuration: "0.45s" }}>
        <div
          className="rounded-2xl p-8"
          style={{
            background: "color-mix(in srgb, var(--surface) 88%, transparent)",
            backdropFilter: "blur(24px)",
            WebkitBackdropFilter: "blur(24px)",
            border: "1px solid color-mix(in srgb, var(--border) 80%, transparent)",
            boxShadow: "var(--shadow-pop)",
          }}
        >
          <div className="mb-7 flex flex-col items-center text-center">
            <div
              className="mb-4 flex h-14 w-14 items-center justify-center rounded-2xl shadow-xl animate-float"
              style={{
                background: "var(--brand-grad)",
                boxShadow: "0 8px 24px rgba(99,102,241,0.5)",
                animationDuration: "3.5s",
              }}
            >
              <Building2 size={26} className="text-white" />
            </div>
            <h1 className="text-2xl font-bold tracking-tight text-fg">List Your Hospital</h1>
            <p className="mt-1.5 flex items-center gap-1.5 text-sm text-muted">
              <Sparkles size={12} className="text-primary" />
              AI receptionist · Patient marketplace · Free front-desk console
            </p>
            <span className="mt-3 inline-flex items-center gap-1.5 rounded-full px-3 py-1.5 text-xs font-bold"
              style={{ background: "color-mix(in srgb, var(--success) 14%, transparent)", color: "var(--success)" }}>
              <Gift size={13} /> First month free — no card required
            </span>
          </div>

          <form onSubmit={submit} className="space-y-4">
            <div className="space-y-1.5">
              <label className="text-xs font-semibold uppercase tracking-wider text-faint">
                Hospital / clinic name
              </label>
              <input
                type="text" value={hospitalName}
                onChange={(e) => onHospitalNameChange(e.target.value)}
                placeholder="City General Hospital"
                required className="input"
              />
            </div>

            <div className="space-y-1.5">
              <label className="text-xs font-semibold uppercase tracking-wider text-faint">
                URL slug
              </label>
              <input
                type="text" value={slug}
                onChange={(e) => { setSlug(slugify(e.target.value)); setSlugTouched(true); }}
                placeholder="city-general-hospital"
                pattern="[a-z0-9-]+"
                required className="input"
              />
            </div>

            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-1.5">
                <label className="text-xs font-semibold uppercase tracking-wider text-faint">
                  Lead doctor (optional)
                </label>
                <input
                  type="text" value={doctorName}
                  onChange={(e) => setDoctorName(e.target.value)}
                  placeholder="Dr. Rahim"
                  className="input"
                />
              </div>
              <div className="space-y-1.5">
                <label className="text-xs font-semibold uppercase tracking-wider text-faint">
                  Doctor phone (optional)
                </label>
                <input
                  type="tel" value={doctorPhone}
                  onChange={(e) => setDoctorPhone(e.target.value)}
                  placeholder="01XXXXXXXXX"
                  className="input"
                />
              </div>
            </div>

            <div className="space-y-1.5">
              <label className="text-xs font-semibold uppercase tracking-wider text-faint">
                Admin email
              </label>
              <input
                type="email" value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="admin@yourhospital.bd"
                required className="input" autoComplete="email"
              />
            </div>

            <div className="space-y-1.5">
              <label className="text-xs font-semibold uppercase tracking-wider text-faint">
                Admin password
              </label>
              <div className="relative">
                <input
                  type={showPw ? "text" : "password"}
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  placeholder="••••••••"
                  required minLength={8} className="input pr-10"
                  autoComplete="new-password"
                />
                <button
                  type="button" onClick={() => setShowPw((v) => !v)}
                  className="absolute right-3 top-1/2 -translate-y-1/2 text-faint transition-colors hover:text-muted"
                  tabIndex={-1}
                >
                  {showPw ? <EyeOff size={15} /> : <Eye size={15} />}
                </button>
              </div>
              <p className="text-[11px] text-faint">
                At least 8 characters, with an uppercase letter, a lowercase letter, and a digit.
              </p>
            </div>

            {error && (
              <div
                className="rounded-xl px-4 py-3 text-sm font-medium animate-fade-in"
                style={{
                  background: "color-mix(in srgb, var(--danger) 12%, transparent)",
                  border: "1px solid color-mix(in srgb, var(--danger) 30%, transparent)",
                  color: "var(--danger)",
                }}
              >
                {error}
              </div>
            )}

            <button type="submit" disabled={busy} className="btn-primary btn-lg w-full mt-2">
              {busy ? (
                <span className="flex items-center gap-2">
                  <span className="h-4 w-4 rounded-full border-2 border-white/30 border-t-white animate-spin-slow" />
                  Creating your account…
                </span>
              ) : (
                <span className="flex items-center gap-2">
                  Start free trial <ChevronRight size={16} />
                </span>
              )}
            </button>
          </form>
        </div>

        <p className="mt-5 text-center text-xs text-faint animate-fade-in delay-300">
          Already listed?{" "}
          <Link href="/login" className="font-semibold text-primary hover:underline">
            Sign in
          </Link>
        </p>
      </div>
    </div>
  );
}
