/**
 * Runtime configuration.
 *
 * The browser talks to the FastAPI backend directly, so the base URL has to be
 * a public env var. `NEXT_PUBLIC_API_BASE_URL` should point at the API origin
 * (no trailing slash, no `/v1` — the client appends the version prefix).
 */
export const API_BASE_URL = (
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000"
).replace(/\/+$/, "");

/** Default locale the backend reports through `GET /v1/meta`. */
export const DEFAULT_LOCALE = "ko-KR";

/** All timestamps are stored in UTC; the UI renders them in this zone. */
export const DISPLAY_TIME_ZONE =
  process.env.NEXT_PUBLIC_DISPLAY_TIME_ZONE ?? "Asia/Seoul";
