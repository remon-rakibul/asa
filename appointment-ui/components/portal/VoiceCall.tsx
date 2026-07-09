"use client";

import { useEffect, useRef, useState } from "react";
import {
  BarVisualizer,
  LiveKitRoom,
  RoomAudioRenderer,
  VoiceAssistantControlBar,
  useVoiceAssistant,
  useRoomContext,
  useLocalParticipant,
  useTranscriptions,
} from "@livekit/components-react";
import "@livekit/components-styles";
import { MediaDeviceFailure, RoomEvent } from "livekit-client";
import Link from "next/link";
import { PhoneOff, Loader2, AlertCircle, Stethoscope, MicOff, CheckCircle2, CalendarDays, Ticket } from "lucide-react";
import { portalVoiceToken, VoiceToken } from "@/lib/api";

/** Bangla status text for each LiveKit voice-assistant state. */
const STATE_TEXT: Record<string, string> = {
  connecting: "সংযোগ হচ্ছে…",
  initializing: "প্রস্তুত হচ্ছে…",
  listening: "শুনছি…",
  thinking: "ভাবছি…",
  speaking: "বলছি…",
  disconnected: "সংযোগ বিচ্ছিন্ন",
};

/** Booking result pushed from the worker when a voice booking completes. */
type VoiceBooking = { appointmentId?: string; serial: number | null; slot: string | null };

interface VoiceCallProps {
  /** Department-level call. */
  clinicId?: number;
  /** Hospital-level ("talk to us") call — agent asks which department. */
  hospitalId?: number;
  /** Doctor pre-selected in the wizard, so the agent already knows who. */
  doctorId?: number;
  /** Heading shown in the call card (e.g. department or hospital name). */
  label?: string;
  onClose: () => void;
}

/**
 * Full-screen modal that connects the patient's browser to the LiveKit voice
 * agent. Mints a token via /patient/voice/token, then joins the dispatched room.
 */
export default function VoiceCall({ clinicId, hospitalId, doctorId, label, onClose }: VoiceCallProps) {
  const [conn, setConn] = useState<VoiceToken | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [micDenied, setMicDenied] = useState(false);
  const [booking, setBooking] = useState<VoiceBooking | null>(null);
  const [ended, setEnded] = useState(false);
  // Read inside onDisconnected (whose closure would otherwise be stale).
  const bookingRef = useRef<VoiceBooking | null>(null);
  bookingRef.current = booking;

  useEffect(() => {
    let active = true;
    portalVoiceToken({ clinicId, hospitalId, doctorId })
      .then((t) => { if (active) setConn(t); })
      .catch((e) => {
        if (active) setError(e instanceof Error ? e.message : "ভয়েস কল শুরু করা যায়নি।");
      });
    return () => { active = false; };
  }, [clinicId, hospitalId, doctorId]);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm animate-fade-in"
      role="dialog"
      aria-modal="true"
      aria-label="ভয়েস কল"
    >
      <div className="card relative w-full max-w-md overflow-hidden p-0">
        {/* Header */}
        <div className="flex items-center gap-3 px-5 py-4" style={{ background: "var(--brand-grad)" }}>
          <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-white/20 text-white">
            <Stethoscope size={16} />
          </span>
          <div className="min-w-0 flex-1">
            <div className="truncate text-sm font-bold text-white">ভয়েস কল</div>
            {label && <div className="truncate text-xs text-white/70">{label}</div>}
          </div>
        </div>

        {micDenied ? (
          <div className="flex flex-col items-center gap-4 px-6 py-10 text-center">
            <MicOff size={32} className="text-danger" />
            <div className="space-y-1.5">
              <p className="text-sm font-semibold text-fg">মাইক্রোফোন ব্যবহারের অনুমতি দিন</p>
              <p className="text-xs text-muted">
                ভয়েস কলের জন্য মাইক্রোফোন প্রয়োজন। ব্রাউজারের অ্যাড্রেস বারের পাশের
                আইকনে ক্লিক করে অনুমতি দিন, তারপর আবার চেষ্টা করুন।
              </p>
            </div>
            <button onClick={onClose} className="btn-secondary btn-sm">বন্ধ করুন</button>
          </div>
        ) : booking && ended ? (
          <BookingCard booking={booking} label={label} onClose={onClose} />
        ) : error ? (
          <div className="flex flex-col items-center gap-4 px-6 py-10 text-center">
            <AlertCircle size={32} className="text-danger" />
            <p className="text-sm text-muted">{error}</p>
            <button onClick={onClose} className="btn-secondary btn-sm">বন্ধ করুন</button>
          </div>
        ) : !conn ? (
          <div className="flex flex-col items-center gap-3 px-6 py-12 text-muted">
            <Loader2 size={28} className="animate-spin text-primary" />
            <p className="text-sm">সংযোগ হচ্ছে…</p>
          </div>
        ) : (
          <LiveKitRoom
            serverUrl={conn.serverUrl}
            token={conn.token}
            connect
            audio
            video={false}
            onDisconnected={() => { setEnded(true); if (!bookingRef.current) onClose(); }}
            onError={(e) => setError(e.message || "সংযোগে সমস্যা হয়েছে।")}
            onMediaDeviceFailure={(f) => {
              if (f === MediaDeviceFailure.PermissionDenied || f === MediaDeviceFailure.NotFound) {
                setMicDenied(true);
              }
            }}
          >
            <CallStage onClose={onClose} booking={booking} onBooking={setBooking} />
            <RoomAudioRenderer />
          </LiveKitRoom>
        )}
      </div>
    </div>
  );
}

/** Inner stage rendered once connected — visualizer, captions, and controls. */
function CallStage({ onClose, booking, onBooking }: {
  onClose: () => void;
  booking: VoiceBooking | null;
  onBooking: (b: VoiceBooking) => void;
}) {
  const { state, audioTrack } = useVoiceAssistant();
  const room = useRoomContext();
  const { localParticipant } = useLocalParticipant();
  const transcriptions = useTranscriptions();
  const captionsRef = useRef<HTMLDivElement>(null);

  // Listen for the worker's booking data packet (topic "booking").
  useEffect(() => {
    const onData = (payload: Uint8Array, _p: unknown, _k: unknown, topic?: string) => {
      if (topic !== "booking") return;
      try {
        const data = JSON.parse(new TextDecoder().decode(payload));
        if (data.appointment_id) {
          onBooking({
            appointmentId: data.appointment_id,
            serial: data.serial_number ?? null,
            slot: data.slot_label ?? null,
          });
        }
      } catch { /* ignore malformed packets */ }
    };
    room.on(RoomEvent.DataReceived, onData);
    return () => { room.off(RoomEvent.DataReceived, onData); };
  }, [room, onBooking]);

  // Auto-scroll captions to the latest line.
  useEffect(() => {
    captionsRef.current?.scrollTo({ top: captionsRef.current.scrollHeight, behavior: "smooth" });
  }, [transcriptions]);

  function endCall() {
    room.disconnect();
    onClose();
  }

  const localId = localParticipant?.identity;

  return (
    <div className="flex flex-col items-center gap-5 px-6 py-7">
      <div className="h-20 w-full max-w-[260px]">
        <BarVisualizer
          state={state}
          barCount={5}
          trackRef={audioTrack}
          className="h-full w-full"
          options={{ minHeight: 12 }}
        />
      </div>

      <p className="text-sm font-medium text-muted" aria-live="polite">
        {STATE_TEXT[state] ?? "সংযোগ হচ্ছে…"}
      </p>

      {/* Live captions / transcript */}
      <div
        ref={captionsRef}
        className="h-36 w-full overflow-y-auto rounded-xl border border-border bg-surface-2 px-3 py-2.5"
        aria-live="polite"
      >
        {transcriptions.length === 0 ? (
          <p className="py-8 text-center text-xs text-faint">কথা বলা শুরু করুন…</p>
        ) : (
          <div className="flex flex-col gap-2">
            {transcriptions.map((t, i) => {
              const isUser = t.participantInfo?.identity === localId;
              return (
                <div key={i} className={`flex ${isUser ? "justify-end" : "justify-start"}`}>
                  <span
                    className={`max-w-[85%] rounded-2xl px-3 py-1.5 text-sm leading-snug ${
                      isUser
                        ? "rounded-br-sm text-white"
                        : "rounded-bl-sm border border-border bg-surface text-fg"
                    }`}
                    style={isUser ? { background: "var(--brand-grad)" } : undefined}
                    dir="auto"
                  >
                    {t.text}
                  </span>
                </div>
              );
            })}
          </div>
        )}
      </div>

      {/* Inline booking banner while still connected */}
      {booking && (
        <div className="flex w-full items-center gap-2 rounded-xl border border-success/30 px-3 py-2 text-sm"
          style={{ background: "var(--success-bg)" }}>
          <CheckCircle2 size={15} className="shrink-0 text-success" />
          <span className="text-success">
            বুক হয়েছে{booking.serial != null ? ` · সিরিয়াল #${booking.serial}` : ""}
          </span>
        </div>
      )}

      <div className="flex items-center gap-3">
        {/* Mic mute toggle (hide LiveKit's own leave button — we use ours). */}
        <VoiceAssistantControlBar controls={{ leave: false }} />
        <button
          onClick={endCall}
          aria-label="কল শেষ করুন"
          title="কল শেষ করুন"
          className="flex h-11 w-11 items-center justify-center rounded-full bg-danger text-white shadow-lg transition hover:brightness-110 active:scale-95"
        >
          <PhoneOff size={18} />
        </button>
      </div>
    </div>
  );
}

/** Post-call confirmation shown after the call ends with a successful booking. */
function BookingCard({ booking, label, onClose }: {
  booking: VoiceBooking;
  label?: string;
  onClose: () => void;
}) {
  return (
    <div className="px-6 py-7">
      <div className="flex flex-col items-center gap-2 text-center">
        <CheckCircle2 size={36} className="text-success" />
        <p className="text-base font-bold text-success">অ্যাপয়েন্টমেন্ট নিশ্চিত হয়েছে</p>
      </div>
      <div className="mt-4 space-y-2.5 rounded-xl border border-border bg-surface-2 px-4 py-3.5">
        {label && (
          <div className="flex items-center gap-2 text-sm text-fg">
            <Stethoscope size={14} className="shrink-0 text-primary" />
            <span className="font-semibold">{label}</span>
          </div>
        )}
        {booking.slot && (
          <div className="flex items-center gap-2 text-sm text-muted">
            <CalendarDays size={14} className="shrink-0 text-primary" />
            <span dir="auto">{booking.slot}</span>
          </div>
        )}
        {booking.serial != null && (
          <div className="flex items-center gap-2 text-sm text-muted">
            <Ticket size={14} className="shrink-0 text-primary" />
            সিরিয়াল নম্বর <span className="font-bold text-fg">#{booking.serial}</span>
          </div>
        )}
      </div>
      <div className="mt-4 flex gap-2">
        <Link href="/portal/appointments" className="btn-primary btn-sm flex-1 justify-center" onClick={onClose}>
          <CalendarDays size={14} /> My Appointments
        </Link>
        <button onClick={onClose} className="btn-ghost btn-sm">বন্ধ করুন</button>
      </div>
    </div>
  );
}
