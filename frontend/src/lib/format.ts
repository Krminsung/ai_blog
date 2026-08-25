import { DEFAULT_LOCALE, DISPLAY_TIME_ZONE } from "@/lib/env";

/**
 * The backend stores everything in UTC and returns ISO-8601 strings. All
 * formatting funnels through here so the workspace time zone is applied once.
 */

const dateFormatter = new Intl.DateTimeFormat(DEFAULT_LOCALE, {
  year: "numeric",
  month: "long",
  day: "numeric",
  timeZone: DISPLAY_TIME_ZONE,
});

const dateTimeFormatter = new Intl.DateTimeFormat(DEFAULT_LOCALE, {
  year: "numeric",
  month: "long",
  day: "numeric",
  hour: "2-digit",
  minute: "2-digit",
  timeZone: DISPLAY_TIME_ZONE,
});

const shortFormatter = new Intl.DateTimeFormat(DEFAULT_LOCALE, {
  month: "short",
  day: "numeric",
  timeZone: DISPLAY_TIME_ZONE,
});

const timeFormatter = new Intl.DateTimeFormat(DEFAULT_LOCALE, {
  hour: "2-digit",
  minute: "2-digit",
  timeZone: DISPLAY_TIME_ZONE,
});

export function formatDate(value?: string | null): string {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "—" : dateFormatter.format(date);
}

export function formatDateTime(value?: string | null): string {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "—" : dateTimeFormatter.format(date);
}

export function formatShortDate(value?: string | null): string {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "—" : shortFormatter.format(date);
}

export function formatTime(value?: string | null): string {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "—" : timeFormatter.format(date);
}

const RELATIVE_UNITS: [Intl.RelativeTimeFormatUnit, number][] = [
  ["year", 365 * 24 * 60 * 60 * 1000],
  ["month", 30 * 24 * 60 * 60 * 1000],
  ["day", 24 * 60 * 60 * 1000],
  ["hour", 60 * 60 * 1000],
  ["minute", 60 * 1000],
];

const relativeFormatter = new Intl.RelativeTimeFormat(DEFAULT_LOCALE, {
  numeric: "auto",
});

/** "3일 전" / "5분 후" — used in activity feeds and job timelines. */
export function formatRelative(value?: string | null): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  const delta = date.getTime() - Date.now();
  for (const [unit, ms] of RELATIVE_UNITS) {
    if (Math.abs(delta) >= ms) {
      return relativeFormatter.format(Math.round(delta / ms), unit);
    }
  }
  return relativeFormatter.format(Math.round(delta / 1000), "second");
}

const numberFormatter = new Intl.NumberFormat(DEFAULT_LOCALE);

export function formatNumber(value?: number | string | null): string {
  if (value === null || value === undefined || value === "") return "—";
  const numeric = typeof value === "string" ? Number(value) : value;
  return Number.isNaN(numeric) ? "—" : numberFormatter.format(numeric);
}

/**
 * Money and credit amounts arrive as decimal strings so precision survives the
 * wire; parse late and only for display.
 */
export function formatDecimal(value?: string | number | null, digits = 2): string {
  if (value === null || value === undefined || value === "") return "—";
  const numeric = typeof value === "string" ? Number(value) : value;
  if (Number.isNaN(numeric)) return "—";
  return numeric.toLocaleString(DEFAULT_LOCALE, {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

export function formatCurrency(
  value?: string | number | null,
  currency = "KRW",
): string {
  if (value === null || value === undefined || value === "") return "—";
  const numeric = typeof value === "string" ? Number(value) : value;
  if (Number.isNaN(numeric)) return "—";
  return numeric.toLocaleString(DEFAULT_LOCALE, {
    style: "currency",
    currency,
    maximumFractionDigits: currency === "KRW" ? 0 : 2,
  });
}

export function formatPercent(value?: string | number | null, digits = 1): string {
  if (value === null || value === undefined || value === "") return "—";
  const numeric = typeof value === "string" ? Number(value) : value;
  if (Number.isNaN(numeric)) return "—";
  return `${numeric.toFixed(digits)}%`;
}

export function formatBytes(bytes?: number | null): string {
  if (bytes === null || bytes === undefined) return "—";
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let size = bytes / 1024;
  let unit = 0;
  while (size >= 1024 && unit < units.length - 1) {
    size /= 1024;
    unit += 1;
  }
  return `${size.toFixed(size >= 10 ? 0 : 1)} ${units[unit]}`;
}

/** Content hashes are long; show enough to eyeball a match. */
export function shortHash(value?: string | null, length = 10): string {
  if (!value) return "—";
  return value.length <= length ? value : `${value.slice(0, length)}…`;
}

export function shortId(value?: string | null): string {
  if (!value) return "—";
  return value.split("-")[0] ?? value;
}

/** `WAITING_REVIEW` → `Waiting review`, for enum values without a label map. */
export function humanizeEnum(value?: string | null): string {
  if (!value) return "—";
  const lower = value.replace(/_/g, " ").toLowerCase();
  return lower.charAt(0).toUpperCase() + lower.slice(1);
}
