"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
} from "react";
import { CheckCircle2, AlertCircle, Info, X } from "lucide-react";

type ToastKind = "success" | "error" | "info";
type Toast = { id: number; kind: ToastKind; message: string };

type ToastApi = {
  success: (message: string) => void;
  error: (message: string) => void;
  info: (message: string) => void;
};

const ToastContext = createContext<ToastApi>({
  success: () => {},
  error: () => {},
  info: () => {},
});

/** Imperative toast API: `const toast = useToast(); toast.success("Saved")`. */
export function useToast() {
  return useContext(ToastContext);
}

const ICONS: Record<ToastKind, React.ElementType> = {
  success: CheckCircle2,
  error: AlertCircle,
  info: Info,
};

const ACCENT: Record<ToastKind, string> = {
  success: "var(--success)",
  error: "var(--danger)",
  info: "var(--info)",
};

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const idRef = useRef(0);

  const dismiss = useCallback((id: number) => {
    setToasts((t) => t.filter((x) => x.id !== id));
  }, []);

  const push = useCallback((kind: ToastKind, message: string) => {
    const id = ++idRef.current;
    setToasts((t) => [...t, { id, kind, message }]);
    setTimeout(() => dismiss(id), 4000);
  }, [dismiss]);

  const api: ToastApi = {
    success: (m) => push("success", m),
    error: (m) => push("error", m),
    info: (m) => push("info", m),
  };

  return (
    <ToastContext.Provider value={api}>
      {children}
      <div
        className="pointer-events-none fixed bottom-4 right-4 z-[100] flex w-[calc(100vw-2rem)] max-w-sm flex-col gap-2"
        aria-live="polite"
        aria-atomic="false"
      >
        {toasts.map((t) => (
          <ToastItem key={t.id} toast={t} onDismiss={() => dismiss(t.id)} />
        ))}
      </div>
    </ToastContext.Provider>
  );
}

function ToastItem({ toast, onDismiss }: { toast: Toast; onDismiss: () => void }) {
  const Icon = ICONS[toast.kind];
  const [leaving, setLeaving] = useState(false);

  // Play the exit animation, then remove.
  useEffect(() => {
    const t = setTimeout(() => setLeaving(true), 3700);
    return () => clearTimeout(t);
  }, []);

  return (
    <div
      role="status"
      className={`glass pointer-events-auto flex items-start gap-3 rounded-xl border border-border p-3.5 shadow-lg ${
        leaving ? "animate-fade-out" : "animate-slide-up"
      }`}
    >
      <span
        className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center"
        style={{ color: ACCENT[toast.kind] }}
      >
        <Icon size={18} />
      </span>
      <p className="flex-1 text-sm leading-snug text-fg" dir="auto">
        {toast.message}
      </p>
      <button
        onClick={onDismiss}
        aria-label="Dismiss"
        className="-mr-1 -mt-1 flex h-6 w-6 shrink-0 items-center justify-center rounded-lg text-faint transition-colors hover:bg-surface-3 hover:text-fg"
      >
        <X size={14} />
      </button>
    </div>
  );
}
