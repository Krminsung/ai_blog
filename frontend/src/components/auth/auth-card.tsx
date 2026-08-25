import type { ReactNode } from "react";

import { cn } from "@/lib/cn";

export function AuthCard({
  title,
  description,
  children,
  footer,
  className,
}: {
  title: string;
  description?: ReactNode;
  children: ReactNode;
  footer?: ReactNode;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "rounded-[20px] border border-[var(--hairline-soft)] bg-[var(--surface-raised)] p-8",
        "shadow-[var(--shadow-card)]",
        className,
      )}
    >
      <h1 className="text-[28px] leading-tight font-semibold tracking-[-0.03em]">
        {title}
      </h1>
      {description ? (
        <p className="mt-2 text-[14px] leading-relaxed text-[var(--text-secondary)]">
          {description}
        </p>
      ) : null}
      <div className="mt-7">{children}</div>
      {footer ? (
        <div className="mt-6 border-t border-[var(--hairline-soft)] pt-5 text-center text-[13px] text-[var(--text-secondary)]">
          {footer}
        </div>
      ) : null}
    </div>
  );
}
