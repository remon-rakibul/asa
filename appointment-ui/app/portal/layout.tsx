"use client";

import { PatientAuthProvider } from "@/lib/patientAuth";

export default function PortalLayout({ children }: { children: React.ReactNode }) {
  return <PatientAuthProvider>{children}</PatientAuthProvider>;
}
