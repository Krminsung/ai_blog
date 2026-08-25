"use client";

import { useCallback, useEffect, useSyncExternalStore } from "react";

import { cn } from "@/lib/cn";

export type ThemePreference = "light" | "dark" | "system";

const STORAGE_KEY = "blogops.theme";

/**
 * Inline script that runs before first paint so the page never flashes the
 * wrong theme. Kept as a string because it must execute synchronously in
 * <head>, ahead of hydration.
 */
export const THEME_BOOTSTRAP = `(function(){try{
var p=localStorage.getItem(${JSON.stringify(STORAGE_KEY)})||"system";
var d=p==="dark"||(p==="system"&&matchMedia("(prefers-color-scheme: dark)").matches);
document.documentElement.classList.toggle("dark",d);
}catch(e){}})();`;

/* ---------------------------------------------------------------------------
 * The preference lives in localStorage, not React state. Components read it
 * through `useSyncExternalStore` so the value is correct on the very first
 * client render and stays in sync across tabs — no hydration effect needed.
 * ------------------------------------------------------------------------- */

const listeners = new Set<() => void>();
let cachedPreference: ThemePreference | null = null;

function readPreference(): ThemePreference {
  if (cachedPreference !== null) return cachedPreference;
  try {
    const stored = window.localStorage.getItem(STORAGE_KEY);
    cachedPreference =
      stored === "light" || stored === "dark" ? stored : "system";
  } catch {
    cachedPreference = "system";
  }
  return cachedPreference;
}

function emit(): void {
  listeners.forEach((listener) => listener());
}

function subscribePreference(listener: () => void): () => void {
  listeners.add(listener);
  const onStorage = (event: StorageEvent) => {
    if (event.key !== null && event.key !== STORAGE_KEY) return;
    cachedPreference = null;
    emit();
  };
  window.addEventListener("storage", onStorage);
  return () => {
    listeners.delete(listener);
    window.removeEventListener("storage", onStorage);
  };
}

const DARK_QUERY = "(prefers-color-scheme: dark)";

function subscribeSystem(listener: () => void): () => void {
  const media = window.matchMedia(DARK_QUERY);
  media.addEventListener("change", listener);
  return () => media.removeEventListener("change", listener);
}

function systemIsDark(): boolean {
  return window.matchMedia(DARK_QUERY).matches;
}

/** Reads the stored preference and the OS setting it may defer to. */
export function useTheme() {
  const preference = useSyncExternalStore(
    subscribePreference,
    readPreference,
    // Server render has no storage; the bootstrap script has already applied
    // the real theme to <html> by the time hydration runs.
    () => "system" as ThemePreference,
  );

  const prefersDark = useSyncExternalStore(
    subscribeSystem,
    systemIsDark,
    () => false,
  );

  const resolved: "light" | "dark" =
    preference === "system" ? (prefersDark ? "dark" : "light") : preference;

  const setPreference = useCallback((next: ThemePreference) => {
    cachedPreference = next;
    try {
      window.localStorage.setItem(STORAGE_KEY, next);
    } catch {
      // Private browsing or storage full — the in-memory value still applies.
    }
    const dark = next === "dark" || (next === "system" && systemIsDark());
    document.documentElement.classList.toggle("dark", dark);
    emit();
  }, []);

  return { preference, resolved, setPreference };
}

/**
 * Kept as a component so the app tree has one obvious place where theming is
 * mounted; the state itself lives in the external store above.
 */
export function ThemeProvider({ children }: { children: React.ReactNode }) {
  return (
    <>
      <ThemeSync />
      {children}
    </>
  );
}

/**
 * Keeps `<html class="dark">` aligned with the resolved theme. The bootstrap
 * script sets it before first paint; this only has to catch later changes,
 * such as the OS flipping while the preference is "system".
 */
export function ThemeSync() {
  const { resolved } = useTheme();
  useEffect(() => {
    document.documentElement.classList.toggle("dark", resolved === "dark");
  }, [resolved]);
  return null;
}

const OPTIONS: { value: ThemePreference; label: string; glyph: string }[] = [
  { value: "light", label: "라이트", glyph: "☀" },
  { value: "dark", label: "다크", glyph: "☾" },
  { value: "system", label: "시스템", glyph: "◐" },
];

export function ThemeToggle({ className }: { className?: string }) {
  const { preference, setPreference } = useTheme();
  return (
    <div
      className={cn(
        "inline-flex items-center gap-0.5 rounded-full bg-[var(--surface-alt)] p-0.5",
        className,
      )}
      role="group"
      aria-label="테마 선택"
    >
      {OPTIONS.map((option) => (
        <button
          key={option.value}
          type="button"
          onClick={() => setPreference(option.value)}
          aria-pressed={preference === option.value}
          title={option.label}
          className={cn(
            "grid size-7 place-items-center rounded-full text-[12px] transition-all duration-200",
            preference === option.value
              ? "bg-[var(--surface-raised)] text-[var(--text-primary)] shadow-[0_1px_3px_rgba(0,0,0,.12)]"
              : "text-[var(--text-tertiary)] hover:text-[var(--text-primary)]",
          )}
        >
          <span aria-hidden>{option.glyph}</span>
          <span className="sr-only">{option.label}</span>
        </button>
      ))}
    </div>
  );
}
