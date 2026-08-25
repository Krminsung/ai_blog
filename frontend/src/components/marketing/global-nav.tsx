"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState, useSyncExternalStore } from "react";

import { cn } from "@/lib/cn";
import { Logo } from "@/components/ui/logo";
import { ThemeToggle } from "@/components/ui/theme";
import { useSession } from "@/lib/auth/session-provider";

/** Window scroll, read as an external store so no effect writes state. */
function subscribeScroll(listener: () => void): () => void {
  window.addEventListener("scroll", listener, { passive: true });
  return () => window.removeEventListener("scroll", listener);
}

const LINKS = [
  { href: "/product", label: "제품" },
  { href: "/workflow", label: "워크플로" },
  { href: "/security", label: "보안" },
  { href: "/pricing", label: "요금제" },
];

/**
 * The 48px translucent bar. It stays transparent over the hero and picks up a
 * hairline once the page scrolls, the way apple.com's global nav does.
 */
export function GlobalNav() {
  const pathname = usePathname();
  const { status } = useSession();
  const scrolled = useSyncExternalStore(
    subscribeScroll,
    () => window.scrollY > 8,
    () => false,
  );

  // The sheet is remembered against the route it was opened on, so navigating
  // closes it without an effect that resets state.
  const [menuOpenAt, setMenuOpenAt] = useState<string | null>(null);
  const menuOpen = menuOpenAt === pathname;
  const setMenuOpen = (open: boolean) =>
    setMenuOpenAt(open ? pathname : null);

  // Lock the page behind the open sheet.
  useEffect(() => {
    if (!menuOpen) return;
    const { overflow } = document.body.style;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = overflow;
    };
  }, [menuOpen]);

  return (
    <header
      className={cn(
        "sticky top-0 z-50 transition-shadow duration-300",
        "chrome-blur",
        scrolled && "hairline-b",
      )}
    >
      <nav
        className="shell-wide flex h-12 items-center justify-between gap-6"
        aria-label="주요"
      >
        <Link
          href="/"
          className="shrink-0 text-[var(--text-primary)]"
          aria-label="BlogOps AI 홈"
        >
          <Logo />
        </Link>

        <ul className="hidden items-center gap-8 md:flex">
          {LINKS.map((link) => (
            <li key={link.href}>
              <Link
                href={link.href}
                className={cn(
                  "text-[12px] tracking-[-0.01em] transition-opacity duration-200",
                  pathname === link.href
                    ? "text-[var(--text-primary)]"
                    : "text-[var(--text-secondary)] hover:text-[var(--text-primary)]",
                )}
              >
                {link.label}
              </Link>
            </li>
          ))}
        </ul>

        <div className="flex items-center gap-3">
          <ThemeToggle className="hidden sm:inline-flex" />
          {status === "authenticated" ? (
            <Link
              href="/console"
              className="rounded-full bg-[var(--accent)] px-4 py-1.5 text-[12px] text-[var(--accent-contrast)] transition-opacity hover:opacity-90"
            >
              콘솔 열기
            </Link>
          ) : (
            <>
              <Link
                href="/login"
                className="hidden text-[12px] text-[var(--text-secondary)] transition-colors hover:text-[var(--text-primary)] sm:inline"
              >
                로그인
              </Link>
              <Link
                href="/signup"
                className="rounded-full bg-[var(--accent)] px-4 py-1.5 text-[12px] text-[var(--accent-contrast)] transition-opacity hover:opacity-90"
              >
                시작하기
              </Link>
            </>
          )}
          <button
            type="button"
            className="md:hidden"
            aria-expanded={menuOpen}
            aria-label="메뉴 열기"
            onClick={() => setMenuOpen(!menuOpen)}
          >
            <span className="flex size-6 flex-col items-center justify-center gap-[5px]">
              <span
                className={cn(
                  "block h-px w-4 bg-current transition-transform duration-300",
                  menuOpen && "translate-y-[3px] rotate-45",
                )}
              />
              <span
                className={cn(
                  "block h-px w-4 bg-current transition-transform duration-300",
                  menuOpen && "-translate-y-[3px] -rotate-45",
                )}
              />
            </span>
          </button>
        </div>
      </nav>

      {menuOpen ? (
        <div className="chrome-blur hairline-t md:hidden">
          <ul className="shell flex flex-col py-4">
            {LINKS.map((link) => (
              <li key={link.href}>
                <Link
                  href={link.href}
                  className="block border-b border-[var(--hairline-soft)] py-3 text-[19px] font-medium tracking-[-0.02em]"
                >
                  {link.label}
                </Link>
              </li>
            ))}
            <li>
              <Link
                href={status === "authenticated" ? "/console" : "/login"}
                className="block py-3 text-[19px] font-medium tracking-[-0.02em]"
              >
                {status === "authenticated" ? "콘솔 열기" : "로그인"}
              </Link>
            </li>
            <li className="pt-3">
              <ThemeToggle />
            </li>
          </ul>
        </div>
      ) : null}
    </header>
  );
}
