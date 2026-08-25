"use client";

import type { ReactNode } from "react";

import { SessionProvider } from "@/lib/auth/session-provider";
import { ThemeProvider } from "@/components/ui/theme";
import { ToastProvider } from "@/components/ui/toast";

/**
 * Client-side providers shared by every route. Session sits inside Toast so
 * auth transitions (expired session, workspace switch) can raise toasts.
 */
export function Providers({ children }: { children: ReactNode }) {
  return (
    <ThemeProvider>
      <ToastProvider>
        <SessionProvider>{children}</SessionProvider>
      </ToastProvider>
    </ThemeProvider>
  );
}
