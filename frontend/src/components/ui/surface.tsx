import type { ComponentProps, ElementType, ReactNode } from "react";

import { cn } from "@/lib/cn";

/**
 * Card: the rounded, hairline-bounded container the console is built from.
 * Apple's product tiles use a much larger radius than their UI cards, so the
 * two radii are separate tokens rather than one "rounded" scale.
 */
export function Card({
  className,
  children,
  as: Tag = "div",
  ...props
}: { as?: ElementType; className?: string; children: ReactNode } & Omit<
  ComponentProps<"div">,
  "className" | "children"
>) {
  return (
    <Tag
      className={cn(
        "rounded-[18px] border border-[var(--hairline-soft)] bg-[var(--surface-raised)]",
        className,
      )}
      {...props}
    >
      {children}
    </Tag>
  );
}

export function CardHeader({
  title,
  description,
  actions,
  className,
}: {
  title: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "flex flex-wrap items-start justify-between gap-4 px-6 pt-5 pb-4",
        className,
      )}
    >
      <div className="min-w-0">
        <h2 className="text-[17px] font-semibold tracking-[-0.02em]">{title}</h2>
        {description ? (
          <p className="mt-1 text-[13px] leading-relaxed text-[var(--text-secondary)]">
            {description}
          </p>
        ) : null}
      </div>
      {actions ? (
        <div className="flex shrink-0 items-center gap-2">{actions}</div>
      ) : null}
    </div>
  );
}

export function CardBody({
  className,
  children,
}: {
  className?: string;
  children: ReactNode;
}) {
  return <div className={cn("px-6 pb-6", className)}>{children}</div>;
}

/** Large marketing tile — the 28px-radius panel used in bento grids. */
export function Tile({
  className,
  children,
  tone = "light",
}: {
  className?: string;
  children: ReactNode;
  tone?: "light" | "dark" | "alt";
}) {
  return (
    <div
      className={cn(
        "relative isolate overflow-hidden rounded-[28px] p-8 sm:p-10",
        tone === "dark" && "bg-black text-[#f5f5f7]",
        tone === "alt" && "bg-[var(--surface-alt)] text-[var(--text-primary)]",
        tone === "light" &&
          "border border-[var(--hairline-soft)] bg-[var(--surface-raised)]",
        className,
      )}
    >
      {children}
    </div>
  );
}

/** Full-bleed section with the shared vertical rhythm. */
export function Section({
  className,
  children,
  tone = "surface",
  id,
}: {
  className?: string;
  children: ReactNode;
  tone?: "surface" | "alt" | "dark";
  id?: string;
}) {
  return (
    <section
      id={id}
      className={cn(
        "py-20 sm:py-28",
        tone === "alt" && "bg-[var(--surface-alt)]",
        tone === "dark" && "bg-black text-[#f5f5f7]",
        className,
      )}
    >
      {children}
    </section>
  );
}

export function Divider({ className }: { className?: string }) {
  return (
    <div
      role="separator"
      className={cn("h-px w-full bg-[var(--hairline-soft)]", className)}
    />
  );
}
