"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  LayoutDashboard,
  CalendarDays,
  Clock,
  MessagesSquare,
  MessageSquare,
  Plug,
  Settings,
  Stethoscope,
  Sparkles,
  Building2,
  Users,
  Ticket,
  ShieldCheck,
  BarChart3,
  X,
  Send,
} from "lucide-react";
import { useAuth } from "@/lib/auth";
import { useMobileNav } from "@/lib/mobileNav";
import ThemeToggle from "@/components/layout/ThemeToggle";

const NAV = [
  { href: "/",             label: "Dashboard",     icon: LayoutDashboard, roles: null },
  { href: "/appointments", label: "Appointments",  icon: CalendarDays,    roles: null },
  { href: "/schedule",     label: "Schedule",      icon: Clock,           roles: null },
  { href: "/patients",     label: "Patients",      icon: Users,           roles: null },
  { href: "/queue",        label: "Queue",         icon: Ticket,          roles: null },
  { href: "/conversations",label: "Conversations", icon: MessagesSquare,  roles: null },
  { href: "/messages",     label: "Messages",      icon: Send,            roles: null },
  { href: "/reports",      label: "Reports",       icon: BarChart3,       roles: ["platform_admin", "hospital_admin", "dept_head"] },
  { href: "/chat",         label: "Test Chat",     icon: MessageSquare,   roles: ["platform_admin"] },
  { href: "/hospitals",    label: "Hospitals",     icon: Building2,       roles: ["platform_admin"] },
  { href: "/audit",        label: "Audit Log",     icon: ShieldCheck,     roles: ["platform_admin", "hospital_admin"] },
  { href: "/integrations", label: "Integrations",  icon: Plug,            roles: null },
  { href: "/settings",     label: "Settings",      icon: Settings,        roles: null },
];

export default function Sidebar() {
  const pathname = usePathname();
  const { clinic } = useAuth();
  const { open, setOpen } = useMobileNav();

  return (
    <aside
      className={`fixed inset-y-0 left-0 z-50 flex w-64 shrink-0 flex-col overflow-hidden transition-transform duration-300 lg:relative lg:z-auto lg:translate-x-0 ${
        open ? "translate-x-0 shadow-2xl" : "-translate-x-full"
      } lg:shadow-none`}
      style={{
        background: "var(--surface)",
        borderRight: "1px solid var(--border)",
      }}
    >
      {/* Ambient glow blob */}
      <div
        aria-hidden
        className="pointer-events-none absolute -left-10 -top-10 h-48 w-48 rounded-full opacity-30 animate-orb"
        style={{
          background: "radial-gradient(circle, rgba(99,102,241,0.4) 0%, transparent 70%)",
          filter: "blur(30px)",
        }}
      />

      {/* Logo / brand */}
      <div className="relative flex h-16 items-center gap-3 px-5">
        <span
          className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl text-white shadow-lg"
          style={{ background: "var(--brand-grad)", boxShadow: "0 4px 14px rgba(99,102,241,0.45)" }}
        >
          <Stethoscope size={18} />
        </span>
        <div className="min-w-0 flex-1">
          <div className="truncate text-sm font-bold text-fg leading-tight">
            {clinic?.name ?? "Clinic Console"}
          </div>
          <div className="flex items-center gap-1 text-xs text-faint">
            <Sparkles size={10} className="text-primary" />
            AI Receptionist
          </div>
        </div>
        {/* Close drawer (mobile only) */}
        <button
          onClick={() => setOpen(false)}
          aria-label="Close menu"
          className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg text-faint hover:bg-surface-3 hover:text-fg transition-colors lg:hidden"
        >
          <X size={18} />
        </button>
      </div>

      {/* Divider */}
      <div className="divider mx-4" />

      {/* Navigation */}
      <nav className="flex-1 space-y-0.5 px-3 py-3 overflow-y-auto">
        {NAV.filter(({ roles }) => !roles || roles.includes(clinic?.role ?? "")).map(({ href, label, icon: Icon }, i) => {
          const active = href === "/" ? pathname === "/" : pathname.startsWith(href);
          return (
            <Link
              key={href}
              href={href}
              className={`group relative flex items-center gap-3 rounded-xl px-3 py-2.5 text-sm font-medium transition-all duration-200 animate-slide-left`}
              style={{ animationDelay: `${i * 40}ms` }}
            >
              {/* Active background */}
              {active && (
                <span
                  className="absolute inset-0 rounded-xl"
                  style={{
                    background: "var(--brand-soft)",
                    boxShadow: "inset 0 0 0 1px color-mix(in srgb, var(--primary) 25%, transparent)",
                  }}
                />
              )}

              {/* Left accent bar */}
              {active && (
                <span
                  className="absolute left-0 top-1/2 h-5 w-0.5 -translate-y-1/2 rounded-r"
                  style={{ background: "var(--brand-grad)" }}
                />
              )}

              {/* Icon */}
              <span
                className={`relative z-10 flex h-7 w-7 shrink-0 items-center justify-center rounded-lg transition-all duration-200 ${
                  active
                    ? "text-primary"
                    : "text-faint group-hover:text-muted"
                }`}
                style={
                  active
                    ? {
                        background: "var(--brand-soft)",
                        boxShadow: "0 0 12px rgba(99,102,241,0.25)",
                      }
                    : {}
                }
              >
                <Icon size={16} />
              </span>

              {/* Label */}
              <span
                className={`relative z-10 transition-colors duration-200 ${
                  active ? "text-primary font-semibold" : "text-muted group-hover:text-fg"
                }`}
              >
                {label}
              </span>

              {/* Hover bg */}
              {!active && (
                <span className="absolute inset-0 rounded-xl bg-surface-3 opacity-0 transition-opacity duration-150 group-hover:opacity-100" />
              )}
            </Link>
          );
        })}
      </nav>

      {/* Bottom section */}
      <div className="relative mx-3 mb-3 mt-1 rounded-xl px-3 py-3" style={{ background: "var(--brand-soft)", border: "1px solid color-mix(in srgb, var(--primary) 20%, var(--border))" }}>
        <div className="flex items-center justify-between">
          <div>
            <div className="text-xs font-medium text-fg">Dark mode</div>
            <div className="text-xs text-faint">Toggle theme</div>
          </div>
          <ThemeToggle />
        </div>
      </div>
    </aside>
  );
}
