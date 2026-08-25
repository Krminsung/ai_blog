"use client";

import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState, type ReactNode } from "react";

import { cn } from "@/lib/cn";
import { Sidebar } from "@/components/console/sidebar";
import { Topbar } from "@/components/console/topbar";
import { Spinner } from "@/components/ui/feedback";
import { useSession } from "@/lib/auth/session-provider";

/**
 * Console frame: a persistent rail on wide screens, an overlay drawer below
 * `lg`. Also the auth gate — anonymous visitors are bounced to /login with a
 * `next` param so they land back where they were headed.
 */
export function ConsoleShell({ children }: { children: ReactNode }) {
  const { status } = useSession();
  const router = useRouter();
  const pathname = usePathname();
  const [drawerOpen, setDrawerOpen] = useState(false);

  useEffect(() => {
    if (status !== "anonymous") return;
    const next = encodeURIComponent(pathname);
    router.replace(`/login?next=${next}`);
  }, [status, pathname, router]);

  useEffect(() => {
    if (!drawerOpen) return;
    const { overflow } = document.body.style;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = overflow;
    };
  }, [drawerOpen]);

  if (status !== "authenticated") {
    return (
      <div className="grid min-h-dvh place-items-center">
        <Spinner className="size-6" />
      </div>
    );
  }

  return (
    <div className="flex min-h-dvh">
      <aside className="hidden w-[236px] shrink-0 border-r border-[var(--hairline-soft)] lg:block">
        <div className="sticky top-0 h-dvh">
          <Sidebar />
        </div>
      </aside>

      {drawerOpen ? (
        <div className="fixed inset-0 z-60 lg:hidden">
          <button
            type="button"
            aria-label="메뉴 닫기"
            onClick={() => setDrawerOpen(false)}
            className="absolute inset-0 bg-[var(--overlay)]"
          />
          <div
            className={cn(
              "relative h-full w-[264px] max-w-[82vw] shadow-[var(--shadow-float)]",
              "motion-safe:animate-[drawer-in_.28s_var(--ease-apple)]",
            )}
          >
            <Sidebar onNavigate={() => setDrawerOpen(false)} />
          </div>
          <style>{`
            @keyframes drawer-in {
              from { transform: translateX(-100%); }
              to   { transform: none; }
            }
          `}</style>
        </div>
      ) : null}

      <div className="flex min-w-0 flex-1 flex-col">
        <Topbar onOpenNav={() => setDrawerOpen(true)} />
        <main id="main" className="flex-1 px-4 py-6 sm:px-6 sm:py-8">
          {children}
        </main>
      </div>
    </div>
  );
}
