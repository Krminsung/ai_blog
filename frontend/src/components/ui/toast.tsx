"use client";

import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useState,
} from "react";
import { createPortal } from "react-dom";

import { cn } from "@/lib/cn";
import { useIsMounted } from "@/lib/hooks/use-mounted";

interface Toast {
  id: number;
  message: string;
  tone: "neutral" | "positive" | "critical";
}

interface ToastContextValue {
  notify: (message: string, tone?: Toast["tone"]) => void;
}

const ToastContext = createContext<ToastContextValue | null>(null);

let nextId = 1;

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  // The portal target only exists in the browser, so nothing is emitted until
  // after hydration.
  const mounted = useIsMounted();

  const notify = useCallback(
    (message: string, tone: Toast["tone"] = "neutral") => {
      const id = nextId++;
      setToasts((current) => [...current, { id, message, tone }]);
      window.setTimeout(
        () => setToasts((current) => current.filter((item) => item.id !== id)),
        4000,
      );
    },
    [],
  );

  const value = useMemo(() => ({ notify }), [notify]);

  return (
    <ToastContext.Provider value={value}>
      {children}
      {mounted
        ? createPortal(
            <div
              className="pointer-events-none fixed inset-x-0 bottom-6 z-200 flex flex-col items-center gap-2 px-4"
              aria-live="polite"
              aria-atomic="false"
            >
              {toasts.map((toast) => (
                <div
                  key={toast.id}
                  className={cn(
                    "pointer-events-auto max-w-md rounded-full px-5 py-3 text-[14px]",
                    "shadow-[var(--shadow-float)] backdrop-blur-xl",
                    "motion-safe:animate-[toast-in_.3s_var(--ease-apple)]",
                    toast.tone === "critical"
                      ? "bg-[var(--critical)] text-white"
                      : toast.tone === "positive"
                        ? "bg-[var(--positive)] text-white"
                        : "bg-[var(--surface-inverse)] text-[var(--text-inverse)]",
                  )}
                >
                  {toast.message}
                </div>
              ))}
              <style>{`
                @keyframes toast-in {
                  from { opacity: 0; transform: translateY(10px); }
                  to   { opacity: 1; transform: none; }
                }
              `}</style>
            </div>,
            document.body,
          )
        : null}
    </ToastContext.Provider>
  );
}

export function useToast(): ToastContextValue {
  const context = useContext(ToastContext);
  if (!context) throw new Error("useToast must be used inside <ToastProvider>");
  return context;
}
