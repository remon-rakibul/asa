"use client";

import { useCallback, useEffect, useState } from "react";
import { format } from "date-fns";
import TopBar from "@/components/layout/TopBar";
import { getAuditActions, getAuditLog, type AuditEntry, type AuditFilters } from "@/lib/api";
import { exportCsv } from "@/lib/csv";
import { ShieldCheck, ClipboardList, ChevronDown, ChevronUp, Download, X } from "lucide-react";

const ENTITY_TYPES = ["", "appointment", "clinic", "patient", "schedule", "user", "channel", "token", "doctor", "hospital"];

const ENTITY_BADGE: Record<string, string> = {
  user: "badge-primary",
  appointment: "badge-info",
  clinic: "badge-success",
  schedule: "badge-warning",
  patient: "badge-success",
  channel: "badge-info",
};

function EntityBadge({ type }: { type: string }) {
  const cls = ENTITY_BADGE[type] ?? "badge-surface";
  return <span className={`badge ${cls}`}>{type}</span>;
}

function ActorCell({ e }: { e: AuditEntry }) {
  return (
    <div className="flex flex-col gap-0.5">
      {e.actor_email ? (
        <span className="text-xs font-medium text-fg">{e.actor_email}</span>
      ) : null}
      <span className={`badge ${e.actor_role ? "badge-primary" : "badge-surface"} w-fit`}>
        {e.actor_role || "system"}
      </span>
    </div>
  );
}

function AuditRow({ e }: { e: AuditEntry }) {
  const [expanded, setExpanded] = useState(false);
  const hasPayload = e.old_value || e.new_value;

  return (
    <>
      <tr
        className={`transition-colors ${hasPayload ? "cursor-pointer hover:bg-surface-2" : ""}`}
        onClick={() => hasPayload && setExpanded((x) => !x)}
        {...(hasPayload && {
          role: "button",
          tabIndex: 0,
          "aria-expanded": expanded,
          onKeyDown: (ev: React.KeyboardEvent) => {
            if (ev.key === "Enter" || ev.key === " ") {
              ev.preventDefault();
              setExpanded((x) => !x);
            }
          },
        })}
      >
        <td className="whitespace-nowrap text-xs text-faint">
          {new Date(e.created_at).toLocaleString()}
        </td>
        <td>
          <ActorCell e={e} />
        </td>
        <td className="text-sm font-medium text-fg">{e.action}</td>
        <td>
          <EntityBadge type={e.entity_type} />
        </td>
        <td className="max-w-[120px] truncate font-mono text-xs text-primary">{e.entity_id ?? "—"}</td>
        <td className="text-xs text-faint">{e.ip_address ?? "—"}</td>
        <td className="text-xs text-faint">
          {hasPayload && (
            <span className="inline-flex items-center gap-1 text-primary">
              {expanded ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
              {expanded ? "hide" : "show"}
            </span>
          )}
        </td>
      </tr>
      {expanded && hasPayload && (
        <tr className="animate-fade-in border-t border-border bg-surface-2">
          <td colSpan={7} className="px-6 py-4">
            <div className="grid gap-6 sm:grid-cols-2">
              {e.old_value && (
                <div>
                  <div className="mb-1.5 text-[11px] font-semibold uppercase tracking-wider text-faint">Before</div>
                  <pre className="overflow-x-auto rounded-xl border border-border bg-surface p-3 text-xs leading-relaxed text-muted shadow-sm">
                    {JSON.stringify(e.old_value, null, 2)}
                  </pre>
                </div>
              )}
              {e.new_value && (
                <div>
                  <div className="mb-1.5 text-[11px] font-semibold uppercase tracking-wider text-faint">After</div>
                  <pre className="overflow-x-auto rounded-xl border border-border bg-surface p-3 text-xs leading-relaxed text-muted shadow-sm">
                    {JSON.stringify(e.new_value, null, 2)}
                  </pre>
                </div>
              )}
            </div>
          </td>
        </tr>
      )}
    </>
  );
}

export default function AuditPage() {
  const [entries, setEntries] = useState<AuditEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [entityType, setEntityType] = useState("");
  const [action, setAction] = useState("");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [actions, setActions] = useState<string[]>([]);
  const [err, setErr] = useState("");

  const load = useCallback(async (filters: AuditFilters) => {
    setLoading(true);
    setErr("");
    try {
      setEntries(await getAuditLog(filters));
    } catch {
      setErr("You don't have access to the audit log. Contact your administrator if you think this is a mistake.");
    } finally {
      setLoading(false);
    }
  }, []);

  // Re-query whenever any filter changes.
  useEffect(() => {
    load({
      entity_type: entityType || undefined,
      action: action || undefined,
      date_from: dateFrom || undefined,
      date_to: dateTo || undefined,
    });
  }, [load, entityType, action, dateFrom, dateTo]);

  // Populate the action dropdown once.
  useEffect(() => {
    getAuditActions().then((a) => setActions(a.actions)).catch(() => {});
  }, []);

  const hasFilters = Boolean(entityType || action || dateFrom || dateTo);
  function clearAll() {
    setEntityType("");
    setAction("");
    setDateFrom("");
    setDateTo("");
  }

  return (
    <>
      <TopBar title="Audit Log" />
      <main className="flex-1 overflow-y-auto bg-bg p-6">
        <div className="mx-auto max-w-6xl space-y-4">
          <div className="flex items-center gap-3">
            <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-[var(--brand-soft)]">
              <ShieldCheck size={18} className="text-primary" />
            </div>
            <div>
              <h2 className="text-sm font-semibold text-fg">Audit Log</h2>
              <p className="text-xs text-muted">Immutable record of all admin actions.</p>
            </div>
            <div className="ml-auto">
              <button
                onClick={() =>
                  exportCsv(
                    `audit-${format(new Date(), "yyyy-MM-dd")}`,
                    entries,
                    [
                      { header: "Time", value: (e) => format(new Date(e.created_at), "yyyy-MM-dd HH:mm:ss") },
                      { header: "Actor", value: (e) => e.actor_email ?? "" },
                      { header: "Actor role", value: (e) => e.actor_role },
                      { header: "Action", value: (e) => e.action },
                      { header: "Entity", value: (e) => e.entity_type },
                      { header: "Entity ID", value: (e) => e.entity_id ?? "" },
                      { header: "IP", value: (e) => e.ip_address ?? "" },
                    ]
                  )
                }
                disabled={loading || entries.length === 0}
                className="btn-outline btn-sm"
              >
                <Download size={13} /> Export
              </button>
            </div>
          </div>

          {/* Filters */}
          <div className="card flex flex-wrap items-end gap-3 p-4">
            <label className="flex flex-col gap-1.5">
              <span className="text-xs font-medium text-muted">Type</span>
              <select className="select w-36 text-sm" value={entityType} onChange={(e) => setEntityType(e.target.value)}>
                {ENTITY_TYPES.map((t) => (
                  <option key={t} value={t}>{t || "All types"}</option>
                ))}
              </select>
            </label>
            <label className="flex flex-col gap-1.5">
              <span className="text-xs font-medium text-muted">Action</span>
              <select className="select w-44 text-sm" value={action} onChange={(e) => setAction(e.target.value)}>
                <option value="">All actions</option>
                {actions.map((a) => (
                  <option key={a} value={a}>{a}</option>
                ))}
              </select>
            </label>
            <label className="flex flex-col gap-1.5">
              <span className="text-xs font-medium text-muted">From</span>
              <input type="date" className="input w-40 text-sm" value={dateFrom} onChange={(e) => setDateFrom(e.target.value)} />
            </label>
            <label className="flex flex-col gap-1.5">
              <span className="text-xs font-medium text-muted">To</span>
              <input type="date" className="input w-40 text-sm" value={dateTo} onChange={(e) => setDateTo(e.target.value)} />
            </label>
            {hasFilters && (
              <button onClick={clearAll} className="btn-ghost btn-sm mb-0.5">
                <X size={13} /> Clear
              </button>
            )}
          </div>

          {err && (
            <div className="rounded-2xl border border-danger/30 bg-[var(--danger-bg)] p-4 text-sm text-danger">
              {err}
            </div>
          )}

          {loading ? (
            <div className="table-wrap">
              <div className="flex flex-col gap-3 p-5">
                {Array.from({ length: 5 }).map((_, i) => (
                  <div key={i} className="flex gap-4">
                    <div className="skeleton h-4 w-28" />
                    <div className="skeleton h-4 w-16" />
                    <div className="skeleton h-4 w-24" />
                    <div className="skeleton h-4 w-20" />
                    <div className="skeleton h-4 w-32" />
                    <div className="skeleton h-4 w-28" />
                    <div className="skeleton h-4 w-12" />
                  </div>
                ))}
              </div>
            </div>
          ) : entries.length === 0 ? (
            <div className="empty-state">
              <div className="empty-icon">
                <ClipboardList size={22} />
              </div>
              <p className="empty-title">No audit entries</p>
              <p className="empty-desc">No entries match your filter. Try clearing the filter or check back later.</p>
            </div>
          ) : (
            <div className="table-wrap">
              <table className="w-full text-left">
                <thead className="table-header sticky">
                  <tr>
                    <th>Time</th>
                    <th>Actor</th>
                    <th>Action</th>
                    <th>Type</th>
                    <th>ID</th>
                    <th>IP</th>
                    <th>Payload</th>
                  </tr>
                </thead>
                <tbody className="table-body">
                  {entries.map((e) => (
                    <AuditRow key={e.id} e={e} />
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </main>
    </>
  );
}
