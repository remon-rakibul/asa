"use client";

import { Suspense } from "react";
import { useSearchParams } from "next/navigation";
import ChatPanel from "@/components/portal/ChatPanel";

/** Full-screen chat page. All chat logic lives in ChatPanel (shared with the
 *  floating widget); this wrapper only parses the deep-link URL params
 *  (doctor page "book with AI", HospitalBrowse) into per-turn context. */
function BookPageInner() {
  const params = useSearchParams();
  const clinicId = params.get("clinic") ? Number(params.get("clinic")) : undefined;
  const doctorId = params.get("doctorId") ? Number(params.get("doctorId")) : undefined;

  return (
    <ChatPanel
      variant="page"
      clinicId={clinicId}
      doctorId={doctorId}
      doctorMeta={{
        name: params.get("doctor") ?? undefined,
        degrees: params.get("degrees") ?? undefined,
        specialty: params.get("specialty") ?? undefined,
        fee: params.get("fee") ?? undefined,
        department: params.get("department") ?? undefined,
        hospital: params.get("hospital") ?? undefined,
      }}
    />
  );
}

export default function PortalBookPage() {
  return (
    <Suspense fallback={
      <div className="flex h-screen w-full items-center justify-center bg-bg">
        <div className="flex gap-1.5">
          {[0,1,2].map(i => (
            <span key={i} className="h-2 w-2 animate-bounce rounded-full bg-primary"
              style={{ animationDelay: `${i * 150}ms` }} />
          ))}
        </div>
      </div>
    }>
      <BookPageInner />
    </Suspense>
  );
}
