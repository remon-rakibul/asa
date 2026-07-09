"use client";

import { useState, useTransition } from "react";
import type { ScheduleRow } from "@/types";
import { saveSchedule } from "@/lib/api";
import { useUnsavedChanges } from "@/lib/useUnsavedChanges";
import DayRow from "./DayRow";

export default function ScheduleEditor({ initial }: { initial: ScheduleRow[] }) {
  const [rows, setRows] = useState<ScheduleRow[]>(initial);
  const [pending, startTransition] = useTransition();
  const [saved, setSaved] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [saveErr, setSaveErr] = useState("");

  useUnsavedChanges(dirty);

  function updateRow(next: ScheduleRow) {
    setRows((prev) =>
      prev.map((r) => (r.day_of_week === next.day_of_week ? next : r))
    );
    setSaved(false);
    setDirty(true);
    setSaveErr("");
  }

  function save() {
    setSaveErr("");
    startTransition(async () => {
      try {
        await saveSchedule(rows);
        setSaved(true);
        setDirty(false);
      } catch (ex: unknown) {
        setSaveErr(ex instanceof Error ? ex.message : "Could not save schedule.");
      }
    });
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <p className="text-sm text-muted">
          Changes take effect on the next call the agent handles.
        </p>
        <div className="flex items-center gap-3">
          {saveErr && <span className="text-xs text-danger animate-fade-in">{saveErr}</span>}
          {saved && !pending && !saveErr && (
            <span className="badge badge-success animate-fade-in">Saved</span>
          )}
          <button onClick={save} disabled={pending} className="btn-primary btn-sm">
            {pending ? "Saving…" : "Save Changes"}
          </button>
        </div>
      </div>

      <div className="table-wrap">
        <table className="w-full">
          <thead className="table-header">
            <tr>
              <th className="px-4 py-3 font-medium">Day</th>
              <th className="px-4 py-3 font-medium">Active</th>
              <th className="px-4 py-3 font-medium">Start</th>
              <th className="px-4 py-3 font-medium">End</th>
              <th className="px-4 py-3 font-medium">Slot</th>
              <th className="px-4 py-3 font-medium">Preview</th>
            </tr>
          </thead>
          <tbody className="table-body">
            {rows.map((row) => (
              <DayRow key={row.day_of_week} row={row} onChange={updateRow} />
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
