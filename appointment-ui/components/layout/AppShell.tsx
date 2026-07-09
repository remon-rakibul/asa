"use client";

import { useEffect, useState } from "react";
import { usePathname } from "next/navigation";
import Sidebar from "@/components/layout/Sidebar";
import CommandPalette from "@/components/ui/CommandPalette";
import { useAuth } from "@/lib/auth";
import { MobileNavProvider, useMobileNav } from "@/lib/mobileNav";

const NO_SIDEBAR = ["/login", "/chat", "/platform-admin"];

function Backdrop() {
  const { open, setOpen } = useMobileNav();
  if (!open) return null;
  return (
    <div
      aria-hidden
      onClick={() => setOpen(false)}
      className="fixed inset-0 z-40 bg-black/50 backdrop-blur-sm lg:hidden animate-fade-in"
    />
  );
}

export default function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const { clinic } = useAuth();

  // Patient portal has its own auth + chrome; landing page has no sidebar when unauthenticated.
  const noSidebar =
    NO_SIDEBAR.includes(pathname) ||
    pathname.startsWith("/portal") ||
    (pathname === "/" && !clinic);

  if (noSidebar) {
    return (
      <div className="flex h-screen w-screen overflow-hidden bg-bg text-fg">
        {children}
      </div>
    );
  }
  return (
    <MobileNavProvider>
      <StaffShell>{children}</StaffShell>
    </MobileNavProvider>
  );
}

function StaffShell({ children }: { children: React.ReactNode }) {
  const [paletteOpen, setPaletteOpen] = useState(false);

  // Global ⌘/Ctrl-K opens the command palette.
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setPaletteOpen((v) => !v);
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  return (
    <div className="flex h-screen w-screen overflow-hidden bg-bg text-fg">
      <Backdrop />
      <Sidebar />
      <div id="main-content" tabIndex={-1} className="flex min-w-0 flex-1 flex-col overflow-hidden outline-none">
        {children}
      </div>
      <CommandPalette open={paletteOpen} onClose={() => setPaletteOpen(false)} />
    </div>
  );
}
