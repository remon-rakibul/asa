"use client";

import { useCallback, useEffect, useState } from "react";
import { formatDistanceToNow } from "date-fns";
import { MessageCircle, Phone, Send, Globe, RefreshCw, MessageSquare, ArrowLeft, AlertTriangle, Check, Sparkles } from "lucide-react";
import TopBar from "@/components/layout/TopBar";
import { useToast } from "@/lib/toast";
import {
  draftReply,
  getConversation,
  listConversations,
  listEscalations,
  replyToConversation,
  resolveEscalation,
  type ConversationMessage,
  type ConversationSummary,
  type Escalation,
} from "@/lib/api";

const CHANNEL_ICON: Record<string, React.ElementType> = {
  whatsapp: MessageCircle,
  sms: Send,
  voice: Phone,
  text: Globe,
  web: Globe,
};

function ChannelTag({ channel }: { channel: string }) {
  const Icon = CHANNEL_ICON[channel] ?? Globe;
  return (
    <span className="badge badge-surface gap-1 uppercase">
      <Icon size={11} /> {channel}
    </span>
  );
}

export default function ConversationsPage() {
  const toast = useToast();
  const [list, setList] = useState<ConversationSummary[] | null>(null);
  const [active, setActive] = useState<string | null>(null);
  const [messages, setMessages] = useState<ConversationMessage[]>([]);
  const [loadingMsgs, setLoadingMsgs] = useState(false);
  const [reply, setReply] = useState("");
  const [sending, setSending] = useState(false);
  const [escalations, setEscalations] = useState<Escalation[]>([]);

  // Outbound replies work for SMS / WhatsApp (provider send) and web portal
  // chats (message is appended to the patient's chat thread).
  const canReply =
    !!active &&
    (active.startsWith("sms-") || active.startsWith("whatsapp-") || active.startsWith("pt-acc"));

  async function markResolved(id: number) {
    try {
      await resolveEscalation(id);
      setEscalations((prev) => prev.filter((e) => e.id !== id));
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Could not resolve escalation");
    }
  }

  async function sendReply() {
    const text = reply.trim();
    if (!active || !text || sending) return;
    setSending(true);
    try {
      await replyToConversation(active, text);
      setReply("");
      setMessages(await getConversation(active));
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Could not send reply");
    } finally {
      setSending(false);
    }
  }

  const [drafting, setDrafting] = useState(false);

  // AI-composed suggested reply — fills the box for the staff member to edit;
  // never auto-sends. Local CPU inference, so this can take a little while.
  async function draftWithAI() {
    if (!active || drafting || sending) return;
    setDrafting(true);
    try {
      const { draft } = await draftReply(active);
      setReply(draft);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Could not draft a reply");
    } finally {
      setDrafting(false);
    }
  }

  const load = useCallback(async () => {
    try {
      const rows = await listConversations();
      setList(rows);
      setActive((cur) => cur ?? rows[0]?.session_id ?? null);
    } catch {
      setList([]);
    }
    try {
      setEscalations(await listEscalations("open"));
    } catch {
      setEscalations([]);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    if (!active) return;
    setLoadingMsgs(true);
    getConversation(active)
      .then(setMessages)
      .catch(() => setMessages([]))
      .finally(() => setLoadingMsgs(false));
  }, [active]);

  return (
    <>
      <TopBar title="Conversations" />
      <main className="flex flex-1 overflow-hidden bg-bg">
        <div
          className={`w-full shrink-0 flex-col border-r border-border lg:flex lg:w-80 ${
            active ? "hidden lg:flex" : "flex"
          }`}
        >
          <div className="flex items-center justify-between border-b border-border px-4 py-3">
            <span className="text-sm font-semibold text-fg">Recent</span>
            <button
              onClick={load}
              className="btn-ghost btn-sm"
              title="Refresh"
            >
              <RefreshCw size={14} />
            </button>
          </div>
          {/* Needs-attention queue: conversations the agent escalated */}
          {escalations.length > 0 && (
            <div className="border-b border-border bg-[var(--warning-bg,rgba(234,179,8,0.08))] px-3 py-2.5">
              <div className="mb-1.5 flex items-center gap-1.5 text-xs font-semibold text-fg">
                <AlertTriangle size={13} className="text-amber-500" />
                Needs attention ({escalations.length})
              </div>
              <div className="flex flex-col gap-1.5">
                {escalations.map((e) => (
                  <div
                    key={e.id}
                    className="flex items-center gap-2 rounded-lg border border-border bg-surface px-2.5 py-2"
                  >
                    <button
                      onClick={() => setActive(e.session_id)}
                      className="min-w-0 flex-1 text-left"
                      title={e.session_id}
                    >
                      <p className="truncate text-xs text-fg" dir="auto">
                        {e.reason || "Patient asked for a human"}
                      </p>
                      <p className="text-[10px] text-faint">
                        {e.channel} · {formatDistanceToNow(new Date(e.created_at), { addSuffix: true })}
                      </p>
                    </button>
                    <button
                      onClick={() => markResolved(e.id)}
                      className="btn-ghost btn-sm shrink-0 px-1.5"
                      title="Mark resolved"
                      aria-label="Mark resolved"
                    >
                      <Check size={14} />
                    </button>
                  </div>
                ))}
              </div>
            </div>
          )}
          <div className="flex-1 overflow-y-auto">
            {list === null ? (
              <div className="flex flex-col gap-3 p-4">
                {Array.from({ length: 6 }).map((_, i) => (
                  <div key={i} className="flex flex-col gap-2">
                    <div className="skeleton h-4 w-20" />
                    <div className="skeleton h-3 w-full" />
                    <div className="skeleton h-3 w-16" />
                  </div>
                ))}
              </div>
            ) : list.length === 0 ? (
              <div className="empty-state">
                <div className="empty-icon">
                  <MessageSquare size={22} />
                </div>
                <p className="empty-title">No conversations yet</p>
                <p className="empty-desc">
                  They appear here as patients chat or call through any channel.
                </p>
              </div>
            ) : (
              <div className="flex flex-col gap-1 p-2">
                {list.map((c) => (
                  <button
                    key={c.session_id}
                    onClick={() => setActive(c.session_id)}
                    className={`flex w-full flex-col gap-1.5 rounded-xl border px-3.5 py-3 text-left transition-all duration-200 ${
                      active === c.session_id
                        ? "border-primary/30 bg-[var(--brand-soft)] shadow-sm"
                        : "border-border bg-surface hover:border-primary/20 hover:shadow-sm"
                    }`}
                  >
                    <div className="flex items-center justify-between gap-2">
                      <ChannelTag channel={c.channel} />
                      <span className="text-[11px] text-faint">
                        {formatDistanceToNow(new Date(c.last_at), { addSuffix: true })}
                      </span>
                    </div>
                    <p className="line-clamp-2 text-sm leading-relaxed text-muted" dir="auto">
                      {c.last_text}
                    </p>
                    <span className="text-[11px] text-faint">{c.turns} turns</span>
                  </button>
                ))}
              </div>
            )}
          </div>
        </div>

        <div className={`flex-1 flex-col ${active ? "flex" : "hidden lg:flex"}`}>
          {/* Mobile back bar */}
          {active && (
            <div className="flex items-center gap-2 border-b border-border bg-surface/80 px-4 py-2.5 backdrop-blur lg:hidden">
              <button
                onClick={() => setActive(null)}
                aria-label="Back to conversations"
                className="btn-ghost btn-sm"
              >
                <ArrowLeft size={16} />
                Back
              </button>
            </div>
          )}
          <div className="flex-1 overflow-y-auto">
          {!active ? (
            <div className="empty-state">
              <div className="empty-icon">
                <MessageSquare size={22} />
              </div>
              <p className="empty-title">Select a conversation</p>
              <p className="empty-desc">Choose a conversation from the sidebar to view its transcript.</p>
            </div>
          ) : loadingMsgs ? (
            <div className="mx-auto flex max-w-2xl flex-col gap-4 p-6">
              {Array.from({ length: 4 }).map((_, i) => (
                <div key={i} className={`flex ${i % 2 === 0 ? "justify-end" : "justify-start"}`}>
                  <div className={`flex flex-col gap-2 ${i % 2 === 0 ? "items-end" : "items-start"}`}>
                    <div className="skeleton h-8 w-48 rounded-2xl" />
                    <div className="skeleton h-3 w-16" />
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <div className="mx-auto flex max-w-2xl flex-col gap-4 p-6">
              {messages.map((m, i) => (
                <div
                  key={i}
                  className={`flex animate-fade-in ${m.role === "user" ? "justify-end" : "justify-start"}`}
                  style={{ animationDelay: `${i * 40}ms` }}
                >
                  <div
                    className={`max-w-[78%] rounded-2xl px-4 py-2.5 text-sm leading-relaxed ${
                      m.role === "user"
                        ? "bg-[var(--brand-grad)] text-white shadow-md"
                        : "border border-border bg-surface text-fg shadow-sm"
                    }`}
                    dir="auto"
                  >
                    {m.text}
                  </div>
                </div>
              ))}
            </div>
          )}
          </div>

          {/* Composer — staff take-over (SMS / WhatsApp only) */}
          {active && (
            <div className="shrink-0 border-t border-border bg-surface px-4 py-3">
              {canReply ? (
                <div className="mx-auto flex max-w-2xl items-end gap-2">
                  <button
                    onClick={draftWithAI}
                    disabled={drafting || sending}
                    title="AI দিয়ে খসড়া লিখুন"
                    aria-label="Draft reply with AI"
                    className="btn-ghost flex h-11 w-11 shrink-0 items-center justify-center rounded-2xl p-0 text-primary disabled:opacity-40"
                  >
                    {drafting ? (
                      <span className="h-4 w-4 animate-spin rounded-full border-2 border-primary border-t-transparent" />
                    ) : (
                      <Sparkles size={16} />
                    )}
                  </button>
                  <input
                    value={reply}
                    onChange={(e) => setReply(e.target.value)}
                    onKeyDown={(e) => { if (e.key === "Enter") sendReply(); }}
                    placeholder={drafting ? "AI খসড়া লিখছে…" : "Type a reply…"}
                    disabled={sending}
                    className="input flex-1"
                    dir="auto"
                  />
                  <button
                    onClick={sendReply}
                    disabled={sending || !reply.trim()}
                    aria-label="Send reply"
                    className="btn-primary flex h-11 w-11 shrink-0 items-center justify-center rounded-2xl p-0 disabled:opacity-40"
                  >
                    {sending ? (
                      <span className="h-4 w-4 animate-spin rounded-full border-2 border-white border-t-transparent" />
                    ) : (
                      <Send size={15} />
                    )}
                  </button>
                </div>
              ) : (
                <p className="mx-auto max-w-2xl text-center text-xs text-faint">
                  Replies aren&apos;t available for this channel — SMS, WhatsApp, and portal chats can be answered here.
                </p>
              )}
            </div>
          )}
        </div>
      </main>
    </>
  );
}
