"use client";

import { Moon, Sun } from "lucide-react";
import { useTheme } from "@/lib/theme";

export default function ThemeToggle() {
  const { theme, toggle } = useTheme();
  const dark = theme === "dark";

  return (
    <button
      onClick={toggle}
      aria-label={dark ? "Switch to light mode" : "Switch to dark mode"}
      title={dark ? "Light mode" : "Dark mode"}
      className="relative flex h-8 w-14 items-center rounded-full transition-all duration-300"
      style={{
        background: dark
          ? "linear-gradient(135deg, #312e81, #4c1d95)"
          : "linear-gradient(135deg, #fbbf24, #f59e0b)",
        boxShadow: dark
          ? "0 2px 8px rgba(99,102,241,0.4), inset 0 1px 0 rgba(255,255,255,0.1)"
          : "0 2px 8px rgba(251,191,36,0.5), inset 0 1px 0 rgba(255,255,255,0.3)",
      }}
    >
      {/* Track icons */}
      <span className="absolute left-1.5 top-1/2 -translate-y-1/2 opacity-70 transition-opacity">
        <Sun size={10} className="text-yellow-200" />
      </span>
      <span className="absolute right-1.5 top-1/2 -translate-y-1/2 opacity-70 transition-opacity">
        <Moon size={10} className="text-indigo-200" />
      </span>

      {/* Thumb */}
      <span
        className="absolute flex h-6 w-6 items-center justify-center rounded-full bg-white shadow-md transition-all duration-300"
        style={{
          left: dark ? "calc(100% - 26px)" : "2px",
          boxShadow: "0 1px 4px rgba(0,0,0,0.2), 0 0 0 1px rgba(0,0,0,0.06)",
        }}
      >
        {dark
          ? <Moon size={12} className="text-indigo-600" />
          : <Sun size={12} className="text-amber-500" />
        }
      </span>
    </button>
  );
}
