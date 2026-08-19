"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { ArrowLeft, Send, Stethoscope, RefreshCw, Square, RotateCcw, CheckCircle2, CalendarDays, Ticket, Phone, Clock, AlertTriangle, X, CreditCard, ExternalLink, Sparkles } from "lucide-react";
import { API_BASE, getPatientToken, clearPatientToken, getChatHistory, clearChatHistory, ChatHistoryMessage, ChatSlot, ChatConfirm, ChatPayment, portalGetPayment } from "@/lib/api";
import { usePatientAuth } from "@/lib/patientAuth";
import { useLang } from "@/lib/i18n";
import VoiceCall from "@/components/portal/VoiceCall";

interface Message {
  role: "user" | "assistant";
  text: string;
  statusText?: string;
  streaming?: boolean;
  slots?: ChatSlot[];
}

/** Display-only doctor/department context for the header + booking card. */
export interface DoctorMeta {
  name?: string;
  degrees?: string;
  specialty?: string;
  fee?: string;
  department?: string;
  hospital?: string;
}

export interface ChatPanelProps {
  /** Department context for this turn (deep link) — omitted = platform mode. */
  clinicId?: number;
  /** Pre-selected doctor sent with each turn so the agent's state knows it. */
  doctorId?: number;
  doctorMeta?: DoctorMeta;
  /** "page" = full-screen /portal/book; "popup" = floating widget panel. */
  variant: "page" | "popup";
  /** Popup close button (X). */
  onClose?: () => void;
}

// Minimal, dependency-free inline formatter: **bold**, *italic*, and newlines.
// The agent is told not to emit markdown, so this is just defensive polish.
function RichText({ text }: { text: string }) {
  const parts = text.split(/(\*\*[^*]+\*\*|\*[^*]+\*)/g).filter(Boolean);
  return (
    <span className="whitespace-pre-wrap">
      {parts.map((p, i) => {
        if (p.startsWith("**") && p.endsWith("**"))
          return <strong key={i}>{p.slice(2, -2)}</strong>;
        if (p.startsWith("*") && p.endsWith("*"))
          return <em key={i}>{p.slice(1, -1)}</em>;
        return <span key={i}>{p}</span>;
      })}
    </span>
  );
}

function TypingDots() {
  return (
    <span className="inline-flex items-center gap-1 py-1">
      {[0, 1, 2].map((i) => (
        <span
          key={i}
          className="h-1.5 w-1.5 rounded-full bg-current opacity-60 animate-bounce"
          style={{ animationDelay: `${i * 160}ms`, animationDuration: "1s" }}
        />
      ))}
    </span>
  );
}

function Avatar({ role, initial }: { role: "user" | "assistant"; initial?: string }) {
  if (role === "assistant") {
    return (
      <span
        className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-white shadow-sm"
        style={{ background: "var(--brand-grad)" }}
      >
        <Stethoscope size={14} />
      </span>
    );
  }
  return (
    <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-surface-3 text-xs font-bold text-fg shadow-sm">
      {initial ?? "?"}
    </span>
  );
}

function MessageBubble({ msg, initial }: { msg: Message; initial?: string }) {
  const { t } = useLang();
  const isUser = msg.role === "user";
  const isEmpty = !msg.text && msg.streaming;

  return (
    <div className={`flex w-full items-end gap-2.5 animate-fade-in ${isUser ? "flex-row-reverse" : ""}`}>
      {/* Avatar is always FIRST in DOM — flex-row-reverse pushes it to the far right for user */}
      {isUser
        ? <Avatar role="user" initial={initial} />
        : <Avatar role="assistant" />
      }
      <div
        className={`relative max-w-[72%] rounded-2xl px-4 py-3 text-sm leading-relaxed shadow-sm ${
          isUser
            ? "rounded-br-sm text-white"
            : "rounded-bl-sm border border-border bg-surface text-fg"
        }`}
        style={isUser ? { background: "var(--brand-grad)" } : undefined}
      >
        {isEmpty ? (
          <span className="text-muted"><TypingDots /></span>
        ) : msg.streaming && !msg.text ? (
          <span className="text-muted text-xs italic">{msg.statusText ?? t("thinking")}</span>
        ) : (
          <span>
            <RichText text={msg.text} />
            {msg.streaming && <span className="ml-0.5 inline-block h-4 w-0.5 animate-pulse bg-current opacity-70" />}
          </span>
        )}
      </div>
    </div>
  );
}

// Tappable slot grid rendered under the assistant turn that offered times.
// Tapping a slot does NOT book immediately — it raises a confirm step in the
// parent so the patient explicitly approves before the agent commits.
function SlotPicker({ slots, onPick, disabled }: {
  slots: ChatSlot[];
  onPick: (slot: ChatSlot) => void;
  disabled?: boolean;
}) {
  return (
    <div className="ml-10 flex flex-wrap gap-2">
      {slots.map((s) => (
        <button
          key={s.datetime}
          onClick={() => onPick(s)}
          disabled={disabled}
          className="inline-flex items-center gap-1.5 rounded-xl border border-border bg-surface px-3 py-2 text-sm text-fg shadow-sm transition-colors hover:border-primary/40 hover:bg-[var(--brand-soft)] disabled:opacity-40"
        >
          <Clock size={13} className="text-primary" />
          <span dir="auto">{s.label}</span>
        </button>
      ))}
    </div>
  );
}

/** The booking-agent chat. ONE conversation per account — every mount (page
 *  or popup, any department context) resumes the same unified thread; the
 *  server derives the thread id from the JWT. clinicId/doctorId are per-TURN
 *  context sent with each message, not a different conversation. */
export default function ChatPanel({ clinicId, doctorId, doctorMeta, variant, onClose }: ChatPanelProps) {
  const { account } = usePatientAuth();
  const { t } = useLang();
  const router = useRouter();
  const isPopup = variant === "popup";
  const department = doctorMeta?.department ?? "";
  const hospital = doctorMeta?.hospital ?? "";
  const doctor = doctorMeta?.name ?? "";
  const fee = doctorMeta?.fee ?? "";

  const userInitial = account?.name
    ? account.name.trim().split(" ")[0][0].toUpperCase()
    : "?";

  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [historyLoaded, setHistoryLoaded] = useState(false);
  const [failed, setFailed] = useState(false);
  const [voiceOpen, setVoiceOpen] = useState(false);
  // null = connecting (waiting for the first model response), true = connected,
  // false = connection failed.
  const [online, setOnline] = useState<boolean | null>(null);
  const [booking, setBooking] = useState<{ serial: number | null; slot: string | null } | null>(null);
  const [suggestions, setSuggestions] = useState<string[]>([]);
  // Slot the patient tapped, awaiting explicit confirmation before booking.
  const [pendingSlot, setPendingSlot] = useState<ChatSlot | null>(null);
  // Agent-side confirm question (cancel/reschedule interrupt) awaiting yes/no.
  const [agentConfirm, setAgentConfirm] = useState<ChatConfirm | null>(null);
  // Held-booking pay card (deterministic chrome from the `payment` stream event).
  const [payment, setPayment] = useState<ChatPayment | null>(null);
  const [payLeft, setPayLeft] = useState<string | null>(null);
  const payPollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  // Free-tier monthly cap hit — the agent streamed an `upgrade` event.
  const [upgrade, setUpgrade] = useState<boolean>(false);
  const bottomRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const startedRef = useRef(false);
  const abortRef = useRef<AbortController | null>(null);
  const lastSentRef = useRef<{ message: string; showUser: boolean } | null>(null);
  // appointment_id already reported by the thread — `end` events repeat the id
  // of a past booking on every later turn (state persists in the checkpointer),
  // so the confirmation card is shown only when a NEW id appears.
  const seenApptIdRef = useRef<string | null>(null);

  // Returns true if the stream completed normally (an `end` event arrived).
  // `greeting` marks the silent session-start turn: a returning thread's state
  // still carries the previous booking's appointment_id, which must be recorded
  // but not re-shown as a fresh confirmation card.
  async function consumeStream(res: Response, greeting = false): Promise<boolean> {
    if (!res.ok || !res.body) throw new Error(`HTTP ${res.status}`);
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let receivedEnd = false;
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop() ?? "";
      for (const line of lines) {
        if (!line.startsWith("data: ")) continue;
        const payload = JSON.parse(line.slice(6));
        // First byte from the server means the connection is live.
        setOnline(true);
        if (payload.type === "token") {
          // No flushSync — React batches token updates for smoother long streams.
          setMessages((prev) => {
            const next = [...prev];
            const last = next[next.length - 1];
            if (last?.role === "assistant")
              next[next.length - 1] = { ...last, text: last.text + payload.text, statusText: undefined };
            return next;
          });
        } else if (payload.type === "retract") {
          // The agent invented a listing right before its tool call — the
          // backend retracted it; roll the bubble back to the kept prefix.
          setMessages((prev) => {
            const next = [...prev];
            const last = next[next.length - 1];
            if (last?.role === "assistant")
              next[next.length - 1] = { ...last, text: last.text.slice(0, payload.keep ?? 0) };
            return next;
          });
        } else if (payload.type === "status") {
          setMessages((prev) => {
            const next = [...prev];
            const last = next[next.length - 1];
            if (last?.role === "assistant" && !last.text)
              next[next.length - 1] = { ...last, statusText: payload.text };
            return next;
          });
        } else if (payload.type === "slots") {
          // Attach the tappable slot grid to the current assistant turn.
          setMessages((prev) => {
            const next = [...prev];
            const last = next[next.length - 1];
            if (last?.role === "assistant")
              next[next.length - 1] = { ...last, slots: payload.slots as ChatSlot[] };
            return next;
          });
        } else if (payload.type === "suggestions") {
          setSuggestions((payload.items as string[]) ?? []);
        } else if (payload.type === "confirm") {
          // The agent paused for an explicit yes/no (cancel/reschedule).
          setAgentConfirm(payload as ChatConfirm);
        } else if (payload.type === "payment") {
          // A held booking needs an online fee before it confirms. The pay URL
          // is deterministic chrome (never through the LLM) — show a pay card.
          setBooking(null);
          setPayment(payload as ChatPayment);
        } else if (payload.type === "upgrade") {
          // Free-tier monthly AI-booking cap reached — show an upgrade nudge.
          setUpgrade(true);
        } else if (payload.type === "end") {
          receivedEnd = true;
          // Unlock the input right away — the stream stays open a little
          // longer only to deliver the LLM-composed suggestion chips.
          setBusy(false);
          if (payload.done && payload.appointment_id) {
            const isNewBooking =
              !greeting && payload.appointment_id !== seenApptIdRef.current;
            seenApptIdRef.current = payload.appointment_id;
            if (isNewBooking) {
              setBooking({ serial: payload.serial_number ?? null, slot: payload.slot_label ?? null });
            }
          }
          setMessages((prev) => {
            const next = [...prev];
            const last = next[next.length - 1];
            if (last?.role === "assistant") {
              // A confirm-pause turn (cancel/reschedule) speaks nothing — the
              // confirm buttons carry the turn. Drop the empty placeholder
              // bubble instead of leaving a blank assistant message behind.
              if (!last.text && !last.slots) return next.slice(0, -1);
              next[next.length - 1] = { ...last, streaming: false };
            }
            return next;
          });
        }
      }
    }
    return receivedEnd;
  }

  function finalizeLast(text: string | null) {
    setMessages((prev) => {
      const next = [...prev];
      const last = next[next.length - 1];
      if (last?.role === "assistant") {
        const finalText = text ?? last.text;
        // Nothing was said (e.g. Stop pressed before the first token) —
        // remove the placeholder rather than leave an empty bubble.
        if (!finalText && !last.slots) return next.slice(0, -1);
        next[next.length - 1] = { ...last, streaming: false, text: finalText };
      }
      return next;
    });
  }

  async function send(message: string, showUser: boolean, resume?: boolean) {
    if (!account) return;
    lastSentRef.current = { message, showUser };
    setFailed(false);
    setBusy(true);
    setSuggestions([]);
    setPendingSlot(null);
    setAgentConfirm(null);
    setUpgrade(false);
    // A previous stream may still be open past its `end` event (late
    // LLM-composed suggestion chips arrive there) — cut it off so a stale
    // suggestions event can't land mid-way through the new turn.
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    setMessages((prev) => [
      ...prev,
      ...(showUser ? [{ role: "user" as const, text: message }] : []),
      { role: "assistant" as const, text: "", streaming: true },
    ]);
    try {
      const res = await fetch(`${API_BASE}/patient/chat/stream`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${getPatientToken() ?? ""}`,
        },
        // The server derives the thread id from the JWT (one unified thread
        // per account); clinic/doctor ids are per-turn context only.
        body: JSON.stringify({
          message,
          ...(clinicId !== undefined ? { clinic_id: clinicId } : {}),
          ...(doctorId !== undefined ? { doctor_id: doctorId } : {}),
          ...(resume === undefined ? {} : { resume }),
        }),
        signal: controller.signal,
      });
      if (res.status === 401) {
        // Token expired mid-session (12h TTL). Without this, the expiry
        // surfaces as a misleading "connection problem" + a retry loop that
        // can never succeed — send the patient back to login instead.
        clearPatientToken();
        finalizeLast(t("sessionExpired"));
        router.replace("/portal/login");
        return;
      }
      const ok = await consumeStream(res, message === "" && !showUser);
      if (!ok) {
        // Stream ended without an `end` event — partial/failed reply.
        setFailed(true);
        finalizeLast(null);
      }
    } catch {
      // A newer turn may have already superseded this stream: send() aborts the
      // previous (still-open-for-suggestions) stream BEFORE pushing its own
      // placeholder. When that abort's rejection lands here, the current
      // `messages` tail is the NEW turn's empty placeholder — so finalizing or
      // erroring would strip it, and the new reply's tokens would land on no
      // assistant bubble and stay invisible until a reload. Only the active
      // stream (still owning abortRef) may touch shared state.
      if (abortRef.current !== controller) return;
      if (controller.signal.aborted) {
        // User pressed Stop / closed the popup — keep what streamed, no error.
        finalizeLast(null);
      } else {
        setFailed(true);
        setOnline(false);
        setMessages((prev) => {
          const next = [...prev];
          const last = next[next.length - 1];
          if (last?.role === "assistant")
            next[next.length - 1] = {
              ...last,
              streaming: false,
              text: last.text || t("connectionError"),
            };
          return next;
        });
      }
    } finally {
      // Only the active stream owns busy/abortRef — a superseded one must not
      // reset them (that would unlock the input and drop the new turn's
      // controller mid-flight).
      if (abortRef.current === controller) {
        setBusy(false);
        abortRef.current = null;
      }
      inputRef.current?.focus();
    }
  }

  function stop() {
    abortRef.current?.abort();
  }

  // Wipe the saved thread (checkpointer + conversation_log) and start over.
  async function newConversation() {
    if (busy) return;
    abortRef.current?.abort();
    try { await clearChatHistory("platform"); } catch { /* best-effort — still reset locally */ }
    startedRef.current = false;
    setMessages([]);
    setFailed(false);
    setBooking(null);
    setPayment(null);
    setAgentConfirm(null);
    setOnline(null);
    send("", false);
    startedRef.current = true;
  }

  function retry() {
    const last = lastSentRef.current;
    if (!last || busy) return;
    setFailed(false);
    // Drop the failed assistant bubble; the user's message stays in place.
    setMessages((prev) => {
      const next = [...prev];
      if (next[next.length - 1]?.role === "assistant") next.pop();
      return next;
    });
    send(last.message, false);
  }

  // Load history from checkpointer, then start (or resume) the conversation
  useEffect(() => {
    if (startedRef.current || !account) return;
    startedRef.current = true;

    (async () => {
      let history: ChatHistoryMessage[] = [];
      try {
        history = await getChatHistory("platform");
      } catch {
        // no prior session — start fresh
      }

      if (history.length > 0) {
        // Returning patient — show history. On the full page the agent then
        // speaks a welcome-back line (an empty-message graph turn); the POPUP
        // skips that turn — each bubble open would otherwise burn a full CPU
        // LLM turn just to say hello again. If the last turn offered slots or
        // paused on a confirm question, the patient was mid-flow — keep that
        // visible instead of burying it under a fresh greeting.
        const last = history[history.length - 1];
        const midBooking =
          last?.role === "assistant" && ((last.slots?.length ?? 0) > 0 || !!last.confirm);
        if (last?.confirm) setAgentConfirm(last.confirm);
        setMessages(history);
        setHistoryLoaded(true);
        setOnline(true);
        if (!midBooking && !isPopup) send("", false);
      } else {
        // New session — agent sends the opening greeting
        setHistoryLoaded(true);
        send("", false);
      }
    })();
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [account]);

  // Closing the popup mid-stream must cancel the fetch, not leak it.
  useEffect(() => () => abortRef.current?.abort(), []);

  // Live mm:ss countdown for the pay card; null once the hold has elapsed.
  useEffect(() => {
    if (!payment?.expires_at) { setPayLeft(null); return; }
    const target = new Date(payment.expires_at).getTime();
    const tick = () => {
      const ms = target - Date.now();
      if (ms <= 0) { setPayLeft(null); return false; }
      const s = Math.floor(ms / 1000);
      setPayLeft(`${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`);
      return true;
    };
    if (!tick()) return;
    const id = setInterval(() => { if (!tick()) clearInterval(id); }, 1000);
    return () => clearInterval(id);
  }, [payment?.expires_at]);

  // While a pay card is showing, poll the payment until it confirms (or the
  // gateway reports it failed/expired). On success, swap to the booking card.
  useEffect(() => {
    if (!payment) return;
    if (payPollRef.current) clearInterval(payPollRef.current);
    payPollRef.current = setInterval(async () => {
      const p = await portalGetPayment(payment.payment_id).catch(() => null);
      if (!p) return;
      if (p.appointment_status === "confirmed" || p.status === "paid") {
        setPayment(null);
        setBooking({ serial: null, slot: null });
      } else if (p.status === "failed" || p.status === "expired") {
        // Keep the card but let the countdown/expiry messaging take over.
        setPayment((cur) => (cur ? { ...cur, expires_at: null } : cur));
      }
    }, 3000);
    return () => { if (payPollRef.current) clearInterval(payPollRef.current); };
  }, [payment]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  function submit() {
    const text = input.trim();
    if (!text || busy) return;
    setInput("");
    send(text, true);
  }

  function handleKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      submit();
    }
  }

  return (
    <div className={`flex w-full flex-col bg-bg ${isPopup ? "h-full" : "h-screen"}`}>
      {/* Header */}
      <header className="flex items-center gap-3 border-b border-border bg-surface px-4 py-3 shrink-0 shadow-sm">
        {!isPopup && (
          <Link href="/portal" className="btn-ghost btn-sm rounded-xl">
            <ArrowLeft size={16} />
          </Link>
        )}
        <span
          className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl text-white shadow"
          style={{ background: "var(--brand-grad)" }}
        >
          <Stethoscope size={15} />
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="truncate text-sm font-semibold text-fg">
              {clinicId === undefined ? t("aiTitle") : department || t("bookingHeader")}
            </span>
            {online === null ? (
              <span className="shrink-0 text-xs text-faint animate-pulse">connecting…</span>
            ) : online ? (
              <span
                className="h-2 w-2 shrink-0 rounded-full"
                style={{ background: "var(--success)" }}
                title="connected"
                aria-label="connected"
              />
            ) : (
              <span
                className="h-2 w-2 shrink-0 rounded-full"
                style={{ background: "var(--danger)" }}
                title="offline"
                aria-label="offline"
              />
            )}
          </div>
          <div className="truncate text-xs text-muted">
            {clinicId === undefined ? t("allHospitals") : hospital}
            {doctor ? ` · ${doctor}` : ""}
            {doctorMeta?.degrees ? `, ${doctorMeta.degrees}` : ""}
            {doctorMeta?.specialty ? ` (${doctorMeta.specialty})` : ""}
          </div>
        </div>
        {fee && !isPopup && (
          <span className="hidden shrink-0 rounded-full bg-[var(--brand-soft)] px-2.5 py-1 text-[11px] font-bold text-primary sm:inline">
            {t("fee")} ৳{fee}
          </span>
        )}
        <button
          onClick={() => setVoiceOpen(true)}
          className={`btn-primary btn-sm flex items-center gap-1.5 ${isPopup ? "px-2.5" : ""}`}
          title={t("speakVoice")}
          aria-label={t("speakVoice")}
        >
          <Phone size={13} />{!isPopup && " Voice"}
        </button>
        {messages.length > 0 && (
          <button
            onClick={newConversation}
            disabled={busy}
            className="btn-ghost flex items-center gap-1 px-2.5 py-1.5 text-xs text-muted disabled:opacity-40"
            title={t("newConversation")}
            aria-label={t("newConversation")}
          >
            <RefreshCw size={12} />{!isPopup && " New"}
          </button>
        )}
        {isPopup && onClose && (
          <button
            onClick={onClose}
            className="btn-ghost btn-sm rounded-xl px-2"
            title={t("faClose")}
            aria-label={t("faClose")}
          >
            <X size={16} />
          </button>
        )}
      </header>

      {voiceOpen && (
        <VoiceCall
          clinicId={clinicId}
          doctorId={doctorId}
          label={clinicId === undefined ? t("aiTitle") : department || hospital || undefined}
          onClose={() => setVoiceOpen(false)}
        />
      )}

      {/* Message area */}
      <div className="flex-1 overflow-y-auto px-4 py-5">
        <div className="mx-auto flex w-full max-w-3xl flex-col gap-4" aria-live="polite" aria-atomic="false">
          {!historyLoaded && (
            <div className="flex justify-center py-12">
              <div className="flex items-center gap-2 text-sm text-muted">
                <span className="flex gap-1">
                  {[0,1,2].map(i => (
                    <span key={i} className="h-1.5 w-1.5 rounded-full bg-muted animate-bounce"
                      style={{ animationDelay: `${i * 160}ms` }} />
                  ))}
                </span>
                {t("loading")}
              </div>
            </div>
          )}

          {(() => {
            // Render the slot grid only under the most recent turn that offered
            // slots, and only while no booking has completed.
            let lastSlotIdx = -1;
            messages.forEach((m, i) => { if (m.slots?.length) lastSlotIdx = i; });
            return messages.map((m, i) => (
              <div key={i} className="flex flex-col gap-3">
                <MessageBubble msg={m} initial={userInitial} />
                {i === lastSlotIdx && !booking && !agentConfirm && m.slots && (
                  pendingSlot ? (
                    <div className="ml-10 max-w-md animate-slide-up rounded-2xl border border-primary/30 bg-surface p-4 shadow-sm">
                      <div className="flex items-center gap-2 text-sm text-fg">
                        <Clock size={15} className="text-primary" />
                        <span>{t("bookThisTime")}</span>
                      </div>
                      <p className="mt-1.5 text-sm font-semibold text-fg" dir="auto">{pendingSlot.label}</p>
                      <div className="mt-3 flex gap-2">
                        <button
                          onClick={() => { const s = pendingSlot; setPendingSlot(null); send(s.label, true); }}
                          disabled={busy}
                          className="btn-primary btn-sm flex items-center gap-1.5 disabled:opacity-40"
                        >
                          <CheckCircle2 size={14} /> {t("confirmBtn")}
                        </button>
                        <button
                          onClick={() => setPendingSlot(null)}
                          disabled={busy}
                          className="btn-ghost btn-sm disabled:opacity-40"
                        >
                          {t("changeBtn")}
                        </button>
                      </div>
                    </div>
                  ) : (
                    <SlotPicker slots={m.slots} onPick={setPendingSlot} disabled={busy} />
                  )
                )}
              </div>
            ));
          })()}

          {/* Agent confirm card: cancel/reschedule needs an explicit yes/no */}
          {agentConfirm && !busy && (
            <div className="ml-10 max-w-md animate-slide-up rounded-2xl border border-danger/30 bg-surface p-4 shadow-sm">
              <div className="flex items-center gap-2 text-sm text-fg">
                <AlertTriangle size={15} className="text-danger" />
                <span dir="auto">{agentConfirm.question}</span>
              </div>
              {agentConfirm.appointment && (
                <p className="mt-1.5 text-sm font-semibold text-fg" dir="auto">
                  {agentConfirm.appointment.label}
                  {agentConfirm.appointment.doctor_name ? ` · ${t("drPrefix")} ${agentConfirm.appointment.doctor_name}` : ""}
                  {agentConfirm.kind === "confirm_reschedule" && agentConfirm.slot_label
                    ? ` → ${agentConfirm.slot_label}` : ""}
                </p>
              )}
              <div className="mt-3 flex gap-2">
                <button
                  onClick={() => send("হ্যাঁ, নিশ্চিত করুন", true, true)}
                  className="btn-primary btn-sm flex items-center gap-1.5"
                >
                  <CheckCircle2 size={14} /> {t("confirmBtn")}
                </button>
                <button
                  onClick={() => send("না", true, false)}
                  className="btn-ghost btn-sm"
                >
                  {t("noBtn")}
                </button>
              </div>
            </div>
          )}

          {/* Held booking — pay the fee to confirm (deterministic pay card) */}
          {payment && !booking && (
            <div className="animate-slide-up mx-auto w-full max-w-md overflow-hidden rounded-2xl border border-primary/30 bg-surface shadow-lg">
              <div className="flex items-center gap-2 px-5 py-3" style={{ background: "var(--brand-soft)" }}>
                <CreditCard size={18} className="text-primary" />
                <span className="text-sm font-bold text-primary">{t("payFeeTitle")}</span>
              </div>
              <div className="space-y-3 px-5 py-4">
                <p className="text-sm text-fg">{t("payFeeBody", { n: payment.amount })}</p>
                <div className="flex items-center gap-2 text-xs text-muted">
                  <Clock size={13} className="shrink-0 text-primary" />
                  <span>{payLeft ? t("payHoldNote", { m: payLeft }) : t("payExpired")}</span>
                </div>
                {payment.pay_url && payLeft && (
                  <a
                    href={payment.pay_url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="btn-primary btn-sm w-full justify-center"
                  >
                    <ExternalLink size={14} /> {t("payNowCta")}
                  </a>
                )}
                <p className="text-[11px] text-faint">{t("payAtDeskNote")}</p>
              </div>
            </div>
          )}

          {/* Free-tier cap reached — upgrade nudge (direct booking still works) */}
          {upgrade && (
            <div className="animate-slide-up mx-auto w-full max-w-md overflow-hidden rounded-2xl border border-primary/30 bg-surface shadow-lg">
              <div className="flex items-center gap-2 px-5 py-3" style={{ background: "var(--brand-soft)" }}>
                <Sparkles size={18} className="text-primary" />
                <span className="text-sm font-bold text-primary">{t("upgradeChatTitle")}</span>
              </div>
              <div className="space-y-3 px-5 py-4">
                <p className="text-sm text-fg">{t("upgradeChatBody")}</p>
                <Link href="/portal/account" className="btn-primary btn-sm w-full justify-center">
                  <Sparkles size={14} /> {t("upgradeCta")}
                </Link>
              </div>
            </div>
          )}

          {/* Structured booking confirmation */}
          {booking && (
            <div className="animate-slide-up mx-auto w-full max-w-md overflow-hidden rounded-2xl border border-success/30 bg-surface shadow-lg">
              <div className="flex items-center gap-2 px-5 py-3" style={{ background: "var(--success-bg)" }}>
                <CheckCircle2 size={18} className="text-success" />
                <span className="text-sm font-bold text-success">{t("vcConfirmed")}</span>
              </div>
              <div className="space-y-2.5 px-5 py-4">
                {(department || hospital) && (
                  <div className="flex items-center gap-2 text-sm text-fg">
                    <Stethoscope size={14} className="shrink-0 text-primary" />
                    <span className="font-semibold">{department || hospital}</span>
                    {doctor && <span className="text-muted">· {doctor}</span>}
                  </div>
                )}
                {booking.slot && (
                  <div className="flex items-center gap-2 text-sm text-muted">
                    <CalendarDays size={14} className="shrink-0 text-primary" />
                    {booking.slot}
                  </div>
                )}
                {booking.serial != null && (
                  <div className="flex items-center gap-2 text-sm text-muted">
                    <Ticket size={14} className="shrink-0 text-primary" />
                    {t("vcSerialLabel")} <span className="font-bold text-fg">#{booking.serial}</span>
                  </div>
                )}
                <Link
                  href="/portal/appointments"
                  className="btn-primary btn-sm mt-1 w-full justify-center"
                >
                  <CalendarDays size={14} /> {t("viewInAppts")}
                </Link>
              </div>
            </div>
          )}

          <div ref={bottomRef} />
        </div>
      </div>

      {/* Input bar — chips/retry float above the divider so the border sits
          directly on top of the input row, not above the whole block */}
      <div className="shrink-0">
        {/* Quick-reply suggestion chips */}
        {suggestions.length > 0 && !busy && !pendingSlot && (
          <div className="mx-auto flex w-full max-w-3xl flex-wrap justify-center gap-2 px-4 pb-2">
            {suggestions.map((s, i) => (
              <button
                key={i}
                onClick={() => send(s, true)}
                className="rounded-full border border-primary/30 bg-[var(--brand-soft)] px-3.5 py-1.5 text-xs font-medium text-primary transition-colors hover:bg-primary/10"
              >
                {s}
              </button>
            ))}
          </div>
        )}
        {/* Retry affordance after a failed reply */}
        {failed && !busy && (
          <div className="mx-auto flex w-full max-w-3xl justify-center px-4 pb-2">
            <button
              onClick={retry}
              className="inline-flex items-center gap-1.5 rounded-full border border-danger/30 bg-danger/10 px-3 py-1.5 text-xs font-semibold text-danger transition-colors hover:bg-danger/20"
            >
              <RotateCcw size={13} /> {t("retryBtn")}
            </button>
          </div>
        )}
        <div className="border-t border-border bg-surface px-4 py-3">
        <div className="mx-auto flex w-full max-w-3xl items-end gap-2.5">
          <textarea
            ref={inputRef}
            rows={1}
            value={input}
            onChange={(e) => {
              setInput(e.target.value);
              e.target.style.height = "auto";
              e.target.style.height = Math.min(e.target.scrollHeight, 120) + "px";
            }}
            onKeyDown={handleKeyDown}
            disabled={!historyLoaded}
            placeholder={t("chatPlaceholder")}
            className="input flex-1 resize-none overflow-hidden leading-relaxed"
            style={{ minHeight: "44px" }}
          />
          {busy ? (
            <button
              onClick={stop}
              aria-label="Stop generating"
              title={t("stop")}
              className="btn-secondary flex h-11 w-11 shrink-0 items-center justify-center rounded-2xl p-0"
            >
              <Square size={15} className="fill-current" />
            </button>
          ) : (
            <button
              onClick={submit}
              disabled={!input.trim() || !historyLoaded}
              aria-label="Send message"
              className="btn-primary flex h-11 w-11 shrink-0 items-center justify-center rounded-2xl p-0 transition-all disabled:opacity-40"
            >
              <Send size={15} />
            </button>
          )}
        </div>
        </div>
      </div>
    </div>
  );
}
