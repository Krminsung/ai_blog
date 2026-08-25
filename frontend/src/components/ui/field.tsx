import type { ComponentProps, ReactNode } from "react";
import { useId } from "react";

import { cn } from "@/lib/cn";

const CONTROL =
  "w-full rounded-[12px] border border-[var(--hairline)] bg-[var(--surface)] " +
  "px-3.5 py-2.5 text-[15px] text-[var(--text-primary)] " +
  "placeholder:text-[var(--text-tertiary)] " +
  "transition-[border-color,box-shadow] duration-200 " +
  "focus:border-[var(--accent)] focus:outline-none focus:ring-4 focus:ring-[var(--accent-soft)] " +
  "disabled:opacity-50 aria-[invalid=true]:border-[var(--critical)]";

/** Label + control + hint/error, with ids wired for screen readers. */
export function Field({
  label,
  hint,
  error,
  required,
  children,
  className,
}: {
  label: ReactNode;
  hint?: ReactNode;
  error?: string | null;
  required?: boolean;
  children: (props: {
    id: string;
    "aria-invalid": boolean;
    "aria-describedby": string | undefined;
  }) => ReactNode;
  className?: string;
}) {
  const id = useId();
  const describedBy = error ? `${id}-error` : hint ? `${id}-hint` : undefined;

  return (
    <div className={cn("flex flex-col gap-1.5", className)}>
      <label
        htmlFor={id}
        className="text-[13px] font-medium text-[var(--text-secondary)]"
      >
        {label}
        {required ? (
          <span className="ml-0.5 text-[var(--critical)]" aria-hidden>
            *
          </span>
        ) : null}
      </label>
      {children({
        id,
        "aria-invalid": Boolean(error),
        "aria-describedby": describedBy,
      })}
      {error ? (
        <p id={`${id}-error`} className="text-[12px] text-[var(--critical)]">
          {error}
        </p>
      ) : hint ? (
        <p id={`${id}-hint`} className="text-[12px] text-[var(--text-tertiary)]">
          {hint}
        </p>
      ) : null}
    </div>
  );
}

export function Input({ className, ...props }: ComponentProps<"input">) {
  return <input className={cn(CONTROL, className)} {...props} />;
}

export function Textarea({ className, ...props }: ComponentProps<"textarea">) {
  return (
    <textarea className={cn(CONTROL, "min-h-28 resize-y", className)} {...props} />
  );
}

/**
 * Native select with the UA chevron replaced by our own, drawn as an inline
 * background so the control stays a single focusable element.
 */
export function Select({ className, children, ...props }: ComponentProps<"select">) {
  return (
    <select
      className={cn(
        CONTROL,
        "appearance-none bg-[length:11px] bg-[right_0.85rem_center] bg-no-repeat pr-9",
        className,
      )}
      style={{
        backgroundImage:
          "url(\"data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='11' height='7' fill='none'%3E%3Cpath d='M1 1l4.5 4.5L10 1' stroke='%2386868b' stroke-width='1.6' stroke-linecap='round' stroke-linejoin='round'/%3E%3C/svg%3E\")",
      }}
      {...props}
    >
      {children}
    </select>
  );
}

/** iOS-style switch for boolean settings. */
export function Toggle({
  checked,
  onChange,
  label,
  disabled,
}: {
  checked: boolean;
  onChange: (next: boolean) => void;
  label: string;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className={cn(
        "relative h-[31px] w-[51px] shrink-0 rounded-full transition-colors duration-300",
        "[transition-timing-function:var(--ease-apple)] disabled:opacity-40",
        checked ? "bg-[var(--positive)]" : "bg-[var(--hairline)]",
      )}
    >
      <span
        className={cn(
          "absolute top-[2px] left-[2px] size-[27px] rounded-full bg-white shadow-sm",
          "transition-transform duration-300 [transition-timing-function:var(--ease-apple)]",
          checked && "translate-x-5",
        )}
      />
    </button>
  );
}

/** Segmented control — the console's primary in-page filter. */
export function Segmented<T extends string>({
  options,
  value,
  onChange,
  className,
}: {
  options: { value: T; label: string }[];
  value: T;
  onChange: (next: T) => void;
  className?: string;
}) {
  return (
    <div
      role="tablist"
      className={cn(
        "inline-flex items-center gap-0.5 rounded-[10px] bg-[var(--surface-alt)] p-0.5",
        className,
      )}
    >
      {options.map((option) => {
        const active = option.value === value;
        return (
          <button
            key={option.value}
            role="tab"
            type="button"
            aria-selected={active}
            onClick={() => onChange(option.value)}
            className={cn(
              "rounded-[8px] px-3 py-1.5 text-[13px] font-medium transition-all duration-200",
              "[transition-timing-function:var(--ease-apple)]",
              active
                ? "bg-[var(--surface-raised)] text-[var(--text-primary)] shadow-[0_1px_3px_rgba(0,0,0,0.12)]"
                : "text-[var(--text-secondary)] hover:text-[var(--text-primary)]",
            )}
          >
            {option.label}
          </button>
        );
      })}
    </div>
  );
}

/** Rounded search field with a leading glyph. */
export function SearchInput({
  className,
  ...props
}: ComponentProps<"input">) {
  return (
    <div className={cn("relative", className)}>
      <span
        aria-hidden
        className="pointer-events-none absolute top-1/2 left-3 -translate-y-1/2 text-[var(--text-tertiary)]"
      >
        <svg width="15" height="15" viewBox="0 0 16 16" fill="none">
          <circle
            cx="7"
            cy="7"
            r="5"
            stroke="currentColor"
            strokeWidth="1.5"
          />
          <path
            d="M11 11L14.5 14.5"
            stroke="currentColor"
            strokeWidth="1.5"
            strokeLinecap="round"
          />
        </svg>
      </span>
      <input
        type="search"
        className={cn(
          CONTROL,
          "rounded-full py-2 pl-9 text-[14px] [&::-webkit-search-cancel-button]:appearance-none",
        )}
        {...props}
      />
    </div>
  );
}
