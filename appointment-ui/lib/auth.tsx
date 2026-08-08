"use client";

import { createContext, useContext, useEffect, useState } from "react";
import { useRouter, usePathname } from "next/navigation";
import { Clinic, clearToken, getMe, getToken } from "@/lib/api";

type AuthState = {
  clinic: Clinic | null;
  ready: boolean;
  logout: () => void;
  setClinic: (c: Clinic) => void;
};

const AuthContext = createContext<AuthState>({
  clinic: null,
  ready: false,
  logout: () => {},
  setClinic: () => {},
});

export function useAuth() {
  return useContext(AuthContext);
}

/** Wraps the app: loads the current clinic from the token and redirects to
 * /login when unauthenticated. The login page itself is exempt. */
export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [clinic, setClinic] = useState<Clinic | null>(null);
  const [ready, setReady] = useState(false);
  const router = useRouter();
  const pathname = usePathname();

  function logout() {
    clearToken();
    setClinic(null);
    router.push("/login");
  }

  useEffect(() => {
    // Public / self-authenticating routes — never redirect or fetch clinic.
    if (
      pathname === "/login" ||
      pathname === "/signup" ||
      pathname === "/platform-admin" ||
      pathname.startsWith("/portal")
    ) {
      setReady(true);
      return;
    }
    const token = getToken();
    // On the landing page with no token, show the public landing page (no redirect).
    if (pathname === "/" && !token) {
      setReady(true);
      return;
    }
    if (!token) {
      router.replace("/login");
      return;
    }
    getMe()
      .then((c) => {
        setClinic(c);
        setReady(true);
      })
      .catch(() => {
        router.replace("/login");
      });
  }, [pathname, router]);

  if (!ready) {
    return (
      <div className="flex h-screen items-center justify-center" style={{ background: "var(--bg)" }}>
        <div className="flex flex-col items-center gap-4">
          <span
            className="flex h-12 w-12 items-center justify-center rounded-2xl text-white shadow-lg"
            style={{ background: "var(--brand-grad)", boxShadow: "0 4px 24px rgba(99,102,241,0.4)" }}
          >
            <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M22 12h-4l-3 9L9 3l-3 9H2"/>
            </svg>
          </span>
          <div className="flex items-center gap-1.5">
            {[0,1,2].map((i) => (
              <span key={i} className="h-1.5 w-1.5 animate-bounce rounded-full bg-primary"
                style={{ animationDelay: `${i * 150}ms` }} />
            ))}
          </div>
        </div>
      </div>
    );
  }

  return (
    <AuthContext.Provider value={{ clinic, ready, logout, setClinic }}>
      {children}
    </AuthContext.Provider>
  );
}
