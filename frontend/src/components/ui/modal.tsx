"use client";

import { useEffect, useRef, type ReactNode } from "react";
import { createPortal } from "react-dom";

import { cn } from "@/lib/cn";
import { Button } from "@/components/ui/button";
import { useIsMounted } from "@/lib/hooks/use-mounted";

/**
 * Centered sheet with a blurred scrim. Focus is trapped while open and the
 * page behind is locked, matching the modal behaviour on apple.com's
 * configurator overlays.
 */
export function Modal({
  open,
  onClose,
  title,
  description,
  children,
  footer,
  size = "md",
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  description?: string;
  children: ReactNode;
  footer?: ReactNode;
  size?: "sm" | "md" | "lg";
}) {
  const panelRef = useRef<HTMLDivElement>(null);
  const mounted = useIsMounted();

  useEffect(() => {
    if (!open) return;

    const previouslyFocused = document.activeElement as HTMLElement | null;
    const { overflow } = document.body.style;
    document.body.style.overflow = "hidden";

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.stopPropagation();
        onClose();
        return;
      }
      if (event.key !== "Tab") return;

      const focusable = panelRef.current?.querySelectorAll<HTMLElement>(
        'a[href], button:not([disabled]), textarea, input, select, [tabindex]:not([tabindex="-1"])',
      );
      if (!focusable || focusable.length === 0) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };

    document.addEventListener("keydown", onKeyDown, true);
    // Defer so the panel exists before we move focus into it.
    const raf = requestAnimationFrame(() => {
      panelRef.current
        ?.querySelector<HTMLElement>(
          'input, textarea, select, button:not([data-close])',
        )
        ?.focus();
    });

    return () => {
      document.removeEventListener("keydown", onKeyDown, true);
      cancelAnimationFrame(raf);
      document.body.style.overflow = overflow;
      previouslyFocused?.focus();
    };
  }, [open, onClose]);

  if (!open || !mounted) return null;

  return createPortal(
    <div
      className="fixed inset-0 z-100 flex items-end justify-center p-0 sm:items-center sm:p-6"
      role="dialog"
      aria-modal="true"
      aria-label={title}
    >
      <button
        type="button"
        aria-label="닫기"
        onClick={onClose}
        className="absolute inset-0 bg-[var(--overlay)] backdrop-blur-[2px]"
      />
      <div
        ref={panelRef}
        className={cn(
          "relative z-1 flex max-h-[90dvh] w-full flex-col overflow-hidden",
          "rounded-t-[20px] bg-[var(--surface-raised)] shadow-[var(--shadow-float)]",
          "sm:rounded-[20px]",
          "motion-safe:animate-[modal-in_.32s_var(--ease-apple)]",
          size === "sm" && "sm:max-w-md",
          size === "md" && "sm:max-w-xl",
          size === "lg" && "sm:max-w-3xl",
        )}
      >
        <div className="flex items-start justify-between gap-4 px-6 pt-6 pb-2">
          <div className="min-w-0">
            <h2 className="text-[20px] font-semibold tracking-[-0.02em]">
              {title}
            </h2>
            {description ? (
              <p className="mt-1 text-[13px] leading-relaxed text-[var(--text-secondary)]">
                {description}
              </p>
            ) : null}
          </div>
          <button
            type="button"
            data-close
            onClick={onClose}
            aria-label="닫기"
            className="-mt-1 -mr-1 grid size-8 shrink-0 place-items-center rounded-full text-[var(--text-secondary)] transition-colors hover:bg-[var(--surface-alt)]"
          >
            <svg width="14" height="14" viewBox="0 0 14 14" fill="none" aria-hidden>
              <path
                d="M1 1l12 12M13 1L1 13"
                stroke="currentColor"
                strokeWidth="1.6"
                strokeLinecap="round"
              />
            </svg>
          </button>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto px-6 py-4">{children}</div>

        {footer ? (
          <div className="flex flex-wrap justify-end gap-2 border-t border-[var(--hairline-soft)] px-6 py-4">
            {footer}
          </div>
        ) : null}
      </div>

      <style>{`
        @keyframes modal-in {
          from { opacity: 0; transform: translateY(12px) scale(.98); }
          to   { opacity: 1; transform: none; }
        }
      `}</style>
    </div>,
    document.body,
  );
}

/** Destructive confirmation with the action spelled out in the button. */
export function ConfirmDialog({
  open,
  onClose,
  onConfirm,
  title,
  description,
  confirmLabel = "확인",
  danger = false,
  pending = false,
}: {
  open: boolean;
  onClose: () => void;
  onConfirm: () => void;
  title: string;
  description?: string;
  confirmLabel?: string;
  danger?: boolean;
  pending?: boolean;
}) {
  return (
    <Modal
      open={open}
      onClose={onClose}
      title={title}
      description={description}
      size="sm"
      footer={
        <>
          <Button variant="secondary" onClick={onClose} disabled={pending}>
            취소
          </Button>
          <Button
            variant={danger ? "danger" : "primary"}
            onClick={onConfirm}
            loading={pending}
          >
            {confirmLabel}
          </Button>
        </>
      }
    >
      <p className="text-[14px] leading-relaxed text-[var(--text-secondary)]">
        이 작업은 되돌릴 수 없습니다. 계속하려면 확인을 누르세요.
      </p>
    </Modal>
  );
}
