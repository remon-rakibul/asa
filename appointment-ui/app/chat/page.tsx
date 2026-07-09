"use client";

import { useEffect, useRef, useState } from "react";
import { flushSync } from "react-dom";
import Link from "next/link";
import {
  Send,
  RotateCcw,
  ArrowLeft,
  Stethoscope,
  Wifi,
  WifiOff,
  User,
} from "lucide-react";
import { useAuth } from "@/lib/auth";

const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
const API_KEY = process.env.NEXT_PUBLIC_API_KEY ?? "";
const AUTH_HEADERS: Record<string, string> = API_KEY ? { "X-API-Key": API_KEY } : {};

interface Message {
  role: "user" | "assistant";
  text: string;
  statusText?: string;   // interim tool status shown before first token arrives
  streaming?: boolean;
  ts: Date;
}

function generateSessionId(): string {
  if (typeof crypto !== "undefined" && crypto.randomUUID) return `web-${crypto.randomUUID()}`;
  return `web-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

function formatTime(d: Date) {
  return d.toLocaleTimeString("bn-BD", { hour: "2-digit", minute: "2-digit" });
}

function ThinkingDots() {
  return (
    <span className="inline-flex items-end gap-1 h-4">
      {[0, 1, 2].map((i) => (
        <span
          key={i}
          className="inline-block h-2 w-2 animate-bounce rounded-full"
          style={{
            animationDelay: `${i * 160}ms`,
            background: "var(--primary)",
            opacity: 0.7,
          }}
        />
      ))}
    </span>
  );
}

function BotAvatar() {
  return (
    <span
      className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-white shadow"
      style={{ background: "var(--brand-grad)" }}
    >
      <Stethoscope size={14} />
    </span>
  );
}

function UserAvatar() {
  return (
    <span
      className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full"
      style={{ background: "var(--surface-3)", border: "1px solid var(--border)" }}
    >
      <User size={14} className="text-muted" />
    </span>
  );
}

function ChatBubble({ msg }: { msg: Message }) {
  const isUser = msg.role === "user";

  return (
    <div className={`flex items-end gap-2.5 ${isUser ? "flex-row-reverse" : "flex-row"}`}>
      {isUser ? <UserAvatar /> : <BotAvatar />}

      <div className={`flex flex-col gap-1 max-w-[72%] ${isUser ? "items-end" : "items-start"}`}>
        <div
          className="rounded-2xl px-4 py-3 text-sm leading-relaxed shadow-sm"
          style={
            isUser
              ? {
                  background: "var(--brand-grad)",
                  color: "#fff",
                  borderBottomRightRadius: "4px",
                }
              : {
                  background: "var(--surface)",
                  border: "1px solid var(--border)",
                  color: "var(--fg)",
                  borderBottomLeftRadius: "4px",
                }
          }
        >
          {msg.text || (
            msg.streaming
              ? msg.statusText
                ? <span style={{ opacity: 0.6, fontStyle: "italic", fontSize: "0.85em" }}>{msg.statusText}</span>
                : <ThinkingDots />
              : null
          )}
        </div>
        <span className="text-[10px] text-faint px-1">{formatTime(msg.ts)}</span>
      </div>
    </div>
  );
}

export default function ChatPage() {
  const { clinic } = useAuth();
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [online, setOnline] = useState<boolean | null>(null);
  const sessionId = useRef(generateSessionId());
  const bottomRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const greeted = useRef(false);
  const loadingTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  // The local model can take several seconds to load into RAM on the first call.
  // If no token/status has arrived shortly after a request starts, show a
  // "connecting / loading model" hint on the pending assistant bubble.
  function armConnectingHint() {
    clearConnectingHint();
    loadingTimer.current = setTimeout(() => {
      setMessages((prev) => {
        const next = [...prev];
        const last = next[next.length - 1];
        if (last?.role === "assistant" && last.streaming && !last.text && !last.statusText) {
          next[next.length - 1] = { ...last, statusText: "সংযোগ হচ্ছে, মডেল লোড হচ্ছে…" };
        }
        return next;
      });
    }, 2500);
  }
  function clearConnectingHint() {
    if (loadingTimer.current) {
      clearTimeout(loadingTimer.current);
      loadingTimer.current = null;
    }
  }

  async function consumeStream(res: Response) {
    if (!res.ok || !res.body) throw new Error(`HTTP ${res.status}`);

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let receivedEnd = false;

    // Client-side safety timeout: if no "end" event arrives within 120 s of
    // starting, give up and show a retry prompt so the UI never freezes.
    const CLIENT_TIMEOUT_MS = 120_000;
    const abortTimer = setTimeout(() => reader.cancel(), CLIENT_TIMEOUT_MS);

    try {
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");
        buffer = lines.pop() ?? "";

        for (const line of lines) {
          if (!line.startsWith("data: ")) continue;
          const payload = JSON.parse(line.slice(6));

          // First byte from the server means the model is up — drop the hint.
          clearConnectingHint();

          if (payload.type === "token") {
            flushSync(() => {
              setMessages((prev) => {
                const next = [...prev];
                const last = next[next.length - 1];
                if (last?.role === "assistant") {
                  next[next.length - 1] = { ...last, text: last.text + payload.text, statusText: undefined };
                }
                return next;
              });
            });
          } else if (payload.type === "status") {
            setMessages((prev) => {
              const next = [...prev];
              const last = next[next.length - 1];
              if (last?.role === "assistant" && !last.text) {
                next[next.length - 1] = { ...last, statusText: payload.text };
              }
              return next;
            });
          } else if (payload.type === "end") {
            receivedEnd = true;
            setMessages((prev) => {
              const next = [...prev];
              const last = next[next.length - 1];
              if (last?.role === "assistant") {
                next[next.length - 1] = { ...last, streaming: false };
              }
              return next;
            });
          }
        }
      }
    } finally {
      clearTimeout(abortTimer);
      // If the stream closed without an "end" event (server error or timeout),
      // finalize the last bubble so it doesn't stay frozen in streaming state.
      if (!receivedEnd) {
        setMessages((prev) => {
          const next = [...prev];
          const last = next[next.length - 1];
          if (last?.role === "assistant" && last.streaming) {
            next[next.length - 1] = {
              ...last,
              streaming: false,
              text: last.text || "দুঃখিত, উত্তর পেতে সমস্যা হয়েছে। আবার চেষ্টা করুন।",
              statusText: undefined,
            };
          }
          return next;
        });
      }
    }
  }

  async function greet() {
    setBusy(true);
    setOnline(null);
    setMessages([{ role: "assistant", text: "", streaming: true, ts: new Date() }]);
    armConnectingHint();
    try {
      const res = await fetch(`${API}/chat/stream`, {
        method: "POST",
        headers: { "Content-Type": "application/json", ...AUTH_HEADERS },
        body: JSON.stringify({ session_id: sessionId.current, message: "", clinic_slug: clinic?.slug }),
      });
      await consumeStream(res);
      setOnline(true);
    } catch {
      setMessages([
        {
          role: "assistant",
          text: "ব্যাকেন্ড সার্ভারের সাথে সংযোগ করা যাচ্ছে না। অনুগ্রহ করে সার্ভার চালু আছে কিনা নিশ্চিত করুন।",
          streaming: false,
          ts: new Date(),
        },
      ]);
      setOnline(false);
    } finally {
      clearConnectingHint();
      setBusy(false);
      inputRef.current?.focus();
    }
  }

  useEffect(() => {
    if (greeted.current) return;
    greeted.current = true;
    greet();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  async function sendMessage() {
    const text = input.trim();
    if (!text || busy) return;

    setInput("");
    setBusy(true);

    setMessages((prev) => [
      ...prev,
      { role: "user", text, ts: new Date() },
      { role: "assistant", text: "", streaming: true, ts: new Date() },
    ]);
    armConnectingHint();

    try {
      const res = await fetch(`${API}/chat/stream`, {
        method: "POST",
        headers: { "Content-Type": "application/json", ...AUTH_HEADERS },
        body: JSON.stringify({ session_id: sessionId.current, message: text, clinic_slug: clinic?.slug }),
      });
      await consumeStream(res);
    } catch {
      setMessages((prev) => {
        const next = [...prev];
        const last = next[next.length - 1];
        if (last?.role === "assistant") {
          next[next.length - 1] = {
            ...last,
            text: "কিছু একটা সমস্যা হয়েছে। আবার চেষ্টা করুন।",
            streaming: false,
          };
        }
        return next;
      });
    } finally {
      clearConnectingHint();
      setBusy(false);
      inputRef.current?.focus();
    }
  }

  function handleKey(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  }

  function resetChat() {
    sessionId.current = generateSessionId();
    greeted.current = false;
    greet();
  }

  return (
    <div className="flex h-screen w-screen flex-col" style={{ background: "var(--bg)" }}>
      <div aria-hidden className="pointer-events-none fixed inset-0 overflow-hidden">
        <div
          className="absolute -top-32 -left-32 h-96 w-96 rounded-full opacity-20 blur-3xl"
          style={{ background: "radial-gradient(circle, rgba(99,102,241,0.5) 0%, transparent 70%)" }}
        />
        <div
          className="absolute -bottom-32 -right-32 h-96 w-96 rounded-full opacity-15 blur-3xl"
          style={{ background: "radial-gradient(circle, rgba(168,85,247,0.5) 0%, transparent 70%)" }}
        />
      </div>

      <header
        className="relative z-10 flex h-16 shrink-0 items-center justify-between px-4 sm:px-6"
        style={{
          background: "color-mix(in srgb, var(--surface) 85%, transparent)",
          backdropFilter: "blur(20px)",
          WebkitBackdropFilter: "blur(20px)",
          borderBottom: "1px solid var(--border)",
        }}
      >
        <div className="flex items-center gap-3">
          <Link
            href="/"
            className="btn-ghost btn-sm"
          >
            <ArrowLeft size={16} />
          </Link>
          <div className="flex items-center gap-2.5">
            <span
              className="flex h-9 w-9 items-center justify-center rounded-xl text-white shadow"
              style={{ background: "var(--brand-grad)", boxShadow: "0 2px 12px rgba(99,102,241,0.4)" }}
            >
              <Stethoscope size={16} />
            </span>
            <div>
              <div className="text-sm font-semibold text-fg leading-tight">AI Receptionist</div>
              <div className="flex items-center gap-1.5 text-xs text-faint">
                {online === null ? (
                  <span className="badge badge-surface animate-pulse">Connecting</span>
                ) : online ? (
                  <span className="badge badge-success"><Wifi size={10} /> Online</span>
                ) : (
                  <span className="badge badge-danger"><WifiOff size={10} /> Offline</span>
                )}
              </div>
            </div>
          </div>
        </div>

        <div className="flex items-center gap-2">
          <span className="hidden text-xs text-faint sm:inline font-mono">
            {sessionId.current.slice(0, 20)}…
          </span>
          <button
            onClick={resetChat}
            disabled={busy}
            title="New conversation"
            className="btn-ghost btn-sm"
          >
            <RotateCcw size={14} />
          </button>
        </div>
      </header>

      <div className="relative z-10 flex-1 overflow-y-auto px-4 py-6 sm:px-6">
        <div className="mx-auto flex max-w-2xl flex-col gap-5">
          {messages.length === 0 && (
            <div className="flex flex-col items-center gap-4 py-16 text-center">
              <span
                className="flex h-16 w-16 items-center justify-center rounded-2xl text-white shadow-lg"
                style={{ background: "var(--brand-grad)", boxShadow: "0 8px 32px rgba(99,102,241,0.35)" }}
              >
                <Stethoscope size={28} />
              </span>
              <div>
                <div className="text-base font-semibold text-fg">বাংলা AI রিসেপশনিস্ট</div>
                <div className="mt-1 text-sm text-faint">সংযোগ স্থাপন হচ্ছে…</div>
              </div>
            </div>
          )}

          {messages.map((msg, i) => (
            <ChatBubble key={i} msg={msg} />
          ))}

          <div ref={bottomRef} />
        </div>
      </div>

      <div
        className="relative z-10 shrink-0 px-4 py-4 sm:px-6"
        style={{
          background: "color-mix(in srgb, var(--surface) 90%, transparent)",
          backdropFilter: "blur(20px)",
          WebkitBackdropFilter: "blur(20px)",
          borderTop: "1px solid var(--border)",
        }}
      >
        <div className="mx-auto flex max-w-2xl items-end gap-3">
          <div
            className="flex flex-1 items-end rounded-2xl px-4 py-2"
            style={{
              background: "var(--surface-2)",
              border: "1px solid var(--border)",
              boxShadow: busy ? "0 0 0 2px color-mix(in srgb, var(--primary) 20%, transparent)" : "none",
              transition: "box-shadow 0.2s",
            }}
          >
            <textarea
              ref={inputRef}
              rows={1}
              value={input}
              onChange={(e) => {
                setInput(e.target.value);
                e.target.style.height = "auto";
                e.target.style.height = Math.min(e.target.scrollHeight, 120) + "px";
              }}
              onKeyDown={handleKey}
              disabled={busy}
              placeholder="বাংলা বা ইংরেজিতে লিখুন… (Enter to send)"
              className="w-full resize-none bg-transparent text-sm text-fg placeholder:text-faint focus:outline-none"
              style={{ maxHeight: "120px", lineHeight: "1.5" }}
            />
          </div>

          <button
            onClick={sendMessage}
            disabled={busy || !input.trim()}
            className="btn-primary flex h-11 w-11 shrink-0 items-center justify-center rounded-2xl p-0"
          >
            <Send size={16} />
          </button>
        </div>

        <p className="mt-2 text-center text-[10px] text-faint">
          Shift+Enter for new line · Enter to send
        </p>
      </div>
    </div>
  );
}
