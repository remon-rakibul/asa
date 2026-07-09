"use client";

import { useCallback, useEffect, useState } from "react";
import {
  MessageCircle,
  Phone,
  Send,
  Cpu,
  Volume2,
  Mic,
  Plus,
  Trash2,
  CheckCircle2,
  AlertCircle,
} from "lucide-react";
import TopBar from "@/components/layout/TopBar";
import { useToast } from "@/lib/toast";
import {
  addChannel,
  deleteChannel,
  getIntegrations,
  listChannels,
  type Channel,
  type IntegrationItem,
} from "@/lib/api";

const ICONS: Record<string, React.ElementType> = {
  whatsapp: MessageCircle,
  sms: Send,
  voice: Phone,
  llm: Cpu,
  tts: Volume2,
  stt: Mic,
};

const KIND_LABELS: Record<Channel["kind"], string> = {
  whatsapp: "WhatsApp number",
  sms: "SMS number",
  voice: "Voice DID / SIP",
  voice_sip: "Voice SIP DID",
  phone: "Phone number",
  web: "Web chat key",
  voice_ivr: "Hospital IVR (hospital-level only)",
};

export default function IntegrationsPage() {
  const toast = useToast();
  const [items, setItems] = useState<IntegrationItem[] | null>(null);
  const [channels, setChannels] = useState<Channel[]>([]);
  const [kind, setKind] = useState<Channel["kind"]>("whatsapp");
  const [identifier, setIdentifier] = useState("");
  const [label, setLabel] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [status, ch] = await Promise.all([getIntegrations(), listChannels()]);
      setItems(status.items);
      setChannels(ch);
    } catch {
      setItems([]);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  async function add(e: React.FormEvent) {
    e.preventDefault();
    if (!identifier.trim()) return;
    setBusy(true);
    setError(null);
    try {
      await addChannel({ kind, identifier: identifier.trim(), label: label.trim() || undefined });
      setIdentifier("");
      setLabel("");
      await load();
      toast.success("Channel added");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not add channel");
    } finally {
      setBusy(false);
    }
  }

  async function remove(id: number) {
    try {
      await deleteChannel(id);
      await load();
      toast.success("Channel removed");
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Could not remove channel");
    }
  }

  return (
    <>
      <TopBar title="Integrations" />
      <main className="flex-1 space-y-8 overflow-y-auto bg-bg p-6">
        {/* Capability status */}
        <section className="space-y-3">
          <h2 className="text-sm font-semibold uppercase tracking-wide text-faint">
            Platform status
          </h2>
          {items === null ? (
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
              {[1, 2, 3].map((i) => (
                <div key={i} className="skeleton-card rounded-2xl" />
              ))}
            </div>
          ) : (
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3 animate-fade-in">
              {items.map((it) => {
                const Icon = ICONS[it.key] ?? Cpu;
                return (
                  <div key={it.key} className="card card-lift flex items-start gap-3 p-4">
                    <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-brand-soft text-primary">
                      <Icon size={18} />
                    </span>
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2">
                        <span className="font-medium text-fg">{it.name}</span>
                        {it.configured ? (
                          <CheckCircle2 size={15} className="text-success" />
                        ) : (
                          <AlertCircle size={15} className="text-warning" />
                        )}
                      </div>
                      <p className="mt-0.5 truncate text-xs text-muted" title={it.detail}>
                        {it.detail}
                      </p>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
          <p className="text-xs text-faint">
            Status is read-only &mdash; credentials live in the server environment. Map
            the numbers patients reach you on to this clinic below.
          </p>
        </section>

        {/* Channel routing */}
        <section className="space-y-3">
          <h2 className="text-sm font-semibold uppercase tracking-wide text-faint">
            Channel routing
          </h2>

          <form onSubmit={add} className="card flex flex-wrap items-end gap-3 p-4">
            <label className="flex flex-col gap-1 text-xs text-muted">
              Type
              <select
                className="select"
                value={kind}
                onChange={(e) => setKind(e.target.value as Channel["kind"])}
              >
                <option value="whatsapp">WhatsApp</option>
                <option value="sms">SMS</option>
                <option value="voice">Voice (SIP/DID)</option>
                <option value="phone">Phone</option>
                <option value="web">Web</option>
              </select>
            </label>
            <label className="flex flex-1 flex-col gap-1 text-xs text-muted">
              {KIND_LABELS[kind]}
              <input
                className="input"
                placeholder="e.g. 8801XXXXXXXXX"
                value={identifier}
                onChange={(e) => setIdentifier(e.target.value)}
              />
            </label>
            <label className="flex flex-1 flex-col gap-1 text-xs text-muted">
              Label (optional)
              <input
                className="input"
                placeholder="Main reception line"
                value={label}
                onChange={(e) => setLabel(e.target.value)}
              />
            </label>
            <button type="submit" disabled={busy} className="btn-primary">
              <Plus size={16} /> Add
            </button>
          </form>
          {error && <p className="text-sm text-danger">{error}</p>}

          {channels.length === 0 ? (
            <div className="empty-state py-12">
              <div className="empty-icon">
                <Phone size={22} />
              </div>
              <p className="empty-title">No channels mapped</p>
              <p className="empty-desc">Add a number above to route it to this clinic for patient communication.</p>
            </div>
          ) : (
            <div className="table-wrap">
              <table className="w-full text-sm">
                <thead className="table-header sticky">
                  <tr>
                    <th>Type</th>
                    <th>Identifier</th>
                    <th>Label</th>
                    <th className="w-12"></th>
                  </tr>
                </thead>
                <tbody className="table-body">
                  {channels.map((c) => (
                    <tr key={c.id}>
                      <td>
                        <span className="badge badge-info uppercase">{c.kind}</span>
                      </td>
                      <td className="font-medium tabular-nums text-fg">
                        {c.identifier}
                      </td>
                      <td className="text-muted">{c.label ?? "\u2014"}</td>
                      <td className="text-right">
                        <button
                          onClick={() => remove(c.id)}
                          className="rounded-lg p-1.5 text-muted hover:bg-danger/10 hover:text-danger transition-colors"
                          title="Remove"
                        >
                          <Trash2 size={15} />
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      </main>
    </>
  );
}
