"use client";

import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { X, CalendarDays } from "lucide-react";
import type { Slot } from "@/lib/api";

/** Slot-picker modal for rescheduling. Caller supplies how to fetch slots and
 * how to commit the chosen one, so it works for both staff and patient flows. */
export default function RescheduleModal({
  open,
  title,
  fetchSlots,
  onConfirm,
  onClose,
  onDone,
}: {
  open: boolean;
  title: string;
  fetchSlots: () => Promise<Slot[]>;
  onConfirm: (slotDatetime: string) => Promise<void>;
  onClose: () => void;
  onDone?: () => void;
}) {
  const [slots, setSlots] = useState<Slot[] | null>(null);
  const [picked, setPicked] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  useEffect(() => {
    if (!open) return;
    setSlots(null); setPicked(null); setErr("");
    fetchSlots().then(setSlots).catch((e) => {
      setSlots([]);
      setErr(e instanceof Error ? e.message : "Could not load slots.");
    });
  }, [open, fetchSlots]);

  async function confirm() {
    if (!picked) return;
    setBusy(true);
    setErr("");
    try {
      await onConfirm(picked);
      onDone?.();
      onClose();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Could not reschedule.");
    } finally {
      setBusy(false);
    }
  }

  if (!open) return null;

  return createPortal(
    <div className="modal-overlay" onClick={(e) => { if (e.target === e.currentTarget && !busy) onClose(); }}>
      <div className="modal-panel">
        <div className="modal-header">
          <h2 className="modal-title">{title}</h2>
          <button onClick={onClose} className="modal-close" aria-label="Close" disabled={busy}><X size={16} /></button>
        </div>

        <div className="modal-body">
          {slots === null ? (
            <div className="space-y-2">
              {[0, 1, 2, 3].map((i) => <div key={i} className="skeleton h-9 w-full rounded-lg" />)}
            </div>
          ) : slots.length === 0 ? (
            <p className="py-6 text-center text-sm text-faint">No open slots available.</p>
          ) : (
            <div className="max-h-72 space-y-1.5 overflow-y-auto pr-1">
              {slots.map((s) => (
                <button
                  key={s.datetime}
                  onClick={() => setPicked(s.datetime)}
                  className={`flex w-full items-center gap-2.5 rounded-xl border px-3.5 py-2.5 text-left text-sm transition-colors ${
                    picked === s.datetime
                      ? "border-primary/40 bg-[var(--brand-soft)] text-fg"
                      : "border-border bg-surface text-muted hover:border-primary/20"
                  }`}
                >
                  <CalendarDays size={14} className={picked === s.datetime ? "text-primary" : "text-faint"} />
                  <span dir="auto">{s.label}</span>
                </button>
              ))}
            </div>
          )}
          {err && <p className="text-xs text-danger" role="alert">{err}</p>}
        </div>

        <div className="modal-footer">
          <button onClick={onClose} className="btn-ghost text-sm" disabled={busy}>Cancel</button>
          <button onClick={confirm} disabled={!picked || busy} className="btn-primary text-sm">
            {busy ? "Rescheduling…" : "Confirm new time"}
          </button>
        </div>
      </div>
    </div>,
    document.body
  );
}
