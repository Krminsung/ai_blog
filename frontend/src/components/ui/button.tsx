import Link from "next/link";
import type { ComponentProps, ReactNode } from "react";

import { cn } from "@/lib/cn";

/**
 * Apple ships two button shapes: a filled pill for commitment and a plain
 * blue link with a chevron for "learn more". Both live here so the marketing
 * pages and the console share one hit-target and focus treatment.
 */
export type ButtonVariant =
  | "primary"
  | "secondary"
  | "outline"
  | "ghost"
  | "danger";
export type ButtonSize = "sm" | "md" | "lg";

const BASE =
  "inline-flex items-center justify-center gap-1.5 rounded-full font-normal " +
  "transition-[background-color,color,opacity,transform] duration-200 " +
  "[transition-timing-function:var(--ease-apple)] " +
  "disabled:opacity-40 disabled:pointer-events-none select-none whitespace-nowrap";

const VARIANTS: Record<ButtonVariant, string> = {
  primary:
    "bg-[var(--accent)] text-[var(--accent-contrast)] hover:bg-[color-mix(in_srgb,var(--accent)_88%,white)] active:scale-[0.98]",
  secondary:
    "bg-[var(--surface-alt)] text-[var(--text-primary)] hover:bg-[color-mix(in_srgb,var(--surface-alt)_82%,var(--text-primary))] active:scale-[0.98]",
  outline:
    "border border-[var(--hairline)] text-[var(--text-primary)] hover:bg-[var(--surface-alt)] active:scale-[0.98]",
  ghost:
    "text-[var(--text-primary)] hover:bg-[var(--surface-alt)] active:scale-[0.98]",
  danger:
    "bg-[var(--critical)] text-white hover:opacity-90 active:scale-[0.98]",
};

const SIZES: Record<ButtonSize, string> = {
  sm: "h-8 px-3.5 text-[13px]",
  md: "h-11 px-5 text-[15px]",
  lg: "h-12 px-7 text-[17px]",
};

interface CommonProps {
  variant?: ButtonVariant;
  size?: ButtonSize;
  loading?: boolean;
  className?: string;
  children: ReactNode;
}

export function Button({
  variant = "primary",
  size = "md",
  loading = false,
  className,
  children,
  disabled,
  ...props
}: CommonProps & ComponentProps<"button">) {
  return (
    <button
      className={cn(BASE, VARIANTS[variant], SIZES[size], className)}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      {...props}
    >
      {loading ? <Spinner /> : null}
      {children}
    </button>
  );
}

export function ButtonLink({
  variant = "primary",
  size = "md",
  className,
  children,
  ...props
}: CommonProps & ComponentProps<typeof Link>) {
  return (
    <Link
      className={cn(BASE, VARIANTS[variant], SIZES[size], className)}
      {...props}
    >
      {children}
    </Link>
  );
}

/** The blue "더 알아보기 ›" affordance used across marketing sections. */
export function ChevronLink({
  className,
  children,
  ...props
}: { children: ReactNode } & ComponentProps<typeof Link>) {
  return (
    <Link
      className={cn(
        "group inline-flex items-center gap-1 text-[17px] text-[var(--accent-link)]",
        "transition-opacity duration-200 hover:underline underline-offset-4",
        className,
      )}
      {...props}
    >
      {children}
      <span
        aria-hidden
        className="translate-y-[-0.5px] transition-transform duration-200 [transition-timing-function:var(--ease-apple)] group-hover:translate-x-0.5"
      >
        ›
      </span>
    </Link>
  );
}

function Spinner() {
  return (
    <span
      aria-hidden
      className="size-3.5 animate-spin rounded-full border-2 border-current border-t-transparent opacity-70"
    />
  );
}
