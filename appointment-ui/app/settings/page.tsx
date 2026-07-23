"use client";

import { useEffect, useState } from "react";
import {
  Building2,
  Users,
  MessageSquareText,
  CheckCircle2,
  AlertCircle,
  Save,
  Hash,
  Globe,
  Send,
  Star as StarIcon,
} from "lucide-react";
import TopBar from "@/components/layout/TopBar";
import { useAuth } from "@/lib/auth";
import { updateClinic, getSmsTemplateDefaults, type ClinicUpdate, type SmsTemplates } from "@/lib/api";
import DoctorsManager from "@/components/settings/DoctorsManager";
import ReviewsManager from "@/components/settings/ReviewsManager";
import { useUnsavedChanges } from "@/lib/useUnsavedChanges";

type FormState = {
  name: string;
  availability_days_ahead: number;
  timezone: string;
  greeting_instructions: string;
  sms_sender_id: string;
  sms_templates: Required<SmsTemplates>;
};

type Tab = "clinic" | "doctors" | "reviews" | "greetings" | "sms";

const TABS: { id: Tab; label: string; icon: React.ElementType; desc: string }[] = [
  { id: "clinic",    label: "Clinic",    icon: Building2,        desc: "Name, timezone & window" },
  { id: "doctors",   label: "Doctors",   icon: Users,            desc: "Manage care providers"  },
  { id: "reviews",   label: "Reviews",   icon: StarIcon,         desc: "Moderate patient reviews" },
  { id: "greetings", label: "Greetings", icon: MessageSquareText, desc: "AI greeting instructions" },
  { id: "sms",       label: "SMS",       icon: Send,             desc: "Sender & templates"     },
];

// Editable SMS templates surfaced in the UI, with their available placeholders.
const SMS_TEMPLATES: { key: keyof Required<SmsTemplates>; label: string; hint: string; placeholders: string[] }[] = [
  { key: "confirmation", label: "Booking confirmation", hint: "Sent to the patient after a successful booking.", placeholders: ["patient_name", "clinic", "doctor", "slot", "serial"] },
  { key: "reminder",     label: "24h reminder",         hint: "Sent ~24 hours before the appointment.",          placeholders: ["patient_name", "clinic", "doctor", "slot"] },
  { key: "doctor_alert", label: "Doctor new-booking alert", hint: "Sent to the doctor when a patient books.",     placeholders: ["clinic", "patient_name", "age", "mobile", "slot"] },
  { key: "token",        label: "Queue token call",     hint: "Sent when the patient's queue token is called.",   placeholders: ["patient_name", "token", "clinic"] },
];

export default function SettingsPage() {
  const { clinic, setClinic } = useAuth();
  const [form, setForm] = useState<FormState | null>(null);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("clinic");

  useUnsavedChanges(dirty);
  // Built-in default templates, so the SMS boxes are pre-filled and editable.
  const [defaults, setDefaults] = useState<Required<SmsTemplates> | null>(null);

  useEffect(() => {
    getSmsTemplateDefaults()
      .then((d) =>
        setDefaults({
          confirmation: d.defaults.confirmation ?? "",
          reminder: d.defaults.reminder ?? "",
          doctor_alert: d.defaults.doctor_alert ?? "",
          token: d.defaults.token ?? "",
        })
      )
      .catch(() =>
        setDefaults({ confirmation: "", reminder: "", doctor_alert: "", token: "" })
      );
  }, []);

  useEffect(() => {
    if (clinic && defaults && form === null) {
      // Pre-fill each template box with the saved value, falling back to the
      // built-in default so staff can see and edit the actual wording.
      const tpl = (k: keyof Required<SmsTemplates>) =>
        clinic.sms_templates?.[k] || defaults[k];
      setForm({
        name: clinic.name,
        availability_days_ahead: clinic.availability_days_ahead,
        timezone: clinic.timezone ?? "Asia/Dhaka",
        greeting_instructions: clinic.greeting_instructions ?? "",
        sms_sender_id: clinic.sms_sender_id ?? "",
        sms_templates: {
          confirmation: tpl("confirmation"),
          reminder: tpl("reminder"),
          doctor_alert: tpl("doctor_alert"),
          token: tpl("token"),
        },
      });
    }
  }, [clinic, defaults, form]);

  function set<K extends keyof FormState>(key: K, value: FormState[K]) {
    setForm((f) => (f ? { ...f, [key]: value } : f));
    setSaved(false);
    setDirty(true);
  }

  function setTemplate(key: keyof Required<SmsTemplates>, value: string) {
    setForm((f) => (f ? { ...f, sms_templates: { ...f.sms_templates, [key]: value } } : f));
    setSaved(false);
    setDirty(true);
  }

  async function save() {
    if (!form) return;
    setSaving(true);
    setError(null);
    try {
      const patch: ClinicUpdate = {
        name: form.name,
        availability_days_ahead: form.availability_days_ahead,
        timezone: form.timezone,
        greeting_instructions: form.greeting_instructions,
        sms_sender_id: form.sms_sender_id,
        sms_templates: form.sms_templates,
      };
      const updated = await updateClinic(patch);
      setClinic(updated);
      setSaved(true);
      setDirty(false);
      setTimeout(() => setSaved(false), 3000);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to save");
    } finally {
      setSaving(false);
    }
  }

  return (
    <>
      <TopBar title="Settings" />
      <div className="flex flex-1 flex-col overflow-hidden bg-bg lg:flex-row">
        <aside
          role="tablist"
          aria-orientation="vertical"
          className="flex w-full shrink-0 gap-1 overflow-x-auto border-b border-border p-3 animate-slide-left lg:w-56 lg:flex-col lg:border-b-0 lg:border-r lg:pt-5"
          style={{ background: "var(--surface)" }}
        >
          <p className="mb-2 hidden px-3 text-xs font-semibold uppercase tracking-widest text-faint lg:block">
            Sections
          </p>
          {TABS.map(({ id, label, icon: Icon, desc }) => {
            const active = tab === id;
            return (
              <button
                key={id}
                role="tab"
                aria-selected={active}
                onClick={() => setTab(id)}
                className="group relative flex w-auto shrink-0 items-center gap-3 rounded-xl px-3 py-2.5 text-left transition-all duration-200 lg:w-full"
              >
                {active && (
                  <span
                    className="absolute inset-0 rounded-xl"
                    style={{ background: "var(--brand-soft)" }}
                  />
                )}
                {active && (
                  <span
                    className="absolute left-0 top-1/2 h-5 w-0.5 -translate-y-1/2 rounded-r"
                    style={{ background: "var(--brand-grad)" }}
                  />
                )}
                <span
                  className="relative z-10 flex h-8 w-8 shrink-0 items-center justify-center rounded-lg transition-all duration-200"
                  style={
                    active
                      ? { background: "var(--brand-soft)", color: "var(--primary)" }
                      : {}
                  }
                >
                  <Icon size={16} className={active ? "text-primary" : "text-faint group-hover:text-muted"} />
                </span>
                <div className="relative z-10 min-w-0">
                  <div className={`text-sm font-medium ${active ? "text-primary" : "text-muted group-hover:text-fg"}`}>
                    {label}
                  </div>
                  <div className="hidden text-xs text-faint truncate lg:block">{desc}</div>
                </div>
              </button>
            );
          })}
        </aside>

        <main className="flex-1 overflow-y-auto p-4 lg:p-8">
          {form === null ? (
            <SettingsSkeleton />
          ) : (
            <div className="mx-auto max-w-3xl space-y-6 animate-fade-in-up">
              {tab === "clinic" && (
                <>
                  <SectionHeader
                    icon={Building2}
                    title="Clinic information"
                    description="Configure your clinic name and appointment booking window."
                  />
                  <div className="card overflow-hidden">
                    <div className="divide-y divide-border">
                      <SettingRow
                        icon={Building2}
                        label="Clinic name"
                        hint={
                          form.name
                            ? `The AI will say "${form.name}" in every greeting.`
                            : `Replace "মাই ক্লিনিক" — type your clinic's real name here.`
                        }
                      >
                        <input
                          className="input"
                          dir="auto"
                          placeholder="মাই ক্লিনিক — type your clinic name to change"
                          value={form.name}
                          onChange={(e) => set("name", e.target.value)}
                        />
                      </SettingRow>
                      <SettingRow
                        icon={Hash}
                        label="Booking window"
                        hint="Patients can book this many days in advance (1 – 60)."
                      >
                        <div className="flex items-center gap-3">
                          <input
                            type="number"
                            min={1}
                            max={60}
                            className="input w-28"
                            value={form.availability_days_ahead}
                            onChange={(e) => set("availability_days_ahead", Number(e.target.value))}
                          />
                          <span className="text-sm text-muted">days ahead</span>
                        </div>
                      </SettingRow>
                      <SettingRow
                        icon={Globe}
                        label="Timezone"
                        hint="Used for slot times and reminders (IANA name, e.g. Asia/Dhaka)."
                      >
                        <input
                          className="input w-64"
                          list="tz-options"
                          placeholder="Asia/Dhaka"
                          value={form.timezone}
                          onChange={(e) => set("timezone", e.target.value)}
                        />
                        <datalist id="tz-options">
                          <option value="Asia/Dhaka" />
                          <option value="Asia/Kolkata" />
                          <option value="Asia/Karachi" />
                          <option value="Asia/Dubai" />
                          <option value="UTC" />
                        </datalist>
                      </SettingRow>
                    </div>
                    <SaveBar saving={saving} saved={saved} error={error} onSave={save} />
                  </div>
                </>
              )}

              {tab === "sms" && (
                <>
                  <SectionHeader
                    icon={Send}
                    title="SMS notifications"
                    description="Set a per-department sender ID and edit the message templates. Leave a template blank to use the built-in Bangla default."
                  />
                  <div className="card overflow-hidden">
                    <div className="divide-y divide-border">
                      <SettingRow
                        icon={Hash}
                        label="Sender ID"
                        hint="Optional. Overrides the global SMS From/sender for this department."
                      >
                        <input
                          className="input w-64"
                          placeholder="e.g. MyClinic or 8801XXXXXXXXX"
                          value={form.sms_sender_id}
                          onChange={(e) => set("sms_sender_id", e.target.value)}
                        />
                      </SettingRow>

                      {SMS_TEMPLATES.map(({ key, label, hint, placeholders }) => (
                        <SettingRow key={key} icon={MessageSquareText} label={label} hint={hint}>
                          <textarea
                            dir="auto"
                            rows={4}
                            className="input resize-none font-[inherit]"
                            placeholder="Leave blank to use the built-in default."
                            value={form.sms_templates[key]}
                            onChange={(e) => setTemplate(key, e.target.value)}
                          />
                          <div className="mt-2 flex flex-wrap items-center gap-1.5">
                            {placeholders.map((p) => (
                              <code
                                key={p}
                                className="rounded-md border border-border bg-surface-2 px-1.5 py-0.5 text-[11px] text-muted"
                              >
                                {"{" + p + "}"}
                              </code>
                            ))}
                            {defaults && form.sms_templates[key] !== defaults[key] && (
                              <button
                                type="button"
                                onClick={() => setTemplate(key, defaults[key])}
                                className="ml-auto text-[11px] font-medium text-primary hover:underline"
                              >
                                Reset to default
                              </button>
                            )}
                          </div>
                        </SettingRow>
                      ))}
                    </div>
                    <SaveBar saving={saving} saved={saved} error={error} onSave={save} />
                  </div>
                </>
              )}

              {tab === "doctors" && (
                <>
                  <SectionHeader
                    icon={Users}
                    title="Doctors"
                    description="The primary doctor's name is used in the AI greeting and SMS notifications."
                  />
                  <DoctorsManager />
                </>
              )}

              {tab === "reviews" && (
                <>
                  <SectionHeader
                    icon={StarIcon}
                    title="Patient reviews"
                    description="Reviews patients left for this department's doctors. Hidden reviews disappear from the portal and the doctor's average rating."
                  />
                  <ReviewsManager />
                </>
              )}

              {tab === "greetings" && (
                <>
                  <SectionHeader
                    icon={MessageSquareText}
                    title="AI greeting"
                    description="The AI writes its own greeting on every chat and call. Give it instructions here to shape the tone and content."
                  />
                  <div className="card overflow-hidden">
                    <div className="divide-y divide-border">
                      <SettingRow
                        icon={MessageSquareText}
                        label="Greeting instructions"
                        hint="Guidance, not a script — e.g. tone, what to mention (clinic hours, a tagline), how formal to be. Applies to chat and voice. Leave blank for the default warm greeting."
                      >
                        <textarea
                          dir="auto"
                          rows={4}
                          className="input resize-none font-[inherit]"
                          placeholder="উদাহরণ: খুব আন্তরিকভাবে স্বাগত জানাও, হাসপাতালের নাম উল্লেখ করো এবং জানাও যে সন্ধ্যা ৭টার পরেও চেম্বার খোলা।"
                          value={form.greeting_instructions}
                          onChange={(e) => set("greeting_instructions", e.target.value)}
                        />
                      </SettingRow>
                    </div>
                    <SaveBar saving={saving} saved={saved} error={error} onSave={save} />
                  </div>
                </>
              )}
            </div>
          )}
        </main>
      </div>
    </>
  );
}

function SectionHeader({
  icon: Icon,
  title,
  description,
}: {
  icon: React.ElementType;
  title: string;
  description: string;
}) {
  return (
    <div className="flex items-start gap-4">
      <div
        className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl"
        style={{ background: "var(--brand-soft)", color: "var(--primary)" }}
      >
        <Icon size={20} />
      </div>
      <div>
        <h2 className="text-lg font-bold text-fg">{title}</h2>
        <p className="mt-0.5 text-sm text-muted">{description}</p>
      </div>
    </div>
  );
}

function SettingRow({
  icon: Icon,
  label,
  hint,
  children,
}: {
  icon: React.ElementType;
  label: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <div className="grid grid-cols-1 gap-4 px-6 py-5 sm:grid-cols-5">
      <div className="sm:col-span-2">
        <div className="flex items-center gap-2 text-sm font-semibold text-fg">
          <Icon size={14} className="text-primary shrink-0" />
          {label}
        </div>
        {hint && <p className="mt-1 text-xs leading-relaxed text-muted">{hint}</p>}
      </div>
      <div className="sm:col-span-3">{children}</div>
    </div>
  );
}

function SaveBar({
  saving,
  saved,
  error,
  onSave,
}: {
  saving: boolean;
  saved: boolean;
  error: string | null;
  onSave: () => void;
}) {
  return (
    <div
      className="flex items-center justify-between px-6 py-4"
      style={{
        background: "var(--surface-2)",
        borderTop: "1px solid var(--border)",
      }}
    >
      <div className="flex items-center gap-2 text-sm">
        {saved && !saving && (
          <span className="flex items-center gap-1.5 font-medium text-success animate-fade-in">
            <CheckCircle2 size={15} /> Changes saved
          </span>
        )}
        {error && (
          <span className="flex items-center gap-1.5 font-medium text-danger animate-fade-in">
            <AlertCircle size={15} /> {error}
          </span>
        )}
      </div>
      <button onClick={onSave} disabled={saving} className="btn-primary px-5 py-2">
        {saving ? (
          <span className="flex items-center gap-2">
            <span className="h-3.5 w-3.5 rounded-full border-2 border-white/30 border-t-white animate-spin-slow" />
            Saving…
          </span>
        ) : (
          <>
            <Save size={14} /> Save changes
          </>
        )}
      </button>
    </div>
  );
}

function SettingsSkeleton() {
  return (
    <div className="mx-auto max-w-3xl space-y-6 animate-fade-in">
      <div className="flex items-center gap-4">
        <div className="skeleton-avatar h-11 w-11 rounded-xl" />
        <div className="space-y-2">
          <div className="skeleton-title h-5 w-40" />
          <div className="skeleton-text h-3 w-64" />
        </div>
      </div>
      <div className="card overflow-hidden">
        {[...Array(2)].map((_, i) => (
          <div key={i} className="grid grid-cols-5 gap-4 border-b border-border px-6 py-5">
            <div className="col-span-2 space-y-2">
              <div className="skeleton-text h-4 w-24" />
              <div className="skeleton-text h-3 w-40" />
            </div>
            <div className="col-span-3">
              <div className="skeleton h-10 w-full rounded-xl" />
            </div>
          </div>
        ))}
        <div className="flex justify-end px-6 py-4" style={{ background: "var(--surface-2)" }}>
          <div className="skeleton h-9 w-32 rounded-xl" />
        </div>
      </div>
    </div>
  );
}
