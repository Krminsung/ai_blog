"use client";

import Link from "next/link";
import type { ReactNode } from "react";

import { cn } from "@/lib/cn";
import { Card } from "@/components/ui/surface";
import { EmptyState, ErrorState, SkeletonRows } from "@/components/ui/feedback";
import type { ApiError } from "@/lib/api/errors";

export function PageHeader({
  title,
  description,
  actions,
  breadcrumb,
  className,
}: {
  title: string;
  description?: string;
  actions?: ReactNode;
  breadcrumb?: { href: string; label: string };
  className?: string;
}) {
  return (
    <header className={cn("mb-6", className)}>
      {breadcrumb ? (
        <Link
          href={breadcrumb.href}
          className="mb-2 inline-flex items-center gap-1 text-[13px] text-[var(--accent-link)] hover:underline"
        >
          <span aria-hidden>‹</span> {breadcrumb.label}
        </Link>
      ) : null}
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div className="min-w-0">
          <h1 className="text-[28px] leading-tight font-semibold tracking-[-0.03em] sm:text-[32px]">
            {title}
          </h1>
          {description ? (
            <p className="mt-1.5 max-w-2xl text-[14px] leading-relaxed text-[var(--text-secondary)]">
              {description}
            </p>
          ) : null}
        </div>
        {actions ? (
          <div className="flex shrink-0 flex-wrap items-center gap-2">
            {actions}
          </div>
        ) : null}
      </div>
    </header>
  );
}

/** Compact metric tile for dashboard rows. */
export function StatCard({
  label,
  value,
  hint,
  href,
  tone = "neutral",
}: {
  label: string;
  value: ReactNode;
  hint?: string;
  href?: string;
  tone?: "neutral" | "positive" | "caution" | "critical";
}) {
  const body = (
    <Card
      className={cn(
        "h-full px-5 py-4 transition-shadow duration-200",
        href && "hover:shadow-[var(--shadow-card)]",
      )}
    >
      <p className="text-[12.5px] text-[var(--text-secondary)]">{label}</p>
      <p
        className={cn(
          "numeric mt-1.5 text-[26px] leading-none font-semibold tracking-[-0.03em]",
          tone === "positive" && "text-[var(--positive)]",
          tone === "caution" && "text-[var(--caution)]",
          tone === "critical" && "text-[var(--critical)]",
        )}
      >
        {value}
      </p>
      {hint ? <p className="type-caption mt-1.5">{hint}</p> : null}
    </Card>
  );

  return href ? (
    <Link href={href} className="block">
      {body}
    </Link>
  ) : (
    body
  );
}

/** Key/value pairs for detail panes. */
export function DescriptionList({
  items,
  columns = 2,
  className,
}: {
  items: { term: string; value: ReactNode }[];
  columns?: 1 | 2 | 3;
  className?: string;
}) {
  return (
    <dl
      className={cn(
        "grid gap-x-6 gap-y-4",
        columns === 1 && "grid-cols-1",
        columns === 2 && "sm:grid-cols-2",
        columns === 3 && "sm:grid-cols-2 lg:grid-cols-3",
        className,
      )}
    >
      {items.map((item) => (
        <div key={item.term} className="min-w-0">
          <dt className="text-[12px] text-[var(--text-tertiary)]">{item.term}</dt>
          <dd className="mt-0.5 text-[14px] break-words">{item.value}</dd>
        </div>
      ))}
    </dl>
  );
}

/**
 * One place that decides between skeleton, error, empty and content, so every
 * list screen behaves identically.
 */
export function AsyncSection<T>({
  data,
  error,
  errorText,
  isLoading,
  onRetry,
  isEmpty,
  empty,
  children,
  skeletonRows = 5,
}: {
  data: T | undefined;
  error?: ApiError;
  errorText?: string | null;
  isLoading: boolean;
  onRetry?: () => void;
  isEmpty?: (data: T) => boolean;
  empty?: { title: string; description?: string; action?: ReactNode };
  children: (data: T) => ReactNode;
  skeletonRows?: number;
}) {
  if (isLoading) return <SkeletonRows rows={skeletonRows} />;

  if (error) {
    return (
      <ErrorState
        message={errorText ?? "데이터를 불러오지 못했습니다."}
        requestId={error.requestId}
        onRetry={onRetry}
      />
    );
  }

  if (data === undefined) {
    return (
      <EmptyState
        title="표시할 내용이 없습니다"
        description="백엔드 응답이 비어 있습니다."
      />
    );
  }

  if (isEmpty?.(data)) {
    return (
      <EmptyState
        title={empty?.title ?? "아직 항목이 없습니다"}
        description={empty?.description}
        action={empty?.action}
      />
    );
  }

  return <>{children(data)}</>;
}

/** Horizontal filter bar used above tables. */
export function FilterBar({
  children,
  className,
}: {
  children: ReactNode;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "mb-4 flex flex-wrap items-center gap-2.5",
        className,
      )}
    >
      {children}
    </div>
  );
}
