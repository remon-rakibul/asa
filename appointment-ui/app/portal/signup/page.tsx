"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { HeartPulse, User, Phone, Mail, Lock, ArrowRight, ArrowLeft } from "lucide-react";
import { patientSignup } from "@/lib/api";

type FieldKey = "name" | "phone" | "email" | "password";

const PW_RULES = [
  { label: "8+ characters", test: (v: string) => v.length >= 8 },
  { label: "lowercase", test: (v: string) => /[a-z]/.test(v) },
  { label: "uppercase", test: (v: string) => /[A-Z]/.test(v) },
  { label: "a digit", test: (v: string) => /\d/.test(v) },
];

function fieldError(key: FieldKey, value: string): string {
  const v = value.trim();
  if (!v) return "Required";
  if (key === "email" && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(v)) return "Enter a valid email";
  if (key === "phone" && !/^01\d{9}$/.test(v)) return "Use an 11-digit number starting 01";
  if (key === "password" && !PW_RULES.every((r) => r.test(v))) return "Password too weak";
  return "";
}

export default function PatientSignupPage() {
  const [form, setForm] = useState({ name: "", phone: "", email: "", password: "" });
  const [touched, setTouched] = useState<Record<FieldKey, boolean>>({
    name: false, phone: false, email: false, password: false,
  });
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const router = useRouter();

  function set(k: keyof typeof form) {
    return (e: React.ChangeEvent<HTMLInputElement>) =>
      setForm((f) => ({ ...f, [k]: e.target.value }));
  }

  const pwScore = PW_RULES.filter((r) => r.test(form.password)).length;
  const allValid = (["name", "phone", "email", "password"] as FieldKey[])
    .every((k) => !fieldError(k, form[k]));

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!allValid) {
      setTouched({ name: true, phone: true, email: true, password: true });
      return;
    }
    setBusy(true);
    setError("");
    try {
      await patientSignup(form);
      router.push("/portal");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not create account.");
    } finally {
      setBusy(false);
    }
  }

  const fields = [
    { key: "name" as const, label: "Full name", type: "text", placeholder: "Rokaiya Begum", icon: User, autoComplete: "name" },
    { key: "phone" as const, label: "Mobile number", type: "tel", placeholder: "01XXXXXXXXX", icon: Phone, autoComplete: "tel" },
    { key: "email" as const, label: "Email", type: "email", placeholder: "you@example.com", icon: Mail, autoComplete: "email" },
    { key: "password" as const, label: "Password", type: "password", placeholder: "At least 8 chars, mixed case + digit", icon: Lock, autoComplete: "new-password" },
  ];

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
      <div className="mb-8 flex flex-col items-center gap-3 text-center">
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
          <h1 className="mt-1 text-2xl font-bold text-fg">Create your account</h1>
          <p className="mt-0.5 text-sm text-muted">One account for every hospital on the platform</p>
        </div>
      </div>

      <form
        onSubmit={submit}
        className="w-full max-w-sm space-y-4 rounded-2xl border border-border bg-surface p-7 shadow-lg"
      >
        {fields.map(({ key, label, type, placeholder, icon: Icon, autoComplete }) => {
          const err = fieldError(key, form[key]);
          const showErr = touched[key] && !!err;
          const showOk = touched[key] && !err && !!form[key];
          return (
            <div key={key} className="space-y-1.5">
              <label htmlFor={`signup-${key}`} className="text-xs font-semibold uppercase tracking-wider text-faint">{label}</label>
              <div className="relative">
                <Icon size={15} className="absolute left-3.5 top-1/2 -translate-y-1/2 text-muted" />
                <input
                  id={`signup-${key}`}
                  type={type}
                  value={form[key]}
                  onChange={set(key)}
                  onBlur={() => setTouched((t) => ({ ...t, [key]: true }))}
                  required
                  autoComplete={autoComplete}
                  className={`input pl-9 ${showErr ? "input-error" : showOk ? "input-success" : ""}`}
                  placeholder={placeholder}
                />
              </div>
              {key === "password" && form.password && (
                <div className="space-y-1 pt-0.5">
                  <div className="flex gap-1">
                    {[0, 1, 2, 3].map((i) => (
                      <span
                        key={i}
                        className="h-1 flex-1 rounded-full transition-colors"
                        style={{
                          background: i < pwScore
                            ? (pwScore <= 2 ? "var(--danger)" : pwScore === 3 ? "var(--warning)" : "var(--success)")
                            : "var(--surface-3)",
                        }}
                      />
                    ))}
                  </div>
                  <p className="text-[11px] text-faint">
                    Needs: {PW_RULES.filter((r) => !r.test(form.password)).map((r) => r.label).join(", ") || "looking good!"}
                  </p>
                </div>
              )}
              {showErr && key !== "password" && <p className="text-[11px] text-danger">{err}</p>}
            </div>
          );
        })}

        {error && (
          <div role="alert" className="rounded-xl bg-danger/10 px-3 py-2.5 text-sm text-danger">
            {error}
          </div>
        )}

        <button
          type="submit"
          disabled={busy || !allValid}
          className="btn-primary btn-lg flex w-full items-center justify-center gap-2"
        >
          {busy ? (
            <span className="h-4 w-4 animate-spin rounded-full border-2 border-white border-t-transparent" />
          ) : (
            <>Create account <ArrowRight size={15} /></>
          )}
        </button>

        <p className="text-center text-sm text-muted">
          Already have an account?{" "}
          <Link href="/portal/login" className="font-semibold text-primary hover:underline">
            Sign in
          </Link>
        </p>
      </form>
    </div>
  );
}
