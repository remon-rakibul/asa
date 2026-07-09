"use client";

import { useEffect, useRef, useState } from "react";
import { format } from "date-fns";
import Link from "next/link";
import { LogOut, Calendar, Menu, Settings, Sun, Moon, ChevronDown } from "lucide-react";
import { useAuth } from "@/lib/auth";
import { useTheme } from "@/lib/theme";
import { useMobileNav } from "@/lib/mobileNav";

function initials(name: string) {
  return name.trim().split(/\s+/).slice(0, 2).map((w) => w[0]).join("").toUpperCase() || "?";
}

export default function TopBar({ title }: { title: string }) {
  const { clinic, logout } = useAuth();
  const { theme, toggle: toggleTheme } = useTheme();
  const { toggle } = useMobileNav();
  const [dateStr, setDateStr] = useState<string | null>(null);
  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    setDateStr(format(new Date(), "EEEE, d MMMM yyyy"));
  }, []);

  // Close the account menu on outside click / Esc.
  useEffect(() => {
    if (!menuOpen) return;
    function onDown(e: MouseEvent) {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) setMenuOpen(false);
    }
    function onKey(e: KeyboardEvent) { if (e.key === "Escape") setMenuOpen(false); }
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => { document.removeEventListener("mousedown", onDown); document.removeEventListener("keydown", onKey); };
  }, [menuOpen]);

  return (
    <header
      className="relative z-30 flex h-16 shrink-0 items-center justify-between px-6 animate-fade-in"
      style={{
        background: "color-mix(in srgb, var(--surface) 80%, transparent)",
        backdropFilter: "blur(20px)",
        WebkitBackdropFilter: "blur(20px)",
        borderBottom: "1px solid var(--border)",
      }}
    >
      {/* Title */}
      <div className="flex items-center gap-2 min-w-0">
        {/* Hamburger (mobile only) */}
        <button
          onClick={toggle}
          aria-label="Open menu"
          className="-ml-2 flex h-9 w-9 shrink-0 items-center justify-center rounded-lg text-muted hover:bg-surface-3 hover:text-fg transition-colors lg:hidden"
        >
          <Menu size={20} />
        </button>
        <div className="flex items-baseline gap-3 min-w-0">
          <h1 className="truncate text-lg font-bold text-fg tracking-tight">{title}</h1>
          {clinic && (
            <span className="hidden text-sm text-faint sm:inline">· {clinic.name}</span>
          )}
        </div>
      </div>

      {/* Right side */}
      <div className="flex items-center gap-2">
        {/* Date chip */}
        {dateStr && (
          <div className="badge badge-surface hidden items-center gap-2 sm:inline-flex">
            <Calendar size={12} className="text-primary" />
            {dateStr}
          </div>
        )}

        {/* Account menu */}
        <div className="relative" ref={menuRef}>
          <button
            onClick={() => setMenuOpen((v) => !v)}
            aria-haspopup="menu"
            aria-expanded={menuOpen}
            className="flex items-center gap-2 rounded-xl px-1.5 py-1.5 transition-colors hover:bg-surface-3"
          >
            <span
              className="flex h-8 w-8 items-center justify-center rounded-lg text-xs font-bold text-white"
              style={{ background: "var(--brand-grad)" }}
            >
              {initials(clinic?.name ?? "?")}
            </span>
            <ChevronDown size={14} className="hidden text-faint sm:block" />
          </button>

          {menuOpen && (
            <div
              role="menu"
              className="glass-strong absolute right-0 top-full z-50 mt-2 w-56 overflow-hidden rounded-xl border border-border shadow-lg animate-slide-down"
            >
              <div className="border-b border-border px-4 py-3">
                <div className="truncate text-sm font-semibold text-fg">{clinic?.name ?? "Account"}</div>
                {clinic?.role && (
                  <div className="mt-0.5 text-xs capitalize text-faint">{clinic.role.replace("_", " ")}</div>
                )}
              </div>
              <div className="p-1.5">
                <button
                  onClick={() => { toggleTheme(); }}
                  role="menuitem"
                  className="flex w-full items-center gap-2.5 rounded-lg px-3 py-2 text-sm text-muted transition-colors hover:bg-surface-3 hover:text-fg"
                >
                  {theme === "dark" ? <Sun size={15} /> : <Moon size={15} />}
                  {theme === "dark" ? "Light mode" : "Dark mode"}
                </button>
                <Link
                  href="/settings"
                  role="menuitem"
                  onClick={() => setMenuOpen(false)}
                  className="flex w-full items-center gap-2.5 rounded-lg px-3 py-2 text-sm text-muted transition-colors hover:bg-surface-3 hover:text-fg"
                >
                  <Settings size={15} /> Settings
                </Link>
                <button
                  onClick={logout}
                  role="menuitem"
                  className="flex w-full items-center gap-2.5 rounded-lg px-3 py-2 text-sm text-danger transition-colors hover:bg-danger/10"
                >
                  <LogOut size={15} /> Sign out
                </button>
              </div>
            </div>
          )}
        </div>
      </div>
    </header>
  );
}
