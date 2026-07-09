"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import TopBar from "@/components/layout/TopBar";
import { listPatients, listHospitals, registerPatient, type Patient, type Hospital } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { Search, UserPlus, X, User, Building2, Phone, Hash, Cake, VenetianMask, CalendarDays } from "lucide-react";
import Drawer from "@/components/ui/Drawer";

function GenderBadge({ gender }: { gender: string | null }) {
  if (!gender) return <span className="text-xs text-faint">—</span>;
  const colors: Record<string, { bg: string; color: string }> = {
    male: { bg: "rgba(59,130,246,0.1)", color: "#3b82f6" },
    female: { bg: "rgba(236,72,153,0.1)", color: "#ec4899" },
    other: { bg: "var(--brand-soft)", color: "var(--primary)" },
  };
  const c = colors[gender] ?? { bg: "var(--surface-2)", color: "var(--muted)" };
  return (
    <span className="badge" style={{ background: c.bg, color: c.color }}>
      {gender}
    </span>
  );
}

function PatientRow({ p, onSelect }: { p: Patient; onSelect: (p: Patient) => void }) {
  return (
    <tr
      className="animate-fade-in cursor-pointer"
      onClick={() => onSelect(p)}
      role="button"
      tabIndex={0}
      onKeyDown={(e) => { if (e.key === "Enter") onSelect(p); }}
    >
      <td className="font-mono text-xs text-primary font-semibold">{p.mrn}</td>
      <td className="text-sm text-fg font-medium">{p.name}</td>
      <td className="text-sm text-muted tabular-nums">{p.phone}</td>
      <td className="text-sm text-muted">{p.age ?? <span className="text-faint">—</span>}</td>
      <td className="text-sm"><GenderBadge gender={p.gender} /></td>
      <td className="text-xs text-faint">{new Date(p.created_at).toLocaleDateString()}</td>
    </tr>
  );
}

function RegisterModal({
  onClose, onSaved, hospitalId,
}: { onClose: () => void; onSaved: () => void; hospitalId?: number }) {
  const [form, setForm] = useState({ name: "", phone: "", age: "", gender: "" });
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState("");

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setSaving(true);
    setErr("");
    try {
      await registerPatient({
        name: form.name,
        phone: form.phone,
        age: form.age ? parseInt(form.age) : undefined,
        gender: (form.gender || undefined) as "male" | "female" | "other" | undefined,
      }, hospitalId);
      onSaved();
      onClose();
    } catch (ex: unknown) {
      setErr(ex instanceof Error ? ex.message : "Failed to register");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="modal-overlay" onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="modal-panel">
        <div className="modal-header">
          <div className="flex items-center gap-3">
            <span className="flex h-10 w-10 items-center justify-center rounded-xl" style={{ background: "var(--brand-soft)", color: "var(--primary)" }}>
              <UserPlus size={20} />
            </span>
            <h2 className="modal-title">Register Patient</h2>
          </div>
          <button className="modal-close" onClick={onClose}><X size={16} /></button>
        </div>
        <form onSubmit={submit} className="modal-body">
          <div>
            <label className="text-xs font-medium text-muted">Full Name *</label>
            <input className="input mt-1" required value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })} />
          </div>
          <div>
            <label className="text-xs font-medium text-muted">Phone *</label>
            <input className="input mt-1" required value={form.phone}
              onChange={(e) => setForm({ ...form, phone: e.target.value })} />
          </div>
          <div className="flex gap-3">
            <div className="flex-1">
              <label className="text-xs font-medium text-muted">Age</label>
              <input type="number" className="input mt-1" min={0} max={150}
                value={form.age} onChange={(e) => setForm({ ...form, age: e.target.value })} />
            </div>
            <div className="flex-1">
              <label className="text-xs font-medium text-muted">Gender</label>
              <select className="select mt-1" value={form.gender}
                onChange={(e) => setForm({ ...form, gender: e.target.value })}>
                <option value="">Select</option>
                <option value="male">Male</option>
                <option value="female">Female</option>
                <option value="other">Other</option>
              </select>
            </div>
          </div>
          {err && <p className="text-xs text-danger">{err}</p>}
          <div className="modal-footer">
            <button type="button" onClick={onClose} className="btn-ghost text-sm">Cancel</button>
            <button type="submit" disabled={saving} className="btn-primary text-sm">
              {saving ? "Saving…" : "Register"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

export default function PatientsPage() {
  const { clinic } = useAuth();
  const isPlatformAdmin = clinic?.role === "platform_admin";

  const [hospitals, setHospitals] = useState<Hospital[]>([]);
  const [selectedHospitalId, setSelectedHospitalId] = useState<number | undefined>();
  const [patients, setPatients] = useState<Patient[]>([]);
  const [loading, setLoading] = useState(false);
  const [showModal, setShowModal] = useState(false);
  const [selected, setSelected] = useState<Patient | null>(null);
  const [err, setErr] = useState("");
  const debounce = useRef<ReturnType<typeof setTimeout> | null>(null);
  // Seed the search from ?q= so the command palette can deep-link here.
  const [q, setQ] = useState(() =>
    typeof window === "undefined"
      ? ""
      : new URLSearchParams(window.location.search).get("q") ?? ""
  );

  // Load hospital list for platform_admin
  useEffect(() => {
    if (!isPlatformAdmin) return;
    listHospitals()
      .then((h) => {
        setHospitals(h);
        if (h.length === 1) setSelectedHospitalId(h[0].id);
      })
      .catch(() => {});
  }, [isPlatformAdmin]);

  const load = useCallback(async (search?: string, hid?: number) => {
    const hospitalId = isPlatformAdmin ? (hid ?? selectedHospitalId) : undefined;
    if (isPlatformAdmin && !hospitalId) return; // waiting for selection
    setLoading(true);
    setErr("");
    try {
      setPatients(await listPatients(search, hospitalId));
    } catch {
      setErr("Could not load patients.");
    } finally {
      setLoading(false);
    }
  }, [isPlatformAdmin, selectedHospitalId]);

  // Auto-load for non-platform_admin, or when hospital is selected
  useEffect(() => {
    if (isPlatformAdmin && !selectedHospitalId) return;
    load(q || undefined);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [load, isPlatformAdmin, selectedHospitalId]);

  function handleSearch(val: string) {
    setQ(val);
    if (debounce.current) clearTimeout(debounce.current);
    debounce.current = setTimeout(() => load(val || undefined), 350);
  }

  function handleHospitalChange(id: number) {
    setSelectedHospitalId(id);
    setQ("");
    setPatients([]);
  }

  return (
    <>
      <TopBar title="Patient Registry" />
      <main className="flex-1 overflow-y-auto bg-bg p-6 space-y-4">
        {/* Hospital picker for platform_admin */}
        {isPlatformAdmin && (
          <div className="flex items-center gap-3">
            <Building2 size={16} className="text-faint shrink-0" />
            <select
              className="select max-w-xs"
              value={selectedHospitalId ?? ""}
              onChange={(e) => handleHospitalChange(Number(e.target.value))}
            >
              <option value="" disabled>Select a hospital…</option>
              {hospitals.map((h) => (
                <option key={h.id} value={h.id}>{h.name}</option>
              ))}
            </select>
          </div>
        )}

        {/* Toolbar */}
        {(!isPlatformAdmin || selectedHospitalId) && (
          <div className="flex items-center gap-3">
            <div className="relative flex-1 max-w-sm">
              <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-faint" />
              <input className="input input-icon-left text-sm" placeholder="Search name, phone, or MRN…"
                value={q} onChange={(e) => handleSearch(e.target.value)} />
            </div>
            <button onClick={() => setShowModal(true)} className="btn-primary flex items-center gap-2 text-sm">
              <UserPlus size={14} />
              Register Patient
            </button>
          </div>
        )}

        {isPlatformAdmin && !selectedHospitalId ? (
          <div className="card p-8 text-center text-sm text-faint">
            Select a hospital above to view its patients.
          </div>
        ) : err ? (
          <div className="card p-4 text-sm text-danger">{err}</div>
        ) : loading ? (
          <div className="space-y-2">
            {[1, 2, 3, 4, 5].map((i) => (
              <div key={i} className="skeleton h-12 w-full rounded-xl" />
            ))}
          </div>
        ) : patients.length === 0 ? (
          <div className="table-wrap">
            <div className="empty-state">
              <div className="empty-icon"><User size={24} /></div>
              <p className="empty-title">No patients found</p>
              <p className="empty-desc">
                {!q ? "Register the first patient using the button above." : "Try a different search term."}
              </p>
            </div>
          </div>
        ) : (
          <div className="table-wrap">
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="table-header sticky">
                  <tr>
                    <th>MRN</th>
                    <th>Name</th>
                    <th>Phone</th>
                    <th>Age</th>
                    <th>Gender</th>
                    <th>Registered</th>
                  </tr>
                </thead>
                <tbody className="table-body">
                  {patients.map((p) => <PatientRow key={p.id} p={p} onSelect={setSelected} />)}
                </tbody>
              </table>
            </div>
          </div>
        )}

        {showModal && (
          <RegisterModal
            onClose={() => setShowModal(false)}
            onSaved={() => load(q || undefined)}
            hospitalId={selectedHospitalId}
          />
        )}

        <Drawer
          open={selected !== null}
          title={selected?.name ?? "Patient"}
          subtitle={selected ? `MRN ${selected.mrn}` : undefined}
          onClose={() => setSelected(null)}
        >
          {selected && (
            <div className="divide-y divide-border">
              <DetailRow icon={Hash} label="MRN" value={selected.mrn} />
              <DetailRow icon={User} label="Name" value={selected.name} />
              <DetailRow icon={Phone} label="Mobile" value={selected.phone || "—"} />
              <DetailRow icon={Cake} label="Age" value={selected.age ?? "—"} />
              <DetailRow icon={VenetianMask} label="Gender" value={selected.gender ?? "—"} />
              <DetailRow icon={CalendarDays} label="Registered" value={new Date(selected.created_at).toLocaleString()} />
            </div>
          )}
        </Drawer>
      </main>
    </>
  );
}

function DetailRow({ icon: Icon, label, value }: { icon: React.ElementType; label: string; value: React.ReactNode }) {
  return (
    <div className="flex items-start gap-3 py-2.5">
      <span className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-surface-2 text-muted">
        <Icon size={14} />
      </span>
      <div className="min-w-0">
        <div className="text-xs text-faint">{label}</div>
        <div className="text-sm font-medium text-fg" dir="auto">{value}</div>
      </div>
    </div>
  );
}
