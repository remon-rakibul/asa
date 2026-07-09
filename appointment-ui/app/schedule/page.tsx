"use client";

import { useEffect, useState } from "react";
import TopBar from "@/components/layout/TopBar";
import ScheduleEditor from "@/components/schedule/ScheduleEditor";
import { getSchedule } from "@/lib/api";
import type { ScheduleRow } from "@/types";

export default function SchedulePage() {
  const [schedule, setSchedule] = useState<ScheduleRow[] | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    getSchedule()
      .then(setSchedule)
      .catch((ex: unknown) => {
        setError(ex instanceof Error ? ex.message : "Could not load schedule.");
        setSchedule([]);
      });
  }, []);

  return (
    <>
      <TopBar title="Weekly Schedule" />
      <main className="flex-1 overflow-y-auto bg-bg p-6">
        {schedule === null ? (
          <div className="max-w-3xl space-y-3 animate-fade-in">
            <div className="skeleton h-10 w-full max-w-md rounded-xl" />
            {Array.from({ length: 7 }).map((_, i) => (
              <div
                key={i}
                className="skeleton h-14 w-full rounded-xl"
                style={{ animationDelay: `${i * 60}ms` }}
              />
            ))}
          </div>
        ) : error ? (
          <div className="card p-4 text-sm text-danger">{error}</div>
        ) : (
          <ScheduleEditor initial={schedule} />
        )}
      </main>
    </>
  );
}
