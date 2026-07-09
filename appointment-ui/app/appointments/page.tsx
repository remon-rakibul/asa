"use client";

import { Suspense, useCallback, useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import { format } from "date-fns";
import { Download } from "lucide-react";
import TopBar from "@/components/layout/TopBar";
import AppointmentFilters from "@/components/appointments/AppointmentFilters";
import AppointmentTable from "@/components/appointments/AppointmentTable";
import TableSkeleton from "@/components/ui/TableSkeleton";
import { listAppointments } from "@/lib/api";
import { exportCsv } from "@/lib/csv";
import type { Appointment, AppointmentFilters as Filters, AppointmentStatus } from "@/types";

function AppointmentsView() {
  const sp = useSearchParams();
  const [rows, setRows] = useState<Appointment[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const filters: Filters = {
    date_from: sp.get("date_from") ?? undefined,
    date_to: sp.get("date_to") ?? undefined,
    status: (sp.get("status") as AppointmentStatus | "all") ?? "all",
    q: sp.get("q") ?? undefined,
  };

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      setRows(await listAppointments(filters));
    } catch (ex: unknown) {
      setError(ex instanceof Error ? ex.message : "Could not load appointments.");
      setRows([]);
    } finally {
      setLoading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sp.toString()]);

  useEffect(() => {
    load();
  }, [load]);

  function handleExport() {
    exportCsv(
      `appointments-${format(new Date(), "yyyy-MM-dd")}`,
      rows,
      [
        { header: "Serial", value: (a) => a.serial_number ?? "" },
        { header: "Patient", value: (a) => a.patient_name },
        { header: "Age", value: (a) => a.patient_age },
        { header: "Mobile", value: (a) => a.patient_mobile },
        { header: "Scheduled", value: (a) => format(new Date(a.scheduled_at), "yyyy-MM-dd HH:mm") },
        { header: "Duration (min)", value: (a) => a.duration_mins },
        { header: "Status", value: (a) => a.status },
        { header: "Booked", value: (a) => format(new Date(a.created_at), "yyyy-MM-dd HH:mm") },
      ]
    );
  }

  return (
    <main className="flex-1 space-y-4 overflow-y-auto bg-bg p-6">
      <AppointmentFilters count={loading ? undefined : rows.length} />
      <div className="flex justify-end">
        <button
          onClick={handleExport}
          disabled={loading || rows.length === 0}
          className="btn-outline btn-sm"
        >
          <Download size={14} /> Export CSV
        </button>
      </div>
      {loading ? (
        <TableSkeleton rows={8} cols={5} />
      ) : error ? (
        <div className="card p-4 text-sm text-danger">{error}</div>
      ) : (
        <AppointmentTable rows={rows} onCancelled={load} />
      )}
    </main>
  );
}

export default function AppointmentsPage() {
  return (
    <>
      <TopBar title="Appointments" />
      <Suspense fallback={null}>
        <AppointmentsView />
      </Suspense>
    </>
  );
}
