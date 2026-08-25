import Link from "next/link";

import { Logo } from "@/components/ui/logo";
import { ThemeToggle } from "@/components/ui/theme";

/**
 * Auth surfaces get their own chrome: a single centered column with no
 * marketing navigation, so nothing competes with the form.
 */
export default function AuthLayout({ children }: LayoutProps<"/">) {
  return (
    <div className="flex min-h-dvh flex-col bg-[var(--surface-alt)]">
      <header className="shell-wide flex h-14 items-center justify-between">
        <Link href="/" className="text-[var(--text-primary)]" aria-label="홈으로">
          <Logo />
        </Link>
        <ThemeToggle />
      </header>

      <main
        id="main"
        className="flex flex-1 items-center justify-center px-5 py-10"
      >
        <div className="w-full max-w-[420px]">{children}</div>
      </main>

      <footer className="shell-wide py-6 text-center text-[12px] text-[var(--text-tertiary)]">
        © {new Date().getFullYear()} BlogOps AI
      </footer>
    </div>
  );
}
