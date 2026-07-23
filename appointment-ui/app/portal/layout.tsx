"use client";

import { PatientAuthProvider } from "@/lib/patientAuth";
import { LangProvider } from "@/lib/i18n";
import FloatingAssistant from "@/components/portal/FloatingAssistant";

export default function PortalLayout({ children }: { children: React.ReactNode }) {
  return (
    <PatientAuthProvider>
      <LangProvider>
        {children}
        <FloatingAssistant />
      </LangProvider>
    </PatientAuthProvider>
  );
}
