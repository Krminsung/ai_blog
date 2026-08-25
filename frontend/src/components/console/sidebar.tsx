"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { cn } from "@/lib/cn";
import { Logo } from "@/components/ui/logo";
import { isActive, NAV_GROUPS } from "@/components/console/nav-config";

export function Sidebar({
  onNavigate,
  className,
}: {
  /** Called after a link is chosen, so the mobile drawer can close itself. */
  onNavigate?: () => void;
  className?: string;
}) {
  const pathname = usePathname();

  return (
    <div
      className={cn(
        "flex h-full flex-col overflow-y-auto bg-[var(--surface-alt)]",
        className,
      )}
    >
      <div className="sticky top-0 z-1 bg-[var(--surface-alt)] px-5 pt-5 pb-3">
        <Link
          href="/console"
          onClick={onNavigate}
          className="text-[var(--text-primary)]"
        >
          <Logo />
        </Link>
      </div>

      <nav className="flex-1 px-3 pb-6" aria-label="콘솔">
        {NAV_GROUPS.map((group) => (
          <div key={group.title} className="mb-5">
            <p className="px-2 pb-1.5 text-[11px] font-semibold tracking-[0.06em] text-[var(--text-tertiary)] uppercase">
              {group.title}
            </p>
            <ul className="flex flex-col gap-0.5">
              {group.items.map((item) => {
                const active = isActive(pathname, item);
                return (
                  <li key={item.href}>
                    <Link
                      href={item.href}
                      onClick={onNavigate}
                      aria-current={active ? "page" : undefined}
                      className={cn(
                        "block rounded-[9px] px-2.5 py-[7px] text-[13.5px] transition-colors duration-150",
                        active
                          ? "bg-[var(--surface-raised)] font-medium text-[var(--text-primary)] shadow-[0_1px_2px_rgba(0,0,0,.06)]"
                          : "text-[var(--text-secondary)] hover:bg-[color-mix(in_srgb,var(--surface-raised)_60%,transparent)] hover:text-[var(--text-primary)]",
                      )}
                    >
                      {item.label}
                    </Link>
                  </li>
                );
              })}
            </ul>
          </div>
        ))}
      </nav>
    </div>
  );
}
