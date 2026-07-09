"use client";

import type { ScheduleRow } from "@/types";
import { WEEKDAY_LABELS } from "@/types";
import SlotPreview from "./SlotPreview";

const DURATIONS = [15, 30, 45, 60];

export default function DayRow({
  row,
  onChange,
}: {
  row: ScheduleRow;
  onChange: (next: ScheduleRow) => void;
}) {
  return (
    <tr className="divider">
      <td className="px-4 py-3 font-medium text-fg">
        {WEEKDAY_LABELS[row.day_of_week]}
      </td>
      <td className="px-4 py-3">
        <div className="flex items-center gap-2">
          <button
            type="button"
            role="switch"
            aria-checked={row.active}
            onClick={() => onChange({ ...row, active: !row.active })}
            className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors ${
              row.active ? "bg-emerald-500" : "bg-surface-3"
            }`}
          >
            <span
              className={`inline-block h-5 w-5 transform rounded-full bg-white shadow transition-transform ${
                row.active ? "translate-x-5" : "translate-x-0.5"
              }`}
            />
          </button>
          {row.active ? (
            <span className="badge badge-success">Active</span>
          ) : (
            <span className="badge badge-surface">Inactive</span>
          )}
        </div>
      </td>
      <td className="px-4 py-3">
        <input
          type="time"
          value={row.start_time}
          disabled={!row.active}
          onChange={(e) => onChange({ ...row, start_time: e.target.value })}
          className="input"
        />
      </td>
      <td className="px-4 py-3">
        <input
          type="time"
          value={row.end_time}
          disabled={!row.active}
          onChange={(e) => onChange({ ...row, end_time: e.target.value })}
          className="input"
        />
      </td>
      <td className="px-4 py-3">
        <select
          value={row.slot_duration}
          disabled={!row.active}
          onChange={(e) =>
            onChange({ ...row, slot_duration: Number(e.target.value) })
          }
          className="select"
        >
          {DURATIONS.map((d) => (
            <option key={d} value={d}>
              {d} min
            </option>
          ))}
        </select>
      </td>
      <td className="px-4 py-3">
        <SlotPreview
          startTime={row.start_time}
          endTime={row.end_time}
          slotDuration={row.slot_duration}
          active={row.active}
        />
      </td>
    </tr>
  );
}
