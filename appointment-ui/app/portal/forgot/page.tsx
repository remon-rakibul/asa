"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { KeyRound, User, Lock, Hash, ArrowRight, ArrowLeft } from "lucide-react";
import { portalForgotPassword, portalResetPassword } from "@/lib/api";

export default function ForgotPasswordPage() {
  const router = useRouter();
  const [step, setStep] = useState<"request" | "reset">("request");
  const [identifier, setIdentifier] = useState("");
  const [code, setCode] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [info, setInfo] = useState("");

  async function requestCode(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true); setError("");
    try {
      await portalForgotPassword(identifier.trim());
      setInfo("If an account exists for that email or phone, a reset code has been sent by SMS.");
      setStep("reset");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not request a code.");
    } finally {
      setBusy(false);
    }
  }

  async function resetPassword(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true); setError("");
    try {
      await portalResetPassword(identifier.trim(), code.trim(), newPassword);
      router.push("/portal");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not reset password.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex min-h-screen w-full flex-col items-center justify-center bg-bg px-4 py-10">
      <div className="absolute left-5 top-5">
        <Link href="/portal/login"
          className="flex items-center gap-1.5 rounded-xl border border-border bg-surface px-3 py-1.5 text-xs font-medium text-muted transition hover:text-fg">
          <ArrowLeft size={13} /> Back to sign in
        </Link>
      </div>

      <div className="mb-8 flex flex-col items-center gap-3 text-center">
        <span className="flex h-16 w-16 items-center justify-center rounded-2xl text-white shadow-xl"
          style={{ background: "var(--brand-grad)" }}>
          <KeyRound size={28} />
        </span>
        <div>
          <h1 className="text-2xl font-bold text-fg">Reset password</h1>
          <p className="mt-0.5 text-sm text-muted">
            {step === "request"
              ? "We'll text a reset code to your registered phone"
              : "Enter the code from your SMS and a new password"}
          </p>
        </div>
      </div>

      {step === "request" ? (
        <form onSubmit={requestCode} className="w-full max-w-sm space-y-4 rounded-2xl border border-border bg-surface p-7 shadow-lg">
          <div className="space-y-1.5">
            <label htmlFor="identifier" className="text-xs font-semibold uppercase tracking-wider text-faint">Email or phone</label>
            <div className="relative">
              <User size={15} className="absolute left-3.5 top-1/2 -translate-y-1/2 text-muted" />
              <input id="identifier" type="text" value={identifier} onChange={(e) => setIdentifier(e.target.value)}
                required autoComplete="username" className="input pl-9" placeholder="you@example.com or 01XXXXXXXXX" />
            </div>
          </div>
          {error && <div role="alert" className="rounded-xl bg-danger/10 px-3 py-2.5 text-sm text-danger">{error}</div>}
          <button type="submit" disabled={busy} className="btn-primary btn-lg flex w-full items-center justify-center gap-2">
            {busy ? <span className="h-4 w-4 animate-spin rounded-full border-2 border-white border-t-transparent" /> : <>Send code <ArrowRight size={15} /></>}
          </button>
        </form>
      ) : (
        <form onSubmit={resetPassword} className="w-full max-w-sm space-y-4 rounded-2xl border border-border bg-surface p-7 shadow-lg">
          {info && <p className="rounded-xl bg-info/10 px-3 py-2.5 text-xs text-info">{info}</p>}
          <div className="space-y-1.5">
            <label htmlFor="code" className="text-xs font-semibold uppercase tracking-wider text-faint">Reset code</label>
            <div className="relative">
              <Hash size={15} className="absolute left-3.5 top-1/2 -translate-y-1/2 text-muted" />
              <input id="code" inputMode="numeric" value={code} onChange={(e) => setCode(e.target.value)}
                required className="input pl-9 tracking-widest" placeholder="6-digit code" />
            </div>
          </div>
          <div className="space-y-1.5">
            <label htmlFor="newpw" className="text-xs font-semibold uppercase tracking-wider text-faint">New password</label>
            <div className="relative">
              <Lock size={15} className="absolute left-3.5 top-1/2 -translate-y-1/2 text-muted" />
              <input id="newpw" type="password" value={newPassword} onChange={(e) => setNewPassword(e.target.value)}
                required autoComplete="new-password" className="input pl-9" placeholder="At least 8 chars, mixed case + digit" />
            </div>
          </div>
          {error && <div role="alert" className="rounded-xl bg-danger/10 px-3 py-2.5 text-sm text-danger">{error}</div>}
          <button type="submit" disabled={busy} className="btn-primary btn-lg flex w-full items-center justify-center gap-2">
            {busy ? <span className="h-4 w-4 animate-spin rounded-full border-2 border-white border-t-transparent" /> : <>Reset password <ArrowRight size={15} /></>}
          </button>
          <button type="button" onClick={() => { setStep("request"); setError(""); }}
            className="w-full text-center text-xs text-muted hover:text-fg">
            Didn&apos;t get a code? Try again
          </button>
        </form>
      )}
    </div>
  );
}
