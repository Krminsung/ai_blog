"use client";

import { useEffect, useRef, type ElementType, type ReactNode } from "react";

import { cn } from "@/lib/cn";

/**
 * Scroll-triggered fade-and-rise, the motion Apple uses to introduce every
 * section. The animation itself lives in CSS (`[data-reveal]`); this component
 * only flips the attribute once the element enters the viewport, and it never
 * flips back — content that has been read should not re-animate on scroll-up.
 */
export function Reveal({
  children,
  className,
  delay = 0,
  as: Tag = "div",
}: {
  children: ReactNode;
  className?: string;
  /** Stagger in milliseconds, for sibling cards. */
  delay?: number;
  as?: ElementType;
}) {
  const ref = useRef<HTMLElement>(null);

  useEffect(() => {
    const node = ref.current;
    if (!node) return;

    if (
      typeof IntersectionObserver === "undefined" ||
      window.matchMedia("(prefers-reduced-motion: reduce)").matches
    ) {
      node.dataset.reveal = "shown";
      return;
    }

    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (!entry.isIntersecting) continue;
          (entry.target as HTMLElement).dataset.reveal = "shown";
          observer.unobserve(entry.target);
        }
      },
      // Fire slightly before the element is fully on screen so the motion
      // finishes around the time the reader arrives.
      { rootMargin: "0px 0px -12% 0px", threshold: 0.15 },
    );

    observer.observe(node);
    return () => observer.disconnect();
  }, []);

  return (
    <Tag
      ref={ref}
      data-reveal=""
      style={delay ? ({ "--reveal-delay": `${delay}ms` } as React.CSSProperties) : undefined}
      className={cn(className)}
    >
      {children}
    </Tag>
  );
}
