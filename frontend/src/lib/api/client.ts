import { API_BASE_URL } from "@/lib/env";
import { ApiError, type ApiErrorBody } from "@/lib/api/errors";
import {
  clearSession,
  getSession,
  isExpiring,
  setSession,
  type TokenPair,
} from "@/lib/api/tokens";

export type QueryValue = string | number | boolean | null | undefined;
export type Query = Record<string, QueryValue | QueryValue[]>;

export interface RequestOptions {
  method?: "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
  query?: Query;
  body?: unknown;
  /** Sent as `Idempotency-Key`; required by the backend on replayable writes. */
  idempotencyKey?: string;
  signal?: AbortSignal;
  /** Skip the bearer token — used by the public auth routes. */
  anonymous?: boolean;
  /** Return the raw `Response` instead of parsed JSON (CSV/ICS exports). */
  raw?: boolean;
  headers?: Record<string, string>;
}

function buildUrl(path: string, query?: Query): string {
  const url = new URL(
    path.startsWith("/") ? `${API_BASE_URL}${path}` : `${API_BASE_URL}/${path}`,
  );
  if (query) {
    for (const [key, value] of Object.entries(query)) {
      if (value === undefined || value === null || value === "") continue;
      if (Array.isArray(value)) {
        for (const item of value) {
          if (item === undefined || item === null || item === "") continue;
          url.searchParams.append(key, String(item));
        }
      } else {
        url.searchParams.set(key, String(value));
      }
    }
  }
  return url.toString();
}

async function parseError(response: Response): Promise<ApiError> {
  let payload: Partial<ApiErrorBody> = {};
  try {
    payload = (await response.json()) as Partial<ApiErrorBody>;
  } catch {
    // Non-JSON body (gateway timeouts, proxy errors) — fall through.
  }
  return new ApiError(response.status, payload.error ?? {});
}

/* ---------------------------------------------------------------------------
 * Access-token refresh.
 *
 * Refresh tokens rotate, so two concurrent 401s must not both spend the stored
 * token. `inflightRefresh` collapses them onto one request.
 * ------------------------------------------------------------------------- */

let inflightRefresh: Promise<string | null> | null = null;

async function performRefresh(): Promise<string | null> {
  const session = getSession();
  if (!session) return null;
  try {
    const response = await fetch(buildUrl("/v1/auth/token/refresh"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        refresh_token: session.refresh_token,
        workspace_id: session.workspace_id,
      }),
    });
    if (!response.ok) {
      clearSession();
      return null;
    }
    const pair = (await response.json()) as TokenPair;
    setSession(pair);
    return pair.access_token;
  } catch {
    // Network failure: keep the session so a retry can succeed later.
    return null;
  }
}

async function refreshAccessToken(): Promise<string | null> {
  inflightRefresh ??= performRefresh().finally(() => {
    inflightRefresh = null;
  });
  return inflightRefresh;
}

async function authorizationHeader(): Promise<string | null> {
  const session = getSession();
  if (!session) return null;
  if (isExpiring(session)) {
    const refreshed = await refreshAccessToken();
    return refreshed ? `Bearer ${refreshed}` : null;
  }
  return `Bearer ${session.access_token}`;
}

function serializeRequestBody(body: unknown): BodyInit | undefined {
  if (body === undefined) return undefined;
  return body instanceof FormData ? body : JSON.stringify(body);
}

async function requestHeaders(
  options: RequestOptions,
): Promise<Record<string, string>> {
  const headers: Record<string, string> = {
    Accept: "application/json",
    ...options.headers,
  };

  const hasJsonBody =
    options.body !== undefined && !(options.body instanceof FormData);
  if (hasJsonBody) headers["Content-Type"] = "application/json";
  if (options.idempotencyKey) headers["Idempotency-Key"] = options.idempotencyKey;

  if (!options.anonymous) {
    const authorization = await authorizationHeader();
    if (authorization) headers.Authorization = authorization;
  }

  return headers;
}

async function parseSuccess<T>(response: Response): Promise<T> {
  if (response.status === 204) return undefined as T;
  const text = await response.text();
  if (!text) return undefined as T;
  return JSON.parse(text) as T;
}

/** Issue one request, optionally retrying once after a token refresh. */
async function send(
  path: string,
  options: RequestOptions,
  allowRetry: boolean,
): Promise<Response> {
  const response = await fetch(buildUrl(path, options.query), {
    method: options.method ?? "GET",
    headers: await requestHeaders(options),
    body: serializeRequestBody(options.body),
    signal: options.signal,
    cache: "no-store",
  });

  // A 401 on an authenticated call means the access token died early (session
  // revoked elsewhere, clock drift). Refresh once, then replay.
  if (response.status === 401 && allowRetry && !options.anonymous) {
    const refreshed = await refreshAccessToken();
    if (refreshed) return send(path, options, false);
    clearSession();
  }

  return response;
}

/** Typed JSON request. Throws `ApiError` on any non-2xx response. */
export async function api<T>(
  path: string,
  options: RequestOptions = {},
): Promise<T> {
  const response = await send(path, options, true);
  if (!response.ok) throw await parseError(response);
  return parseSuccess<T>(response);
}

/** Request that returns the raw response — used for file/CSV/ICS downloads. */
export async function apiRaw(
  path: string,
  options: RequestOptions = {},
): Promise<Response> {
  const response = await send(path, options, true);
  if (!response.ok) throw await parseError(response);
  return response;
}

/** Convenience wrapper for endpoints that require an idempotency key. */
export function newIdempotencyKey(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return crypto.randomUUID();
  }
  return `idem-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
}
