"use client";

import { createContext, useContext, useEffect, useState } from "react";
import { usePathname } from "next/navigation";

type MobileNavState = {
  open: boolean;
  setOpen: (v: boolean) => void;
  toggle: () => void;
};

const MobileNavContext = createContext<MobileNavState>({
  open: false,
  setOpen: () => {},
  toggle: () => {},
});

export function useMobileNav() {
  return useContext(MobileNavContext);
}

/** Holds the mobile sidebar-drawer open state, shared between the TopBar
 * hamburger (rendered per-page) and the Sidebar (rendered by AppShell).
 * Auto-closes on route change so a tapped nav link dismisses the drawer. */
export function MobileNavProvider({ children }: { children: React.ReactNode }) {
  const [open, setOpen] = useState(false);
  const pathname = usePathname();

  useEffect(() => {
    setOpen(false);
  }, [pathname]);

  // Lock body scroll while the drawer is open on mobile.
  useEffect(() => {
    if (open) {
      const prev = document.body.style.overflow;
      document.body.style.overflow = "hidden";
      return () => {
        document.body.style.overflow = prev;
      };
    }
  }, [open]);

  return (
    <MobileNavContext.Provider value={{ open, setOpen, toggle: () => setOpen(!open) }}>
      {children}
    </MobileNavContext.Provider>
  );
}
