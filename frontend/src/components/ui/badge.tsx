import type { ReactNode } from "react";

import { cn } from "@/lib/cn";
import { labelFor, type Registry, type Tone } from "@/lib/labels";

const TONES: Record<Tone, string> = {
  neutral:
    "bg-[var(--surface-alt)] text-[var(--text-secondary)] ring-[var(--hairline-soft)]",
  progress: "bg-[var(--accent-soft)] text-[var(--accent-link)] ring-transparent",
  positive:
    "bg-[var(--positive-soft)] text-[var(--positive)] ring-transparent",
  caution: "bg-[var(--caution-soft)] text-[var(--caution)] ring-transparent",
  critical:
    "bg-[var(--critical-soft)] text-[var(--critical)] ring-transparent",
};

export function Badge({
  tone = "neutral",
  className,
  children,
}: {
  tone?: Tone;
  className?: string;
  children: ReactNode;
}) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-full px-2.5 py-1 text-[12px]",
        "font-medium leading-none ring-1 ring-inset",
        TONES[tone],
        className,
      )}
    >
      {children}
    </span>
  );
}

/** Badge whose copy and tone come from the enum registries. */
export function StatusBadge({
  registry,
  value,
  className,
}: {
  registry: Registry;
  value?: string | null;
  className?: string;
}) {
  const spec = labelFor(registry, value);
  return (
    <Badge tone={spec.tone} className={className}>
      {spec.label}
    </Badge>
  );
}

/** Small pulsing dot for live states, matching the badge palette. */
export function StatusDot({ tone = "neutral" }: { tone?: Tone }) {
  const color =
    tone === "positive"
      ? "var(--positive)"
      : tone === "caution"
        ? "var(--caution)"
        : tone === "critical"
          ? "var(--critical)"
          : tone === "progress"
            ? "var(--accent)"
            : "var(--text-tertiary)";
  return (
    <span className="relative inline-flex size-2">
      {tone === "progress" ? (
        <span
          className="absolute inset-0 animate-ping rounded-full opacity-60"
          style={{ backgroundColor: color }}
        />
      ) : null}
      <span
        className="relative inline-flex size-2 rounded-full"
        style={{ backgroundColor: color }}
      />
    </span>
  );
}
