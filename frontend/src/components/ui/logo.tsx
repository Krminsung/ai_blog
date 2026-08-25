import { cn } from "@/lib/cn";

/**
 * Wordmark. The glyph is a stacked-document mark that reads at 20px in the
 * nav and scales cleanly into the footer.
 */
export function Logo({
  className,
  showWordmark = true,
}: {
  className?: string;
  showWordmark?: boolean;
}) {
  return (
    <span className={cn("inline-flex items-center gap-2", className)}>
      <svg
        width="22"
        height="22"
        viewBox="0 0 24 24"
        fill="none"
        aria-hidden
        className="shrink-0"
      >
        <path
          d="M6 3.5h8.5L19 8v12.5H6V3.5Z"
          stroke="currentColor"
          strokeWidth="1.6"
          strokeLinejoin="round"
        />
        <path
          d="M14 3.5V8h4.5"
          stroke="currentColor"
          strokeWidth="1.6"
          strokeLinejoin="round"
        />
        <path
          d="M9 12.5h7M9 16h4.5"
          stroke="currentColor"
          strokeWidth="1.6"
          strokeLinecap="round"
        />
      </svg>
      {showWordmark ? (
        <span className="text-[17px] font-semibold tracking-[-0.02em]">
          BlogOps
        </span>
      ) : null}
      <span className="sr-only">BlogOps AI</span>
    </span>
  );
}
