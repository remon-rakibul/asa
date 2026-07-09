"use client";

import { useEffect, useState } from "react";
import { format } from "date-fns";
import { Search, ChevronLeft, ChevronRight } from "lucide-react";
import type { Appointment } from "@/types";
import StatusBadge from "@/components/ui/StatusBadge";
import CancelDialog from "@/components/appointments/CancelDialog";
import AppointmentDrawer from "@/components/appointments/AppointmentDrawer";

const PAGE_SIZE = 25;

export default function AppointmentTable({
  rows,
  onCancelled,
}: {
  rows: Appointment[];
  onCancelled?: () => void;
}) {
  const [page, setPage] = useState(1);
  const [selected, setSelected] = useState<Appointment | null>(null);

  // Reset to the first page whenever the result set changes (e.g. new filters).
  useEffect(() => { setPage(1); }, [rows]);

  if (rows.length === 0) {
    return (
      <div className="table-wrap animate-fade-in-up">
        <div className="empty-state">
          <div className="empty-icon">
            <Search size={20} />
          </div>
          <p className="empty-title">No appointments match these filters.</p>
          <p className="empty-desc">Try adjusting your search criteria or date range.</p>
        </div>
      </div>
    );
  }

  const pageCount = Math.ceil(rows.length / PAGE_SIZE);
  const start = (page - 1) * PAGE_SIZE;
  const pageRows = rows.slice(start, start + PAGE_SIZE);

  return (
    <div className="table-wrap animate-fade-in-up delay-100">
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="table-header sticky">
            <tr>
              {["#", "Patient", "Age", "Mobile", "Scheduled", "Duration", "Status", "Booked", ""].map((h) => (
                <th key={h}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody className="table-body">
            {pageRows.map((a, i) => (
              <tr
                key={a.id}
                className="animate-fade-in cursor-pointer"
                style={{ animationDelay: `${i * 20}ms` }}
                onClick={() => setSelected(a)}
                role="button"
                tabIndex={0}
                onKeyDown={(e) => { if (e.key === "Enter") setSelected(a); }}
              >
                <td className="tabular-nums text-fg font-semibold">
                  {a.serial_number ?? "—"}
                </td>
                <td className="font-semibold text-fg">{a.patient_name}</td>
                <td className="text-muted">{a.patient_age}</td>
                <td className="tabular-nums text-muted">{a.patient_mobile}</td>
                <td className="text-muted">
                  {format(new Date(a.scheduled_at), "EEE d MMM yyyy, h:mm a")}
                </td>
                <td className="text-muted">
                  <span className="badge badge-surface">{a.duration_mins} min</span>
                </td>
                <td>
                  <span className="inline-flex items-center gap-1.5">
                    <StatusBadge status={a.status} />
                    {a.patient_confirmed_at && a.status === "confirmed" && (
                      <span
                        className="badge badge-surface text-[10px]"
                        title="Patient confirmed via reminder reply"
                      >
                        ✓ রোগী নিশ্চিত
                      </span>
                    )}
                  </span>
                </td>
                <td className="text-xs text-faint">
                  {format(new Date(a.created_at), "d MMM, h:mm a")}
                </td>
                <td className="text-right" onClick={(e) => e.stopPropagation()}>
                  {a.status === "confirmed" ? (
                    <CancelDialog
                      appointmentId={a.id}
                      patientName={a.patient_name}
                      onCancelled={onCancelled}
                    />
                  ) : (
                    <span className="text-xs text-faint">—</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {pageCount > 1 && (
        <div className="flex items-center justify-between border-t border-border px-5 py-3 text-xs text-muted">
          <span>
            Showing {start + 1}–{Math.min(start + PAGE_SIZE, rows.length)} of {rows.length}
          </span>
          <div className="flex items-center gap-1">
            <button
              onClick={() => setPage((p) => Math.max(1, p - 1))}
              disabled={page === 1}
              className="btn-ghost btn-sm disabled:opacity-40"
              aria-label="Previous page"
            >
              <ChevronLeft size={15} />
            </button>
            <span className="px-2 tabular-nums">
              {page} / {pageCount}
            </span>
            <button
              onClick={() => setPage((p) => Math.min(pageCount, p + 1))}
              disabled={page === pageCount}
              className="btn-ghost btn-sm disabled:opacity-40"
              aria-label="Next page"
            >
              <ChevronRight size={15} />
            </button>
          </div>
        </div>
      )}

      <AppointmentDrawer
        appointment={selected}
        onClose={() => setSelected(null)}
        onChanged={onCancelled}
      />
    </div>
  );
}
