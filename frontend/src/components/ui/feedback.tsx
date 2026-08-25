import type { ReactNode } from "react";

import { cn } from "@/lib/cn";

/** Shimmering placeholder used while a request is in flight. */
export function Skeleton({ className }: { className?: string }) {
  return (
    <div
      aria-hidden
      className={cn(
        "animate-pulse rounded-[8px] bg-[var(--surface-alt)]",
        className,
      )}
    />
  );
}

export function SkeletonRows({ rows = 5 }: { rows?: number }) {
  return (
    <div className="flex flex-col gap-2.5" role="status" aria-label="불러오는 중">
      {Array.from({ length: rows }).map((_, index) => (
        <Skeleton key={index} className="h-12 w-full" />
      ))}
    </div>
  );
}

export function Spinner({ className }: { className?: string }) {
  return (
    <span
      role="status"
      aria-label="처리 중"
      className={cn(
        "inline-block size-4 animate-spin rounded-full border-2",
        "border-[var(--hairline)] border-t-[var(--accent)]",
        className,
      )}
    />
  );
}

export function EmptyState({
  title,
  description,
  action,
  icon,
  className,
}: {
  title: string;
  description?: string;
  action?: ReactNode;
  icon?: ReactNode;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "flex flex-col items-center justify-center gap-3 px-6 py-16 text-center",
        className,
      )}
    >
      {icon ? (
        <div className="text-[var(--text-tertiary)]" aria-hidden>
          {icon}
        </div>
      ) : null}
      <p className="text-[17px] font-semibold tracking-[-0.02em]">{title}</p>
      {description ? (
        <p className="max-w-md text-[14px] leading-relaxed text-[var(--text-secondary)]">
          {description}
        </p>
      ) : null}
      {action ? <div className="mt-2">{action}</div> : null}
    </div>
  );
}

/**
 * Inline error panel. `requestId` is surfaced deliberately — every backend
 * error carries one, and it is what support needs to trace the failure.
 */
export function ErrorState({
  message,
  requestId,
  onRetry,
  className,
}: {
  message: string;
  requestId?: string | null;
  onRetry?: () => void;
  className?: string;
}) {
  return (
    <div
      role="alert"
      className={cn(
        "flex flex-col items-start gap-2 rounded-[14px] border p-4",
        "border-[color-mix(in_srgb,var(--critical)_30%,transparent)] bg-[var(--critical-soft)]",
        className,
      )}
    >
      <p className="text-[14px] font-medium text-[var(--critical)]">{message}</p>
      {requestId ? (
        <p className="font-mono text-[11px] text-[var(--text-secondary)]">
          request_id: {requestId}
        </p>
      ) : null}
      {onRetry ? (
        <button
          type="button"
          onClick={onRetry}
          className="text-[13px] text-[var(--accent-link)] underline-offset-4 hover:underline"
        >
          다시 시도
        </button>
      ) : null}
    </div>
  );
}

export function Notice({
  tone = "neutral",
  children,
  className,
}: {
  tone?: "neutral" | "info" | "caution" | "critical";
  children: ReactNode;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "rounded-[14px] border px-4 py-3 text-[13px] leading-relaxed",
        tone === "neutral" &&
          "border-[var(--hairline-soft)] bg-[var(--surface-alt)] text-[var(--text-secondary)]",
        tone === "info" &&
          "border-transparent bg-[var(--accent-soft)] text-[var(--accent-link)]",
        tone === "caution" &&
          "border-transparent bg-[var(--caution-soft)] text-[var(--caution)]",
        tone === "critical" &&
          "border-transparent bg-[var(--critical-soft)] text-[var(--critical)]",
        className,
      )}
    >
      {children}
    </div>
  );
}
