"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useRouter } from "next/navigation";
import {
  LayoutDashboard, CalendarDays, Clock, Users, Ticket, MessagesSquare,
  Send, Plug, Settings, Building2, ShieldCheck, BarChart3, Search, CornerDownLeft, User,
} from "lucide-react";
import { listPatients, listAppointments, type Patient } from "@/lib/api";
import type { Appointment } from "@/types";

type Item = {
  id: string;
  label: string;
  sub?: string;
  icon: React.ElementType;
  run: () => void;
  group: string;
};

const NAV: { label: string; href: string; icon: React.ElementType }[] = [
  { label: "Dashboard", href: "/", icon: LayoutDashboard },
  { label: "Appointments", href: "/appointments", icon: CalendarDays },
  { label: "Schedule", href: "/schedule", icon: Clock },
  { label: "Patients", href: "/patients", icon: Users },
  { label: "Queue", href: "/queue", icon: Ticket },
  { label: "Conversations", href: "/conversations", icon: MessagesSquare },
  { label: "Messages", href: "/messages", icon: Send },
  { label: "Reports", href: "/reports", icon: BarChart3 },
  { label: "Hospitals", href: "/hospitals", icon: Building2 },
  { label: "Audit Log", href: "/audit", icon: ShieldCheck },
  { label: "Integrations", href: "/integrations", icon: Plug },
  { label: "Settings", href: "/settings", icon: Settings },
];

export default function CommandPalette({ open, onClose }: { open: boolean; onClose: () => void }) {
  const router = useRouter();
  const [q, setQ] = useState("");
  const [active, setActive] = useState(0);
  const [patients, setPatients] = useState<Patient[]>([]);
  const [appts, setAppts] = useState<Appointment[]>([]);
  const inputRef = useRef<HTMLInputElement>(null);

  // Reset on open.
  useEffect(() => {
    if (open) {
      setQ("");
      setActive(0);
      setPatients([]);
      setAppts([]);
      setTimeout(() => inputRef.current?.focus(), 0);
    }
  }, [open]);

  // Debounced live search.
  useEffect(() => {
    if (!open || q.trim().length < 2) {
      setPatients([]); setAppts([]);
      return;
    }
    const term = q.trim();
    const t = setTimeout(async () => {
      const [p, a] = await Promise.all([
        listPatients(term).catch(() => []),
        listAppointments({ q: term }).catch(() => []),
      ]);
      setPatients(p.slice(0, 5));
      setAppts(a.slice(0, 5));
    }, 300);
    return () => clearTimeout(t);
  }, [q, open]);

  const items: Item[] = useMemo(() => {
    const term = q.trim().toLowerCase();
    const nav: Item[] = NAV.filter((n) => !term || n.label.toLowerCase().includes(term)).map((n) => ({
      id: `nav:${n.href}`,
      label: n.label,
      icon: n.icon,
      group: "Go to",
      run: () => { router.push(n.href); onClose(); },
    }));
    const pat: Item[] = patients.map((p) => ({
      id: `pat:${p.id}`,
      label: p.name || p.mrn,
      sub: `${p.mrn} · ${p.phone || "no phone"}`,
      icon: User,
      group: "Patients",
      run: () => { router.push(`/patients?q=${encodeURIComponent(p.name || p.mrn)}`); onClose(); },
    }));
    const ap: Item[] = appts.map((a) => ({
      id: `appt:${a.id}`,
      label: a.patient_name,
      sub: `${a.patient_mobile} · ${a.status}`,
      icon: CalendarDays,
      group: "Appointments",
      run: () => { router.push(`/appointments?q=${encodeURIComponent(a.patient_mobile || a.patient_name)}`); onClose(); },
    }));
    return [...nav, ...pat, ...ap];
  }, [q, patients, appts, router, onClose]);

  useEffect(() => { setActive(0); }, [items.length]);

  useEffect(() => {
    if (!open) return;
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") { e.preventDefault(); onClose(); }
      else if (e.key === "ArrowDown") { e.preventDefault(); setActive((i) => Math.min(items.length - 1, i + 1)); }
      else if (e.key === "ArrowUp") { e.preventDefault(); setActive((i) => Math.max(0, i - 1)); }
      else if (e.key === "Enter") { e.preventDefault(); items[active]?.run(); }
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, items, active, onClose]);

  if (!open) return null;

  // Render with group separators.
  let lastGroup = "";

  return createPortal(
    <div
      className="fixed inset-0 z-[60] flex items-start justify-center px-4 pt-[12vh]"
      style={{ background: "color-mix(in srgb, #000 50%, transparent)", backdropFilter: "blur(4px)" }}
      onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}
    >
      <div className="glass-strong w-full max-w-xl overflow-hidden rounded-2xl border border-border shadow-pop"
        style={{ boxShadow: "var(--shadow-pop)", animation: "slideUp 0.25s cubic-bezier(.22,.68,0,1.2) both" }}>
        <div className="flex items-center gap-2 border-b border-border px-4">
          <Search size={16} className="text-faint" />
          <input
            ref={inputRef}
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Search pages, patients, appointments…"
            className="w-full bg-transparent py-3.5 text-sm text-fg outline-none placeholder:text-faint"
          />
          <kbd className="hidden sm:inline">Esc</kbd>
        </div>
        <div className="max-h-[50vh] overflow-y-auto py-1">
          {items.length === 0 ? (
            <p className="px-4 py-6 text-center text-sm text-faint">No results</p>
          ) : (
            items.map((it, idx) => {
              const showGroup = it.group !== lastGroup;
              lastGroup = it.group;
              const Icon = it.icon;
              return (
                <div key={it.id}>
                  {showGroup && (
                    <div className="px-4 pb-1 pt-2 text-[10px] font-semibold uppercase tracking-wider text-faint">
                      {it.group}
                    </div>
                  )}
                  <button
                    onMouseEnter={() => setActive(idx)}
                    onClick={() => it.run()}
                    className={`flex w-full items-center gap-3 px-4 py-2 text-left text-sm transition-colors ${
                      idx === active ? "bg-[var(--brand-soft)] text-fg" : "text-muted hover:bg-surface-2"
                    }`}
                  >
                    <Icon size={15} className={idx === active ? "text-primary" : "text-faint"} />
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-fg">{it.label}</span>
                      {it.sub && <span className="block truncate text-xs text-faint">{it.sub}</span>}
                    </span>
                    {idx === active && <CornerDownLeft size={13} className="text-faint" />}
                  </button>
                </div>
              );
            })
          )}
        </div>
      </div>
    </div>,
    document.body
  );
}
