"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { HeartPulse, Mail, Lock, ArrowRight, ArrowLeft } from "lucide-react";
import { ApiError, patientLogin } from "@/lib/api";

export default function PatientLoginPage() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const router = useRouter();

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      await patientLogin(email, password);
      router.push("/portal");
    } catch (err) {
      setError(
        err instanceof ApiError && err.status === 401
          ? "Invalid email or password."
          : err instanceof Error
          ? err.message
          : "Could not sign in."
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex min-h-screen w-full flex-col items-center justify-center bg-bg px-4 py-10">
      {/* Back to home */}
      <div className="absolute left-5 top-5">
        <Link href="/"
          className="flex items-center gap-1.5 rounded-xl border border-border bg-surface px-3 py-1.5 text-xs font-medium text-muted transition hover:text-fg">
          <ArrowLeft size={13} /> Home
        </Link>
      </div>

      {/* Brand strip */}
      <div
        className="mb-8 flex flex-col items-center gap-3 text-center"
      >
        <span
          className="flex h-16 w-16 items-center justify-center rounded-2xl text-white shadow-xl"
          style={{ background: "var(--brand-grad)" }}
        >
          <HeartPulse size={30} />
        </span>
        <div>
          <p className="text-sm font-black uppercase tracking-[0.2em] text-primary">
            ASA <span className="font-semibold normal-case tracking-normal text-muted">· Appointment Setter Agent</span>
          </p>
          <h1 className="mt-1 text-2xl font-bold text-fg">Welcome back</h1>
          <p className="mt-0.5 text-sm text-muted">Sign in to book and manage appointments</p>
        </div>
      </div>

      <form
        onSubmit={submit}
        className="w-full max-w-sm space-y-4 rounded-2xl border border-border bg-surface p-7 shadow-lg"
      >
        <div className="space-y-1.5">
          <label htmlFor="login-email" className="text-xs font-semibold uppercase tracking-wider text-faint">Email</label>
          <div className="relative">
            <Mail size={15} className="absolute left-3.5 top-1/2 -translate-y-1/2 text-muted" />
            <input
              id="login-email"
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              required
              autoComplete="email"
              className="input pl-9"
              placeholder="you@example.com"
            />
          </div>
        </div>

        <div className="space-y-1.5">
          <label htmlFor="login-password" className="text-xs font-semibold uppercase tracking-wider text-faint">Password</label>
          <div className="relative">
            <Lock size={15} className="absolute left-3.5 top-1/2 -translate-y-1/2 text-muted" />
            <input
              id="login-password"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
              autoComplete="current-password"
              className="input pl-9"
              placeholder="••••••••"
            />
          </div>
        </div>

        {error && (
          <div role="alert" className="rounded-xl bg-danger/10 px-3 py-2.5 text-sm text-danger">
            {error}
          </div>
        )}

        <button
          type="submit"
          disabled={busy}
          className="btn-primary btn-lg flex w-full items-center justify-center gap-2"
        >
          {busy ? (
            <span className="h-4 w-4 animate-spin rounded-full border-2 border-white border-t-transparent" />
          ) : (
            <>Sign in <ArrowRight size={15} /></>
          )}
        </button>

        <div className="space-y-1.5 text-center text-sm text-muted">
          <p>
            <Link href="/portal/forgot" className="font-medium text-primary hover:underline">
              Forgot password?
            </Link>
          </p>
          <p>
            New here?{" "}
            <Link href="/portal/signup" className="font-semibold text-primary hover:underline">
              Create an account
            </Link>
          </p>
        </div>
      </form>
    </div>
  );
}
