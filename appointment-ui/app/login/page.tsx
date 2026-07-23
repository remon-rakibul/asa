"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { Stethoscope, Eye, EyeOff, Sparkles, HeartPulse, ChevronRight, ShieldCheck, Building2 } from "lucide-react";
import { ApiError, login } from "@/lib/api";
import ThemeToggle from "@/components/layout/ThemeToggle";

export default function LoginPage() {
  const [email, setEmail]       = useState("");
  const [password, setPassword] = useState("");
  const [showPw, setShowPw]     = useState(false);
  const [error, setError]       = useState("");
  const [busy, setBusy]         = useState(false);
  const router = useRouter();

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      await login(email, password);
      router.push("/");
    } catch (err) {
      setError(
        err instanceof ApiError && err.status === 401
          ? "Invalid email or password."
          : "Could not sign in. Is the server running?"
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="relative flex h-screen w-full items-center justify-center overflow-hidden bg-bg px-4">
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
        <div
          className="absolute rounded-full animate-orb"
          style={{
            width: "300px", height: "300px",
            top: "40%", left: "40%",
            background: "radial-gradient(circle, rgba(59,130,246,0.1) 0%, transparent 70%)",
            filter: "blur(30px)",
            animationDuration: "22s",
            animationDelay: "-3s",
          }}
        />
        <div
          className="absolute inset-0 opacity-[0.025]"
          style={{
            backgroundImage: `
              linear-gradient(var(--fg) 1px, transparent 1px),
              linear-gradient(90deg, var(--fg) 1px, transparent 1px)
            `,
            backgroundSize: "40px 40px",
          }}
        />
      </div>

      <div className="absolute right-5 top-5 z-10">
        <ThemeToggle />
      </div>

      <div
        className="relative z-10 w-full max-w-sm animate-scale-in"
        style={{ animationDuration: "0.45s" }}
      >
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
          <div className="mb-8 flex flex-col items-center text-center">
            <div
              className="mb-4 flex h-14 w-14 items-center justify-center rounded-2xl shadow-xl animate-float"
              style={{
                background: "var(--brand-grad)",
                boxShadow: "0 8px 24px rgba(99,102,241,0.5)",
                animationDuration: "3.5s",
              }}
            >
              <Stethoscope size={26} className="text-white" />
            </div>

            <h1 className="text-2xl font-bold tracking-tight text-fg">
              Clinic Console
            </h1>
            <p className="mt-1.5 flex items-center gap-1.5 text-sm text-muted">
              <Sparkles size={12} className="text-primary" />
              Powered by AI receptionist
            </p>
          </div>

          <form onSubmit={submit} className="space-y-4">
            <div className="space-y-1.5">
              <label className="text-xs font-semibold uppercase tracking-wider text-faint">
                Email
              </label>
              <input
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="doctor@clinic.com"
                required
                className="input"
                autoComplete="email"
              />
            </div>

            <div className="space-y-1.5">
              <label className="text-xs font-semibold uppercase tracking-wider text-faint">
                Password
              </label>
              <div className="relative">
                <input
                  type={showPw ? "text" : "password"}
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  placeholder="••••••••"
                  required
                  className="input pr-10"
                  autoComplete="current-password"
                />
                <button
                  type="button"
                  onClick={() => setShowPw((v) => !v)}
                  className="absolute right-3 top-1/2 -translate-y-1/2 text-faint transition-colors hover:text-muted"
                  tabIndex={-1}
                >
                  {showPw ? <EyeOff size={15} /> : <Eye size={15} />}
                </button>
              </div>
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

            <button
              type="submit"
              disabled={busy}
              className="btn-primary btn-lg w-full mt-2"
            >
              {busy ? (
                <span className="flex items-center gap-2">
                  <span className="h-4 w-4 rounded-full border-2 border-white/30 border-t-white animate-spin-slow" />
                  Signing in…
                </span>
              ) : (
                "Sign in"
              )}
            </button>
          </form>
        </div>

        <p className="mt-5 text-center text-xs text-faint animate-fade-in delay-300">
          <span className="badge badge-surface">Bangla AI receptionist · Appointment management</span>
        </p>

        <Link
          href="/signup"
          className="mt-4 group flex w-full items-center justify-between gap-3 overflow-hidden rounded-2xl border border-white/5 bg-surface/80 px-5 py-4 backdrop-blur-sm transition-all hover:border-indigo-500/30 hover:shadow-[0_0_30px_-8px_rgba(99,102,241,0.4)] active:scale-[0.98] animate-fade-in delay-300"
        >
          <div className="flex items-center gap-3">
            <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-indigo-500/15 text-indigo-400 transition group-hover:bg-indigo-500/25">
              <Building2 size={16} />
            </span>
            <div>
              <div className="text-sm font-semibold text-fg">Own a hospital or clinic?</div>
              <div className="text-xs text-muted">List it — first month free</div>
            </div>
          </div>
          <ChevronRight size={16} className="shrink-0 text-faint transition group-hover:translate-x-0.5 group-hover:text-indigo-400" />
        </Link>

        <Link
          href="/portal"
          className="mt-4 group flex w-full items-center justify-between gap-3 overflow-hidden rounded-2xl border border-white/5 bg-surface/80 px-5 py-4 backdrop-blur-sm transition-all hover:border-indigo-500/30 hover:shadow-[0_0_30px_-8px_rgba(99,102,241,0.4)] active:scale-[0.98] animate-fade-in delay-300"
        >
          <div className="flex items-center gap-3">
            <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-indigo-500/15 text-indigo-400 transition group-hover:bg-indigo-500/25">
              <HeartPulse size={16} />
            </span>
            <div>
              <div className="text-sm font-semibold text-fg">Booking an appointment?</div>
              <div className="text-xs text-muted">Go to the patient portal</div>
            </div>
          </div>
          <ChevronRight size={16} className="shrink-0 text-faint transition group-hover:translate-x-0.5 group-hover:text-indigo-400" />
        </Link>

        <div className="mt-5 text-center animate-fade-in delay-300">
          <Link
            href="/platform-admin"
            className="inline-flex items-center gap-1.5 text-xs text-faint/60 transition hover:text-faint"
          >
            <ShieldCheck size={11} />
            Platform admin login
          </Link>
        </div>
      </div>
    </div>
  );
}
