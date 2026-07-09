"use client";

import { useCallback, useEffect, useState } from "react";
import { format } from "date-fns";
import { MessageSquare, RefreshCw, ChevronDown, ChevronUp, Download, RotateCcw } from "lucide-react";
import TopBar from "@/components/layout/TopBar";
import TableSkeleton from "@/components/ui/TableSkeleton";
import { listMessages, resendMessage, type SmsLogRow } from "@/lib/api";
import { exportCsv } from "@/lib/csv";
import { useToast } from "@/lib/toast";

const KIND_LABELS: Record<string, string> = {
  confirmation: "Confirmation",
  reminder: "Reminder",
  doctor_alert: "Doctor alert",
  token: "Token call",
  agent_reply: "Agent reply",
};

function StatusBadge({ status }: { status: string }) {
  const cls =
    status === "sent" ? "badge-success" :
    status === "failed" ? "badge-danger" :
    "badge-surface";
  return <span className={`badge capitalize ${cls}`}>{status}</span>;
}

function MessageRow({ m, onResend }: { m: SmsLogRow; onResend: (m: SmsLogRow) => void }) {
  const [open, setOpen] = useState(false);
  const canResend = m.status === "failed" || m.status === "skipped";
  return (
    <>
      <tr
        className="cursor-pointer transition-colors hover:bg-surface-2"
        onClick={() => setOpen((v) => !v)}
        role="button"
        tabIndex={0}
        aria-expanded={open}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") { e.preventDefault(); setOpen((v) => !v); }
        }}
      >
        <td className="whitespace-nowrap text-xs text-faint">
          {format(new Date(m.created_at), "d MMM, h:mm a")}
        </td>
        <td>
          <span className="badge badge-info">{KIND_LABELS[m.kind] ?? m.kind}</span>
        </td>
        <td className="tabular-nums text-muted">{m.to_number}</td>
        <td><StatusBadge status={m.status} /></td>
        <td className="max-w-[28rem] truncate text-muted" dir="auto">{m.body}</td>
        <td className="text-faint">
          {open ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
        </td>
      </tr>
      {open && (
        <tr className="bg-surface-2">
          <td colSpan={6} className="px-5 py-3">
            <p className="whitespace-pre-wrap text-sm text-fg" dir="auto">{m.body}</p>
            <div className="mt-2 flex flex-wrap items-center gap-4 text-xs text-faint">
              <span>Provider: {m.provider ?? "—"}</span>
              {m.error && <span className="text-danger">Error: {m.error}</span>}
              {canResend && (
                <button onClick={() => onResend(m)} className="btn-outline btn-xs ml-auto">
                  <RotateCcw size={12} /> Resend
                </button>
              )}
            </div>
          </td>
        </tr>
      )}
    </>
  );
}

export default function MessagesPage() {
  const toast = useToast();
  const [rows, setRows] = useState<SmsLogRow[] | null>(null);
  const [error, setError] = useState("");
  const [kind, setKind] = useState("");
  const [status, setStatus] = useState("");

  async function handleResend(m: SmsLogRow) {
    try {
      await resendMessage(m.id);
      toast.success("Message re-sent");
      load();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Could not resend message");
    }
  }

  const load = useCallback(() => {
    setRows(null);
    setError("");
    listMessages({ kind: kind || undefined, status: status || undefined })
      .then(setRows)
      .catch((e) => { setError(e instanceof Error ? e.message : "Could not load messages."); setRows([]); });
  }, [kind, status]);

  useEffect(() => { load(); }, [load]);

  return (
    <>
      <TopBar title="Messages" />
      <main className="flex-1 space-y-4 overflow-y-auto bg-bg p-6">
        {/* Filters */}
        <div className="card flex flex-wrap items-end gap-3 p-4">
          <label className="flex flex-col gap-1 text-xs text-muted">
            Type
            <select className="select w-40" value={kind} onChange={(e) => setKind(e.target.value)}>
              <option value="">All types</option>
              <option value="confirmation">Confirmation</option>
              <option value="reminder">Reminder</option>
              <option value="doctor_alert">Doctor alert</option>
              <option value="token">Token call</option>
              <option value="agent_reply">Agent reply</option>
            </select>
          </label>
          <label className="flex flex-col gap-1 text-xs text-muted">
            Status
            <select className="select w-36" value={status} onChange={(e) => setStatus(e.target.value)}>
              <option value="">All statuses</option>
              <option value="sent">Sent</option>
              <option value="failed">Failed</option>
              <option value="skipped">Skipped</option>
            </select>
          </label>
          <button onClick={load} className="btn-ghost btn-sm mb-0.5">
            <RefreshCw size={13} /> Refresh
          </button>
          <button
            onClick={() =>
              rows && exportCsv(
                `messages-${format(new Date(), "yyyy-MM-dd")}`,
                rows,
                [
                  { header: "Time", value: (m) => format(new Date(m.created_at), "yyyy-MM-dd HH:mm") },
                  { header: "Type", value: (m) => m.kind },
                  { header: "Recipient", value: (m) => m.to_number },
                  { header: "Status", value: (m) => m.status },
                  { header: "Provider", value: (m) => m.provider ?? "" },
                  { header: "Message", value: (m) => m.body },
                  { header: "Error", value: (m) => m.error ?? "" },
                ]
              )
            }
            disabled={!rows || rows.length === 0}
            className="btn-outline btn-sm mb-0.5"
          >
            <Download size={13} /> Export
          </button>
          {rows && (
            <span className="mb-1.5 ml-auto text-xs text-faint">
              {rows.length} message{rows.length === 1 ? "" : "s"}
            </span>
          )}
        </div>

        {rows === null ? (
          <TableSkeleton rows={8} cols={4} />
        ) : error ? (
          <div className="card p-4 text-sm text-danger">{error}</div>
        ) : rows.length === 0 ? (
          <div className="table-wrap">
            <div className="empty-state">
              <div className="empty-icon"><MessageSquare size={22} /></div>
              <p className="empty-title">No SMS sent yet</p>
              <p className="empty-desc">
                Booking confirmations, reminders, and token calls will appear here as they are sent.
              </p>
            </div>
          </div>
        ) : (
          <div className="table-wrap">
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="table-header sticky">
                  <tr>
                    {["Time", "Type", "Recipient", "Status", "Message", ""].map((h) => (
                      <th key={h}>{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody className="table-body">
                  {rows.map((m) => <MessageRow key={m.id} m={m} onResend={handleResend} />)}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </main>
    </>
  );
}
