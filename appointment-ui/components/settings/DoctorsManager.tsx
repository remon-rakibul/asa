"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  Plus, Star, Trash2, Phone, Stethoscope, CheckCircle2, ChevronDown,
  GraduationCap, ImagePlus, FileText,
} from "lucide-react";
import {
  addDoctor,
  deleteDoctor,
  deleteDoctorPhoto,
  doctorPhotoUrl,
  listDoctors,
  updateDoctor,
  uploadDoctorPhoto,
  type Doctor,
} from "@/lib/api";
import { useToast } from "@/lib/toast";
import ConfirmDialog from "@/components/ui/ConfirmDialog";

function DoctorAvatar({ name }: { name: string }) {
  const parts = name.trim().split(/\s+/);
  const letters = parts.length >= 2 ? parts[0][0] + parts[1][0] : name.slice(0, 2);
  return (
    <span
      className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl text-sm font-bold uppercase text-white"
      style={{ background: "var(--brand-grad)", boxShadow: "0 4px 12px rgba(99,102,241,0.35)" }}
    >
      {letters.toUpperCase()}
    </span>
  );
}

export default function DoctorsManager() {
  const toast = useToast();
  const [doctors, setDoctors] = useState<Doctor[] | null>(null);
  const [pendingDelete, setPendingDelete] = useState<Doctor | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [adding, setAdding] = useState(false);
  const [newName, setNewName] = useState("");
  const [newSpecialty, setNewSpecialty] = useState("");
  const [newPhone, setNewPhone] = useState("");
  const [newDegrees, setNewDegrees] = useState("");
  const [newDescription, setNewDescription] = useState("");
  const [savingNew, setSavingNew] = useState(false);

  const load = useCallback(async () => {
    try {
      setDoctors(await listDoctors());
    } catch {
      setDoctors([]);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  async function run(fn: () => Promise<unknown>, successMsg?: string) {
    try {
      await fn();
      await load();
      if (successMsg) toast.success(successMsg);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Something went wrong");
    }
  }

  async function submitNewDoctor() {
    const name = newName.trim();
    if (!name) return;
    setSavingNew(true);
    try {
      await addDoctor({
        name,
        specialty: newSpecialty.trim() || undefined,
        phone: newPhone.trim() || undefined,
        degrees: newDegrees.trim() || undefined,
        description: newDescription.trim() || undefined,
      });
      await load();
      toast.success(`Added ${name}`);
      setNewName(""); setNewSpecialty(""); setNewPhone("");
      setNewDegrees(""); setNewDescription(""); setAdding(false);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Could not add doctor");
    } finally {
      setSavingNew(false);
    }
  }

  async function confirmDelete() {
    if (!pendingDelete) return;
    setDeleting(true);
    try {
      await deleteDoctor(pendingDelete.id);
      await load();
      toast.success(`Removed ${pendingDelete.name || "doctor"}`);
      setPendingDelete(null);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Could not remove doctor");
    } finally {
      setDeleting(false);
    }
  }

  return (
    <div className="space-y-4">
      {/* Header card */}
      <div className="card p-5">
        <div className="flex items-center justify-between">
          <div>
            <h3 className="text-sm font-bold text-fg">Care providers</h3>
            <p className="mt-0.5 text-xs text-muted">
              Star a doctor to make them <span className="font-semibold text-fg">primary</span> — their name and phone are used in all AI greetings.
            </p>
          </div>
          <button
            onClick={() => setAdding((v) => !v)}
            className="btn-primary px-3 py-2 text-sm"
            aria-expanded={adding}
          >
            <Plus size={15} /> Add doctor
          </button>
        </div>

        {/* Inline create form — no junk record is persisted until you save. */}
        {adding && (
          <div className="mt-4 rounded-xl border border-border bg-surface-2 p-4 animate-slide-down">
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
              <input
                className="input"
                placeholder="Full name (required)"
                value={newName}
                autoFocus
                onChange={(e) => setNewName(e.target.value)}
                onKeyDown={(e) => { if (e.key === "Enter") submitNewDoctor(); }}
              />
              <input
                className="input"
                placeholder="Specialty"
                value={newSpecialty}
                onChange={(e) => setNewSpecialty(e.target.value)}
                onKeyDown={(e) => { if (e.key === "Enter") submitNewDoctor(); }}
              />
              <input
                className="input"
                placeholder="Phone (8801…)"
                value={newPhone}
                onChange={(e) => setNewPhone(e.target.value)}
                onKeyDown={(e) => { if (e.key === "Enter") submitNewDoctor(); }}
              />
            </div>
            <div className="mt-3 grid grid-cols-1 gap-3">
              <input
                className="input"
                placeholder="Degrees (e.g. MBBS, FCPS)"
                value={newDegrees}
                onChange={(e) => setNewDegrees(e.target.value)}
                onKeyDown={(e) => { if (e.key === "Enter") submitNewDoctor(); }}
              />
              <textarea
                className="input min-h-[72px] resize-y"
                placeholder="Short bio patients will see — qualifications, experience, languages… (also helps the AI assistant answer questions about this doctor)"
                value={newDescription}
                maxLength={2000}
                onChange={(e) => setNewDescription(e.target.value)}
              />
            </div>
            <div className="mt-3 flex items-center justify-end gap-2">
              <button
                onClick={() => {
                  setAdding(false); setNewName(""); setNewSpecialty("");
                  setNewPhone(""); setNewDegrees(""); setNewDescription("");
                }}
                className="btn-ghost btn-sm"
                disabled={savingNew}
              >
                Cancel
              </button>
              <button
                onClick={submitNewDoctor}
                disabled={!newName.trim() || savingNew}
                className="btn-primary btn-sm"
              >
                {savingNew ? "Saving…" : "Save doctor"}
              </button>
            </div>
          </div>
        )}
      </div>

      {/* Doctor list */}
      {doctors === null ? (
        <DoctorsSkeleton />
      ) : doctors.length === 0 ? (
        <div
          className="card flex flex-col items-center justify-center py-14 text-center animate-fade-in"
        >
          <div
            className="mb-3 flex h-14 w-14 items-center justify-center rounded-2xl text-faint"
            style={{ background: "var(--surface-2)" }}
          >
            <Stethoscope size={24} />
          </div>
          <p className="text-sm font-medium text-muted">No doctors yet</p>
          <p className="mt-1 text-xs text-faint">Click "Add doctor" to get started.</p>
        </div>
      ) : (
        <div className="space-y-3">
          {doctors.map((d, i) => (
            <DoctorRow
              key={d.id}
              doctor={d}
              canDelete={doctors.length > 1}
              index={i}
              onSave={(patch) => run(() => updateDoctor(d.id, patch), "Doctor saved")}
              onMakePrimary={() => run(() => updateDoctor(d.id, { is_primary: true }), "Primary doctor updated")}
              onDelete={() => setPendingDelete(d)}
              onUploadPhoto={(file) => run(() => uploadDoctorPhoto(d.id, file), "Photo updated")}
              onDeletePhoto={() => run(() => deleteDoctorPhoto(d.id), "Photo removed")}
            />
          ))}
        </div>
      )}

      <ConfirmDialog
        open={pendingDelete !== null}
        destructive
        title="Remove doctor"
        subtitle={pendingDelete?.name || undefined}
        description="This removes the doctor from this department. Existing appointments are unaffected. This cannot be undone."
        confirmLabel="Remove doctor"
        busyLabel="Removing…"
        busy={deleting}
        onConfirm={confirmDelete}
        onClose={() => { if (!deleting) setPendingDelete(null); }}
      />
    </div>
  );
}

function DoctorRow({
  doctor,
  canDelete,
  index,
  onSave,
  onMakePrimary,
  onDelete,
  onUploadPhoto,
  onDeletePhoto,
}: {
  doctor: Doctor;
  canDelete: boolean;
  index: number;
  onSave: (patch: {
    name: string; specialty: string; phone: string;
    degrees: string; description: string;
  }) => void;
  onMakePrimary: () => void;
  onDelete: () => void;
  onUploadPhoto: (file: File) => void | Promise<void>;
  onDeletePhoto: () => void | Promise<void>;
}) {
  const [expanded, setExpanded]         = useState(false);
  const [name, setName]                 = useState(doctor.name);
  const [specialty, setSpecialty]       = useState(doctor.specialty);
  const [phone, setPhone]               = useState(doctor.phone);
  const [degrees, setDegrees]           = useState(doctor.degrees);
  const [description, setDescription]   = useState(doctor.description);
  const [justSaved, setJustSaved]       = useState(false);
  // Bumped after every photo change so the <img> URL busts any cached copy.
  const [photoVer, setPhotoVer]         = useState(() => Date.now());
  const [photoBusy, setPhotoBusy]       = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    setName(doctor.name);
    setSpecialty(doctor.specialty);
    setPhone(doctor.phone);
    setDegrees(doctor.degrees);
    setDescription(doctor.description);
  }, [doctor.name, doctor.specialty, doctor.phone, doctor.degrees, doctor.description]);

  const dirty =
    name !== doctor.name || specialty !== doctor.specialty || phone !== doctor.phone ||
    degrees !== doctor.degrees || description !== doctor.description;

  function handleSave() {
    onSave({ name, specialty, phone, degrees, description });
    setJustSaved(true);
    setTimeout(() => setJustSaved(false), 2500);
  }

  async function handlePhotoPick(file: File | undefined) {
    if (!file) return;
    setPhotoBusy(true);
    try {
      await onUploadPhoto(file);
      setPhotoVer(Date.now());
    } finally {
      setPhotoBusy(false);
      if (fileInputRef.current) fileInputRef.current.value = "";
    }
  }

  async function handlePhotoRemove() {
    setPhotoBusy(true);
    try {
      await onDeletePhoto();
      setPhotoVer(Date.now());
    } finally {
      setPhotoBusy(false);
    }
  }

  return (
    <div
      className="card overflow-hidden animate-fade-in-up"
      style={{
        animationDelay: `${index * 60}ms`,
        borderColor: doctor.is_primary
          ? "color-mix(in srgb, var(--primary) 30%, var(--border))"
          : undefined,
      }}
    >
      {/* Collapsed tile header — click to expand the profile editor */}
      <div
        role="button"
        tabIndex={0}
        aria-expanded={expanded}
        onClick={() => setExpanded((v) => !v)}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") { e.preventDefault(); setExpanded((v) => !v); }
        }}
        className="flex cursor-pointer items-center justify-between px-5 py-4 transition-colors hover:bg-surface-2/50"
        style={expanded ? { borderBottom: "1px solid var(--border)" } : undefined}
      >
        <div className="flex items-center gap-3">
          {doctor.has_photo ? (
            // eslint-disable-next-line @next/next/no-img-element
            <img
              src={doctorPhotoUrl(doctor.id, photoVer)}
              alt={name || "Doctor"}
              className="h-11 w-11 shrink-0 rounded-xl object-cover"
              style={{ boxShadow: "0 4px 12px rgba(99,102,241,0.35)" }}
            />
          ) : (
            <DoctorAvatar name={name || "?"} />
          )}
          <div>
            <div className="text-sm font-bold text-fg">{name || "New doctor"}</div>
            <div className="mt-0.5 flex items-center gap-2">
              {doctor.is_primary ? (
                <span
                  className="inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-xs font-semibold"
                  style={{
                    background: "var(--brand-soft)",
                    color: "var(--primary)",
                    border: "1px solid color-mix(in srgb, var(--primary) 25%, transparent)",
                  }}
                >
                  <Star size={10} className="fill-current" /> Primary
                </span>
              ) : (
                <button
                  onClick={(e) => { e.stopPropagation(); onMakePrimary(); }}
                  className="inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-xs text-faint transition-all hover:text-muted"
                  style={{
                    background: "var(--surface-2)",
                    border: "1px solid var(--border)",
                  }}
                  title="Make primary doctor"
                >
                  <Star size={10} /> Make primary
                </button>
              )}
              {(specialty || degrees) && (
                <span className="text-xs text-faint">
                  · {[specialty, degrees].filter(Boolean).join(" · ")}
                </span>
              )}
            </div>
          </div>
        </div>

        <div className="flex items-center gap-2">
        {canDelete && (
          <button
            onClick={(e) => { e.stopPropagation(); onDelete(); }}
            className="flex h-8 w-8 items-center justify-center rounded-lg text-faint transition-all hover:text-danger"
            style={{ border: "1px solid var(--border)" }}
            onMouseEnter={(e) => {
              (e.currentTarget as HTMLButtonElement).style.background =
                "color-mix(in srgb, var(--danger) 10%, transparent)";
              (e.currentTarget as HTMLButtonElement).style.borderColor =
                "color-mix(in srgb, var(--danger) 30%, transparent)";
            }}
            onMouseLeave={(e) => {
              (e.currentTarget as HTMLButtonElement).style.background = "transparent";
              (e.currentTarget as HTMLButtonElement).style.borderColor = "var(--border)";
            }}
            title="Remove doctor"
          >
            <Trash2 size={14} />
          </button>
        )}
        <ChevronDown
          size={16}
          className={`text-faint transition-transform duration-200 ${expanded ? "rotate-180" : ""}`}
        />
        </div>
      </div>

      {expanded && (<>
      {/* Photo */}
      <div
        className="flex items-center gap-4 px-5 py-4"
        style={{ borderBottom: "1px solid var(--border)" }}
      >
        {doctor.has_photo ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img
            src={doctorPhotoUrl(doctor.id, photoVer)}
            alt={name || "Doctor"}
            className="h-20 w-20 shrink-0 rounded-2xl object-cover"
            style={{ border: "1px solid var(--border)" }}
          />
        ) : (
          <span
            className="flex h-20 w-20 shrink-0 items-center justify-center rounded-2xl text-faint"
            style={{ background: "var(--surface-2)", border: "1px dashed var(--border)" }}
          >
            <ImagePlus size={22} />
          </span>
        )}
        <div className="space-y-1.5">
          <p className="text-xs font-semibold text-muted">Profile photo</p>
          <p className="text-xs text-faint">Shown to patients on the booking portal. JPEG/PNG/WebP, max 2 MB.</p>
          <div className="flex items-center gap-2 pt-1">
            <input
              ref={fileInputRef}
              type="file"
              accept="image/jpeg,image/png,image/webp"
              className="hidden"
              onChange={(e) => handlePhotoPick(e.target.files?.[0])}
            />
            <button
              onClick={() => fileInputRef.current?.click()}
              disabled={photoBusy}
              className="btn-ghost btn-sm"
              style={{ border: "1px solid var(--border)" }}
            >
              <ImagePlus size={13} /> {photoBusy ? "Working…" : doctor.has_photo ? "Replace" : "Upload"}
            </button>
            {doctor.has_photo && (
              <button
                onClick={handlePhotoRemove}
                disabled={photoBusy}
                className="btn-ghost btn-sm text-faint hover:text-danger"
              >
                Remove
              </button>
            )}
          </div>
        </div>
      </div>

      {/* Fields */}
      <div className="grid grid-cols-1 gap-4 px-5 py-4 sm:grid-cols-3">
        <label className="space-y-1.5">
          <span className="flex items-center gap-1.5 text-xs font-semibold text-muted">
            <span
              className="flex h-4 w-4 items-center justify-center rounded"
              style={{ background: "var(--surface-2)" }}
            >
              <Stethoscope size={9} className="text-primary" />
            </span>
            Full name
          </span>
          <input
            className="input"
            placeholder="Dr. …"
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
        </label>
        <label className="space-y-1.5">
          <span className="flex items-center gap-1.5 text-xs font-semibold text-muted">
            <span
              className="flex h-4 w-4 items-center justify-center rounded"
              style={{ background: "var(--surface-2)" }}
            >
              <Stethoscope size={9} className="text-primary" />
            </span>
            Specialty
          </span>
          <input
            className="input"
            placeholder="e.g. Cardiology"
            value={specialty}
            onChange={(e) => setSpecialty(e.target.value)}
          />
        </label>
        <label className="space-y-1.5">
          <span className="flex items-center gap-1.5 text-xs font-semibold text-muted">
            <span
              className="flex h-4 w-4 items-center justify-center rounded"
              style={{ background: "var(--surface-2)" }}
            >
              <Phone size={9} className="text-primary" />
            </span>
            Phone
          </span>
          <input
            className="input"
            placeholder="8801…"
            value={phone}
            onChange={(e) => setPhone(e.target.value)}
          />
        </label>
        <label className="space-y-1.5">
          <span className="flex items-center gap-1.5 text-xs font-semibold text-muted">
            <span
              className="flex h-4 w-4 items-center justify-center rounded"
              style={{ background: "var(--surface-2)" }}
            >
              <GraduationCap size={9} className="text-primary" />
            </span>
            Degrees
          </span>
          <input
            className="input"
            placeholder="e.g. MBBS, FCPS (Medicine)"
            value={degrees}
            maxLength={300}
            onChange={(e) => setDegrees(e.target.value)}
          />
        </label>
        <label className="space-y-1.5 sm:col-span-3">
          <span className="flex items-center gap-1.5 text-xs font-semibold text-muted">
            <span
              className="flex h-4 w-4 items-center justify-center rounded"
              style={{ background: "var(--surface-2)" }}
            >
              <FileText size={9} className="text-primary" />
            </span>
            Description
          </span>
          <textarea
            className="input min-h-[88px] resize-y"
            placeholder="Short bio patients will see — qualifications, experience, languages… The AI assistant also uses this to answer questions about the doctor."
            value={description}
            maxLength={2000}
            onChange={(e) => setDescription(e.target.value)}
          />
        </label>
      </div>

      {/* Footer */}
      <div
        className="flex items-center justify-between px-5 py-3"
        style={{ background: "var(--surface-2)", borderTop: "1px solid var(--border)" }}
      >
        {justSaved ? (
          <span className="flex items-center gap-1.5 text-xs font-medium text-success animate-fade-in">
            <CheckCircle2 size={13} /> Saved
          </span>
        ) : (
          <span className="text-xs text-faint">
            {dirty ? "Unsaved changes" : "No changes"}
          </span>
        )}
        <button
          onClick={handleSave}
          disabled={!dirty}
          className="btn-primary px-4 py-1.5 text-xs"
        >
          Save
        </button>
      </div>
      </>)}
    </div>
  );
}

function DoctorsSkeleton() {
  return (
    <div className="space-y-3">
      {[...Array(2)].map((_, i) => (
        <div
          key={i}
          className="card overflow-hidden"
          style={{ animationDelay: `${i * 80}ms` }}
        >
          <div className="flex items-center gap-3 border-b border-border px-5 py-4">
            <div className="skeleton h-11 w-11 rounded-xl" />
            <div className="space-y-2">
              <div className="skeleton h-4 w-32 rounded" />
              <div className="skeleton h-3 w-20 rounded" />
            </div>
          </div>
          <div className="grid grid-cols-3 gap-4 px-5 py-4">
            {[...Array(3)].map((_, j) => (
              <div key={j} className="space-y-1.5">
                <div className="skeleton h-3 w-16 rounded" />
                <div className="skeleton h-10 w-full rounded-xl" />
              </div>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}
