import type { ReactNode } from "react";

import { cn } from "@/lib/cn";

/**
 * Table primitives. Wide tables must scroll inside their own container so the
 * page body never scrolls horizontally on narrow viewports.
 */
export function TableWrap({
  className,
  children,
}: {
  className?: string;
  children: ReactNode;
}) {
  return (
    <div
      className={cn(
        "overflow-x-auto rounded-[14px] border border-[var(--hairline-soft)]",
        className,
      )}
    >
      {children}
    </div>
  );
}

export function Table({ children }: { children: ReactNode }) {
  return (
    <table className="w-full min-w-[640px] border-collapse text-left text-[14px]">
      {children}
    </table>
  );
}

export function Th({
  children,
  className,
  align = "left",
}: {
  children?: ReactNode;
  className?: string;
  align?: "left" | "right" | "center";
}) {
  return (
    <th
      scope="col"
      className={cn(
        "sticky top-0 z-1 bg-[var(--surface-sunken)] px-4 py-3",
        "text-[12px] font-semibold tracking-[0.02em] text-[var(--text-secondary)]",
        "border-b border-[var(--hairline-soft)] whitespace-nowrap",
        align === "right" && "text-right",
        align === "center" && "text-center",
        className,
      )}
    >
      {children}
    </th>
  );
}

export function Td({
  children,
  className,
  align = "left",
}: {
  children?: ReactNode;
  className?: string;
  align?: "left" | "right" | "center";
}) {
  return (
    <td
      className={cn(
        "border-b border-[var(--hairline-soft)] px-4 py-3 align-middle",
        align === "right" && "text-right",
        align === "center" && "text-center",
        className,
      )}
    >
      {children}
    </td>
  );
}

export function Tr({
  children,
  className,
  onClick,
}: {
  children: ReactNode;
  className?: string;
  onClick?: () => void;
}) {
  return (
    <tr
      onClick={onClick}
      className={cn(
        "transition-colors duration-150 last:[&>td]:border-b-0",
        onClick && "cursor-pointer hover:bg-[var(--surface-alt)]",
        className,
      )}
    >
      {children}
    </tr>
  );
}

/** Monospaced cell for hashes, ids and keys. */
export function Mono({
  children,
  className,
}: {
  children: ReactNode;
  className?: string;
}) {
  return (
    <span
      className={cn(
        "font-mono text-[12px] text-[var(--text-secondary)]",
        className,
      )}
    >
      {children}
    </span>
  );
}
