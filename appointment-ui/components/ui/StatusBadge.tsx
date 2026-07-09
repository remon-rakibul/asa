import type { AppointmentStatus } from "@/types";

const CONFIG: Record<
  AppointmentStatus,
  { badge: string; dot: string; label: string }
> = {
  confirmed:  { badge: "badge-success", dot: "status-dot-success", label: "Confirmed" },
  pending:    { badge: "badge-warning", dot: "status-dot-warning", label: "Pending" },
  checked_in: { badge: "badge-info",    dot: "status-dot-info",    label: "Checked in" },
  completed:  { badge: "badge-success", dot: "status-dot-success", label: "Completed" },
  no_show:    { badge: "badge-warning", dot: "status-dot-warning", label: "No-show" },
  cancelled:  { badge: "badge-danger",  dot: "status-dot-danger",  label: "Cancelled" },
};

export default function StatusBadge({ status }: { status: AppointmentStatus }) {
  const c = CONFIG[status] ?? CONFIG.cancelled;
  return (
    <span className={`badge ${c.badge}`}>
      <span className={`status-dot ${c.dot}`} />
      {c.label}
    </span>
  );
}
