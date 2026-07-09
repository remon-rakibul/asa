"use client";

import { useCallback, useEffect, useState } from "react";
import TopBar from "@/components/layout/TopBar";
import {
  createDepartment,
  createHospital,
  listDepartments,
  listHospitals,
  updateHospital,
  type Department,
  type Hospital,
} from "@/lib/api";
import { useToast } from "@/lib/toast";
import Link from "next/link";
import { BookOpen, Building2, ChevronRight, Plus, X, Pencil } from "lucide-react";

function HospitalCard({
  hospital,
  onSelect,
}: {
  hospital: Hospital;
  onSelect: (h: Hospital) => void;
}) {
  return (
    <button
      onClick={() => onSelect(hospital)}
      className="card card-lift w-full text-left p-4 group"
    >
      <div className="flex items-start justify-between gap-4">
        <div className="flex items-center gap-3 min-w-0">
          <span
            className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl text-white"
            style={{ background: "var(--brand-grad)" }}
          >
            <Building2 size={18} />
          </span>
          <div className="min-w-0">
            <div className="font-semibold text-fg text-sm">{hospital.name}</div>
            <div className="text-xs text-faint font-mono mt-0.5">{hospital.slug}</div>
            {hospital.address && (
              <p className="mt-1 text-xs text-muted truncate max-w-64">{hospital.address}</p>
            )}
          </div>
        </div>
        <div className="flex items-center gap-3 shrink-0">
          <span
            className={`badge ${
              hospital.status === "active" ? "badge-success" : "badge-surface"
            } capitalize`}
          >
            <span
              className={`status-dot ${
                hospital.status === "active" ? "status-dot-success" : "status-dot-muted"
              }`}
            />
            {hospital.status}
          </span>
          <ChevronRight size={14} className="text-faint group-hover:text-primary transition-colors" />
        </div>
      </div>
      <div className="mt-3 flex items-center gap-2 border-t border-border pt-3">
        <Link
          href={`/hospitals/${hospital.id}/documents`}
          onClick={(e) => e.stopPropagation()}
          className="btn-ghost btn-xs flex items-center gap-1 text-xs"
        >
          <BookOpen size={12} /> Knowledge base
        </Link>
      </div>
    </button>
  );
}

function CreateHospitalModal({ onClose, onSaved }: { onClose: () => void; onSaved: () => void }) {
  const [form, setForm] = useState({ slug: "", name: "", address: "", license_number: "", timezone: "Asia/Dhaka" });
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState("");

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setSaving(true);
    setErr("");
    try {
      await createHospital(form);
      onSaved();
      onClose();
    } catch (ex: unknown) {
      setErr(ex instanceof Error ? ex.message : "Failed to create hospital");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="modal-overlay" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className="modal-panel" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <h2 className="modal-title">Add Hospital</h2>
          <button onClick={onClose} className="modal-close"><X size={16} /></button>
        </div>
        <form onSubmit={submit}>
          <div className="modal-body">
            <Field label="Hospital Name *">
              <input className="input mt-1 w-full" required value={form.name}
                onChange={(e) => setForm({ ...form, name: e.target.value })} />
            </Field>
            <Field label="Slug * (lowercase, hyphens only)">
              <input className="input mt-1 w-full font-mono" required pattern="^[a-z0-9-]+"
                value={form.slug} onChange={(e) => setForm({ ...form, slug: e.target.value })} />
            </Field>
            <Field label="Address">
              <input className="input mt-1 w-full" value={form.address}
                onChange={(e) => setForm({ ...form, address: e.target.value })} />
            </Field>
            <Field label="License Number">
              <input className="input mt-1 w-full" value={form.license_number}
                onChange={(e) => setForm({ ...form, license_number: e.target.value })} />
            </Field>
            <Field label="Timezone">
              <input className="input mt-1 w-full" value={form.timezone}
                onChange={(e) => setForm({ ...form, timezone: e.target.value })} />
            </Field>
            {err && <p className="text-xs text-danger">{err}</p>}
          </div>
          <div className="modal-footer">
            <button type="button" onClick={onClose} className="btn-ghost btn-sm">Cancel</button>
            <button type="submit" disabled={saving} className="btn-primary btn-sm">
              {saving ? "Saving\u2026" : "Create"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

function EditHospitalModal({
  hospital,
  onClose,
  onSaved,
}: {
  hospital: Hospital;
  onClose: () => void;
  onSaved: (h: Hospital) => void;
}) {
  const toast = useToast();
  const [form, setForm] = useState({
    name: hospital.name,
    address: hospital.address,
    license_number: hospital.license_number,
    timezone: hospital.timezone,
    status: hospital.status,
  });
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState("");

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setSaving(true);
    setErr("");
    try {
      const updated = await updateHospital(hospital.id, form);
      toast.success("Hospital updated");
      onSaved(updated);
      onClose();
    } catch (ex: unknown) {
      setErr(ex instanceof Error ? ex.message : "Failed to update hospital");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="modal-overlay" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className="modal-panel" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <h2 className="modal-title">Edit Hospital</h2>
          <button onClick={onClose} className="modal-close"><X size={16} /></button>
        </div>
        <form onSubmit={submit}>
          <div className="modal-body">
            <Field label="Hospital Name *">
              <input className="input mt-1 w-full" required value={form.name}
                onChange={(e) => setForm({ ...form, name: e.target.value })} />
            </Field>
            <Field label="Address">
              <input className="input mt-1 w-full" value={form.address}
                onChange={(e) => setForm({ ...form, address: e.target.value })} />
            </Field>
            <Field label="License Number">
              <input className="input mt-1 w-full" value={form.license_number}
                onChange={(e) => setForm({ ...form, license_number: e.target.value })} />
            </Field>
            <Field label="Timezone">
              <input className="input mt-1 w-full" value={form.timezone}
                onChange={(e) => setForm({ ...form, timezone: e.target.value })} />
            </Field>
            <Field label="Status">
              <select className="select mt-1 w-full" value={form.status}
                onChange={(e) => setForm({ ...form, status: e.target.value })}>
                <option value="active">Active</option>
                <option value="inactive">Inactive</option>
                <option value="suspended">Suspended</option>
              </select>
            </Field>
            {err && <p className="text-xs text-danger">{err}</p>}
          </div>
          <div className="modal-footer">
            <button type="button" onClick={onClose} className="btn-ghost btn-sm">Cancel</button>
            <button type="submit" disabled={saving} className="btn-primary btn-sm">
              {saving ? "Saving…" : "Save changes"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

function CreateDeptModal({
  hospital,
  onClose,
  onSaved,
}: {
  hospital: Hospital;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [form, setForm] = useState({ slug: "", name: "", specialty_code: "", floor: "", phone_ext: "" });
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState("");

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setSaving(true);
    setErr("");
    try {
      await createDepartment(hospital.id, form);
      onSaved();
      onClose();
    } catch (ex: unknown) {
      setErr(ex instanceof Error ? ex.message : "Failed to create department");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="modal-overlay" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className="modal-panel" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <h2 className="modal-title">Add Department &mdash; {hospital.name}</h2>
          <button onClick={onClose} className="modal-close"><X size={16} /></button>
        </div>
        <form onSubmit={submit}>
          <div className="modal-body">
            <Field label="Department Name *">
              <input className="input mt-1 w-full" required value={form.name}
                onChange={(e) => setForm({ ...form, name: e.target.value })} />
            </Field>
            <Field label="Slug *">
              <input className="input mt-1 w-full font-mono" required pattern="^[a-z0-9-]+"
                value={form.slug} onChange={(e) => setForm({ ...form, slug: e.target.value })} />
            </Field>
            <div className="grid grid-cols-3 gap-3">
              <Field label="Specialty Code">
                <input className="input mt-1 w-full text-sm" value={form.specialty_code}
                  onChange={(e) => setForm({ ...form, specialty_code: e.target.value })} />
              </Field>
              <Field label="Floor">
                <input className="input mt-1 w-full text-sm" value={form.floor}
                  onChange={(e) => setForm({ ...form, floor: e.target.value })} />
              </Field>
              <Field label="Phone Ext">
                <input className="input mt-1 w-full text-sm" value={form.phone_ext}
                  onChange={(e) => setForm({ ...form, phone_ext: e.target.value })} />
              </Field>
            </div>
            {err && <p className="text-xs text-danger">{err}</p>}
          </div>
          <div className="modal-footer">
            <button type="button" onClick={onClose} className="btn-ghost btn-sm">Cancel</button>
            <button type="submit" disabled={saving} className="btn-primary btn-sm">
              {saving ? "Saving\u2026" : "Add Department"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <label className="text-xs text-muted">{label}</label>
      {children}
    </div>
  );
}

function DeptRow({ d }: { d: Department }) {
  return (
    <tr>
      <td className="font-medium text-fg">{d.name}</td>
      <td className="font-mono text-muted">{d.slug}</td>
      <td className="text-muted">{d.specialty_code || "\u2014"}</td>
      <td className="text-muted">{d.floor || "\u2014"}</td>
      <td className="text-muted">{d.phone_ext || "\u2014"}</td>
    </tr>
  );
}

export default function HospitalsPage() {
  const [hospitals, setHospitals] = useState<Hospital[]>([]);
  const [selected, setSelected] = useState<Hospital | null>(null);
  const [departments, setDepartments] = useState<Department[]>([]);
  const [loading, setLoading] = useState(true);
  const [showHospitalModal, setShowHospitalModal] = useState(false);
  const [showEditModal, setShowEditModal] = useState(false);
  const [showDeptModal, setShowDeptModal] = useState(false);
  const [err, setErr] = useState("");
  const [deptErr, setDeptErr] = useState("");

  const loadHospitals = useCallback(async () => {
    setLoading(true);
    setErr("");
    try {
      setHospitals(await listHospitals());
    } catch {
      setErr("You don't have access to hospital management. Contact your administrator if you think this is a mistake.");
    } finally {
      setLoading(false);
    }
  }, []);

  const loadDepts = useCallback(async (hospital: Hospital) => {
    setDeptErr("");
    try {
      setDepartments(await listDepartments(hospital.id));
    } catch (ex: unknown) {
      setDeptErr(ex instanceof Error ? ex.message : "Could not load departments.");
    }
  }, []);

  useEffect(() => { loadHospitals(); }, [loadHospitals]);

  function selectHospital(h: Hospital) {
    setSelected(h);
    setDeptErr("");
    setDepartments([]);
    loadDepts(h);
  }

  return (
    <>
      <TopBar title="Hospitals" />
      <main className="flex-1 overflow-y-auto bg-bg p-6">
        {selected ? (
          <div className="space-y-4 animate-fade-in">
            {/* Breadcrumb */}
            <div className="flex items-center gap-2 text-sm">
              <button onClick={() => setSelected(null)} className="text-primary hover:underline">
                Hospitals
              </button>
              <ChevronRight size={12} className="text-faint" />
              <span className="gradient-text font-semibold">{selected.name}</span>
              <button
                onClick={() => setShowEditModal(true)}
                className="btn-ghost btn-sm ml-auto"
              >
                <Pencil size={13} /> Edit
              </button>
            </div>

            {/* Hospital info card */}
            <div className="card p-4 grid gap-3 sm:grid-cols-3 text-sm">
              <InfoCell label="Slug" value={selected.slug} mono />
              <InfoCell label="License" value={selected.license_number || "\u2014"} />
              <InfoCell label="Timezone" value={selected.timezone} />
              <InfoCell label="Address" value={selected.address || "\u2014"} />
              <InfoCell label="Status" value={selected.status} />
              <InfoCell label="Created" value={new Date(selected.created_at).toLocaleDateString()} />
            </div>

            {/* Departments */}
            <div className="flex items-center justify-between">
              <h2 className="text-sm font-semibold text-fg">Departments</h2>
              <button
                onClick={() => setShowDeptModal(true)}
                className="btn-primary btn-sm flex items-center gap-1.5"
              >
                <Plus size={12} />
                Add Department
              </button>
            </div>

            {deptErr && (
              <div className="card p-4 flex items-center gap-2 text-sm text-danger">
                <span>&#9888;</span> {deptErr}
              </div>
            )}

            {!deptErr && departments.length === 0 ? (
              <div className="empty-state py-12">
                <div className="empty-icon">
                  <Building2 size={22} />
                </div>
                <p className="empty-title">No departments yet</p>
                <p className="empty-desc">Add the first department to this hospital to start routing appointments.</p>
              </div>
            ) : !deptErr ? (
              <div className="table-wrap">
                <table className="w-full text-left">
                  <thead className="table-header sticky">
                    <tr>
                      <th>Name</th>
                      <th>Slug</th>
                      <th>Specialty</th>
                      <th>Floor</th>
                      <th>Ext</th>
                    </tr>
                  </thead>
                  <tbody className="table-body">
                    {departments.map((d) => <DeptRow key={d.id} d={d} />)}
                  </tbody>
                </table>
              </div>
            ) : null}

            {showDeptModal && (
              <CreateDeptModal
                hospital={selected}
                onClose={() => setShowDeptModal(false)}
                onSaved={() => loadDepts(selected)}
              />
            )}

            {showEditModal && (
              <EditHospitalModal
                hospital={selected}
                onClose={() => setShowEditModal(false)}
                onSaved={(h) => {
                  setSelected(h);
                  setHospitals((list) => list.map((x) => (x.id === h.id ? h : x)));
                }}
              />
            )}
          </div>
        ) : (
          <div className="space-y-4">
            <div className="flex items-center justify-between">
              <p className="text-sm text-muted">All hospital tenants (platform admin view)</p>
              <button
                onClick={() => setShowHospitalModal(true)}
                className="btn-primary flex items-center gap-2 text-sm"
              >
                <Plus size={14} />
                Add Hospital
              </button>
            </div>

            {err && (
              <div className="card p-4 flex items-center gap-2 text-sm text-danger">
                <span className="text-danger">&#9888;</span>
                {err}
              </div>
            )}

            {loading ? (
              <div className="space-y-2">
                {[1, 2, 3].map((i) => (
                  <div key={i} className="skeleton h-[76px] rounded-2xl" />
                ))}
              </div>
            ) : hospitals.length === 0 ? (
              <div className="empty-state">
                <div className="empty-icon">
                  <Building2 size={22} />
                </div>
                <p className="empty-title">No hospitals yet</p>
                <p className="empty-desc">Create the first hospital to get started with appointment routing.</p>
              </div>
            ) : (
              <div className="space-y-2">
                {hospitals.map((h) => (
                  <HospitalCard key={h.id} hospital={h} onSelect={selectHospital} />
                ))}
              </div>
            )}

            {showHospitalModal && (
              <CreateHospitalModal
                onClose={() => setShowHospitalModal(false)}
                onSaved={loadHospitals}
              />
            )}
          </div>
        )}
      </main>
    </>
  );
}

function InfoCell({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div>
      <div className="text-xs text-faint mb-0.5">{label}</div>
      <div className={`text-sm text-fg ${mono ? "font-mono" : ""}`}>{value}</div>
    </div>
  );
}
