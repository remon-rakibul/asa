"use client";

import { useEffect, useRef, useState } from "react";
import { usePathname } from "next/navigation";
import { MessageCircle, Phone, X } from "lucide-react";
import { portalDoctorDetail, DoctorDetail } from "@/lib/api";
import { usePatientAuth } from "@/lib/patientAuth";
import { useLang } from "@/lib/i18n";
import ChatPanel, { DoctorMeta } from "@/components/portal/ChatPanel";
import VoiceCall from "@/components/portal/VoiceCall";

/** Messenger-style floating chat + voice bubbles, mounted once in the portal
 *  layout so the AI assistant is reachable from every page. Both open the
 *  account's ONE unified conversation thread; on a doctor page they carry
 *  that doctor as per-turn context so the agent knows what the patient is
 *  looking at. Hidden on /portal/book (the chat is already full-screen
 *  there) and for logged-out visitors (login/signup pages). */
export default function FloatingAssistant() {
  const { account } = usePatientAuth();
  const { t } = useLang();
  const pathname = usePathname();
  const [chatOpen, setChatOpen] = useState(false);
  const [voiceOpen, setVoiceOpen] = useState(false);
  const [doctorCtx, setDoctorCtx] = useState<DoctorDetail | null>(null);
  // Cache the fetched doctor per id — reopening on the same page is free.
  const doctorCacheRef = useRef<Map<number, DoctorDetail>>(new Map());

  const doctorPageId = (() => {
    const m = pathname?.match(/^\/portal\/doctor\/(\d+)/);
    return m ? Number(m[1]) : null;
  })();

  // Resolve the doctor context lazily when a bubble opens on a doctor page.
  // Failure just falls back to platform mode — never blocks the assistant.
  useEffect(() => {
    if (!chatOpen && !voiceOpen) return;
    if (doctorPageId == null) {
      setDoctorCtx(null);
      return;
    }
    const cached = doctorCacheRef.current.get(doctorPageId);
    if (cached) {
      setDoctorCtx(cached);
      return;
    }
    let stale = false;
    portalDoctorDetail(doctorPageId)
      .then((d) => {
        doctorCacheRef.current.set(doctorPageId, d);
        if (!stale) setDoctorCtx(d);
      })
      .catch(() => { if (!stale) setDoctorCtx(null); });
    return () => { stale = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [chatOpen, voiceOpen, doctorPageId]);

  // Leaving the doctor page drops its context for the NEXT open.
  useEffect(() => {
    if (doctorPageId == null) setDoctorCtx(null);
  }, [doctorPageId]);

  if (!account || pathname === "/portal/book") return null;

  const ctxIsCurrent = doctorPageId != null && doctorCtx?.id === doctorPageId;
  const meta: DoctorMeta | undefined = ctxIsCurrent && doctorCtx
    ? {
        name: doctorCtx.name,
        degrees: doctorCtx.degrees || undefined,
        specialty: doctorCtx.specialty || undefined,
        fee: doctorCtx.fee_new != null ? String(doctorCtx.fee_new) : undefined,
        department: doctorCtx.department_name,
        hospital: doctorCtx.hospital_name,
      }
    : undefined;
  const clinicId = ctxIsCurrent && doctorCtx ? doctorCtx.clinic_id : undefined;
  const ctxDoctorId = ctxIsCurrent && doctorCtx ? doctorCtx.id : undefined;

  return (
    <>
      {voiceOpen && (
        <VoiceCall
          clinicId={clinicId}
          doctorId={ctxDoctorId}
          label={meta?.name ?? t("aiTitle")}
          onClose={() => setVoiceOpen(false)}
        />
      )}

      {/* Popup chat panel: anchored above the bubbles on desktop, full-screen
          on mobile. Unmounted on close — reopening reloads the unified thread
          history (cheap; no LLM turn thanks to the popup greeting throttle). */}
      {chatOpen && (
        <div className="fixed inset-0 z-50 flex flex-col overflow-hidden bg-bg animate-slide-up sm:inset-auto sm:bottom-24 sm:right-5 sm:h-[620px] sm:max-h-[calc(100dvh-7rem)] sm:w-[400px] sm:rounded-3xl sm:border sm:border-border sm:shadow-2xl">
          <ChatPanel
            variant="popup"
            clinicId={clinicId}
            doctorId={ctxDoctorId}
            doctorMeta={meta}
            onClose={() => setChatOpen(false)}
          />
        </div>
      )}

      {/* FABs — hidden on mobile while the full-screen panel is open. */}
      <div className={`fixed bottom-5 right-5 z-40 flex-col items-end gap-3 ${chatOpen ? "hidden sm:flex" : "flex"}`}>
        <button
          onClick={() => setVoiceOpen(true)}
          title={t("faVoice")}
          aria-label={t("faVoice")}
          className="flex h-12 w-12 items-center justify-center rounded-full border border-border bg-surface text-primary shadow-lg transition-transform hover:scale-105"
        >
          <Phone size={19} />
        </button>
        <button
          onClick={() => setChatOpen((v) => !v)}
          title={chatOpen ? t("faClose") : t("faChat")}
          aria-label={chatOpen ? t("faClose") : t("faChat")}
          className="flex h-14 w-14 items-center justify-center rounded-full text-white shadow-xl transition-transform hover:scale-105"
          style={{ background: "var(--brand-grad)" }}
        >
          {chatOpen ? <X size={22} /> : <MessageCircle size={22} />}
        </button>
      </div>
    </>
  );
}
