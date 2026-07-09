export function slotCount(
  startTime: string,
  endTime: string,
  slotDuration: number
): number {
  const [sh, sm] = startTime.split(":").map(Number);
  const [eh, em] = endTime.split(":").map(Number);
  const mins = eh * 60 + em - (sh * 60 + sm);
  if (mins <= 0 || slotDuration <= 0) return 0;
  return Math.floor(mins / slotDuration);
}

export default function SlotPreview({
  startTime,
  endTime,
  slotDuration,
  active,
}: {
  startTime: string;
  endTime: string;
  slotDuration: number;
  active: boolean;
}) {
  if (!active) return <span className="text-sm text-faint">—</span>;
  const count = slotCount(startTime, endTime, slotDuration);
  return (
    <span className="badge badge-surface">
      {count} slot{count === 1 ? "" : "s"}/day
    </span>
  );
}
