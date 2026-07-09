"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { ShieldCheck, Eye, EyeOff, ArrowLeft, Terminal } from "lucide-react";
import { ApiError, clearToken, getMe, login } from "@/lib/api";
import ThemeToggle from "@/components/layout/ThemeToggle";

export default function PlatformAdminLoginPage() {
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
      const clinic = await getMe();
      if (clinic.role !== "platform_admin") {
        clearToken();
        setError("This account does not have platform admin access.");
        return;
      }
      router.push("/hospitals");
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

      {/* Background orbs — slightly more red/amber tint to signal elevated access */}
      <div aria-hidden className="pointer-events-none absolute inset-0 overflow-hidden">
        <div
          className="absolute rounded-full animate-orb"
          style={{
            width: "600px", height: "600px",
            top: "-200px", right: "-100px",
            background: "radial-gradient(circle, rgba(139,92,246,0.18) 0%, transparent 70%)",
            filter: "blur(40px)",
            animationDuration: "14s",
          }}
        />
        <div
          className="absolute rounded-full animate-orb"
          style={{
            width: "500px", height: "500px",
            bottom: "-150px", left: "-100px",
            background: "radial-gradient(circle, rgba(220,38,38,0.10) 0%, transparent 70%)",
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
            background: "radial-gradient(circle, rgba(99,102,241,0.10) 0%, transparent 70%)",
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

      {/* Top corners */}
      <div className="absolute left-5 top-5 z-10">
        <Link
          href="/login"
          className="flex items-center gap-1.5 rounded-xl border border-border bg-surface/80 px-3 py-1.5 text-xs font-medium text-muted backdrop-blur-sm transition hover:text-fg"
        >
          <ArrowLeft size={13} /> Back to staff login
        </Link>
      </div>
      <div className="absolute right-5 top-5 z-10">
        <ThemeToggle />
      </div>

      {/* Card */}
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
          {/* Header */}
          <div className="mb-7 flex flex-col items-center text-center">
            <div
              className="mb-4 flex h-14 w-14 items-center justify-center rounded-2xl shadow-xl animate-float"
              style={{
                background: "linear-gradient(135deg, #7c3aed, #4f46e5)",
                boxShadow: "0 8px 24px rgba(124,58,237,0.5)",
                animationDuration: "3.5s",
              }}
            >
              <ShieldCheck size={26} className="text-white" />
            </div>

            <div className="mb-2 inline-flex items-center gap-1.5 rounded-full border border-red-500/20 bg-red-500/10 px-3 py-1 text-[11px] font-bold uppercase tracking-widest text-red-400">
              <Terminal size={10} />
              Platform Admin
            </div>

            <h1 className="text-xl font-bold tracking-tight text-fg">
              Restricted Access
            </h1>
            <p className="mt-1 text-xs text-muted">
              Platform administrator accounts only
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
                placeholder="admin@platform.bd"
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
              style={{ background: "linear-gradient(135deg, #7c3aed, #4f46e5)" }}
            >
              {busy ? (
                <span className="flex items-center justify-center gap-2">
                  <span className="h-4 w-4 rounded-full border-2 border-white/30 border-t-white animate-spin-slow" />
                  Verifying…
                </span>
              ) : (
                "Sign in as Platform Admin"
              )}
            </button>
          </form>
        </div>

        <p className="mt-4 text-center text-xs text-faint animate-fade-in">
          This login is for platform administrators only.
          <br />
          Regular clinic staff should use{" "}
          <Link href="/login" className="text-primary hover:underline">
            the staff login
          </Link>
          .
        </p>
      </div>
    </div>
  );
}
