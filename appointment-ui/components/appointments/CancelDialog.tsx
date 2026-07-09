"use client";

import { useState } from "react";
import { cancelAppointment } from "@/lib/api";
import ConfirmDialog from "@/components/ui/ConfirmDialog";

export default function CancelDialog({
  appointmentId,
  patientName,
  onCancelled,
}: {
  appointmentId: string;
  patientName: string;
  onCancelled?: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [pending, setPending] = useState(false);
  const [err, setErr] = useState("");

  async function confirm() {
    setPending(true);
    setErr("");
    try {
      await cancelAppointment(appointmentId);
      setOpen(false);
      onCancelled?.();
    } catch (ex: unknown) {
      setErr(ex instanceof Error ? ex.message : "Could not cancel appointment.");
    } finally {
      setPending(false);
    }
  }

  return (
    <>
      <button
        onClick={() => setOpen(true)}
        className="btn-ghost text-xs text-muted hover:text-danger"
      >
        Cancel
      </button>

      <ConfirmDialog
        open={open}
        destructive
        title="Cancel appointment"
        subtitle={patientName}
        description="Are you sure you want to cancel this appointment? This action cannot be undone."
        confirmLabel="Cancel appointment"
        cancelLabel="Keep it"
        busyLabel="Cancelling…"
        busy={pending}
        error={err}
        onConfirm={confirm}
        onClose={() => { if (!pending) { setOpen(false); setErr(""); } }}
      />
    </>
  );
}
