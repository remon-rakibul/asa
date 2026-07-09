"use client";

import { createContext, useContext, useEffect, useState } from "react";
import { useRouter, usePathname } from "next/navigation";
import {
  PatientAccount,
  clearPatientToken,
  getPatientMe,
  getPatientToken,
} from "@/lib/api";

type PatientAuthState = {
  account: PatientAccount | null;
  ready: boolean;
  logout: () => void;
  setAccount: (a: PatientAccount) => void;
};

const PatientAuthContext = createContext<PatientAuthState>({
  account: null,
  ready: false,
  logout: () => {},
  setAccount: () => {},
});

export function usePatientAuth() {
  return useContext(PatientAuthContext);
}

// Pages inside /portal that do not require a logged-in patient.
const PUBLIC = ["/portal/login", "/portal/signup", "/portal/forgot"];

/** Guards the patient portal: loads the account from the patient token and
 *  redirects to /portal/login when unauthenticated. */
export function PatientAuthProvider({ children }: { children: React.ReactNode }) {
  const [account, setAccount] = useState<PatientAccount | null>(null);
  const [ready, setReady] = useState(false);
  const router = useRouter();
  const pathname = usePathname();

  function logout() {
    clearPatientToken();
    setAccount(null);
    router.push("/portal/login");
  }

  useEffect(() => {
    if (PUBLIC.includes(pathname)) {
      setReady(true);
      return;
    }
    if (!getPatientToken()) {
      router.replace("/portal/login");
      return;
    }
    getPatientMe()
      .then((a) => {
        setAccount(a);
        setReady(true);
      })
      .catch(() => router.replace("/portal/login"));
  }, [pathname, router]);

  if (!ready) {
    return (
      <div className="flex h-screen items-center justify-center bg-bg">
        <div className="flex items-center gap-1.5">
          {[0, 1, 2].map((i) => (
            <span
              key={i}
              className="h-2 w-2 animate-bounce rounded-full bg-primary"
              style={{ animationDelay: `${i * 150}ms` }}
            />
          ))}
        </div>
      </div>
    );
  }

  return (
    <PatientAuthContext.Provider value={{ account, ready, logout, setAccount }}>
      {children}
    </PatientAuthContext.Provider>
  );
}
