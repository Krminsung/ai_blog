/**
 * Token storage for the browser session.
 *
 * The backend issues short-lived access tokens plus rotating refresh tokens,
 * so a refresh token is single-use: every refresh replaces the stored pair.
 * Tokens live in `localStorage` behind one key so a sign-out is a single
 * removal, and every tab observes the change through the `storage` event.
 */

export interface TokenPair {
  access_token: string;
  refresh_token: string;
  expires_in: number;
  session_id: string;
  workspace_id: string;
}

export interface StoredSession extends TokenPair {
  /** Epoch milliseconds at which `access_token` stops being accepted. */
  expires_at: number;
}

const STORAGE_KEY = "blogops.session";

/** Refresh this many seconds before the access token actually expires. */
const REFRESH_SKEW_SECONDS = 60;

type Listener = (session: StoredSession | null) => void;

const listeners = new Set<Listener>();
let cached: StoredSession | null | undefined;

function readStorage(): StoredSession | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as StoredSession;
    if (!parsed.access_token || !parsed.refresh_token) return null;
    return parsed;
  } catch {
    return null;
  }
}

export function getSession(): StoredSession | null {
  if (cached === undefined) cached = readStorage();
  return cached;
}

export function setSession(pair: TokenPair): StoredSession {
  const session: StoredSession = {
    ...pair,
    expires_at: Date.now() + pair.expires_in * 1000,
  };
  cached = session;
  if (typeof window !== "undefined") {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(session));
  }
  listeners.forEach((listener) => listener(session));
  return session;
}

export function clearSession(): void {
  cached = null;
  if (typeof window !== "undefined") {
    window.localStorage.removeItem(STORAGE_KEY);
  }
  listeners.forEach((listener) => listener(null));
}

/** True once the access token is inside the refresh skew window. */
export function isExpiring(session: StoredSession): boolean {
  return session.expires_at - REFRESH_SKEW_SECONDS * 1000 <= Date.now();
}

export function subscribe(listener: Listener): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

/** Keep sibling tabs in sync when one of them signs in or out. */
export function watchStorage(): () => void {
  if (typeof window === "undefined") return () => {};
  const onStorage = (event: StorageEvent) => {
    if (event.key !== null && event.key !== STORAGE_KEY) return;
    cached = readStorage();
    listeners.forEach((listener) => listener(cached ?? null));
  };
  window.addEventListener("storage", onStorage);
  return () => window.removeEventListener("storage", onStorage);
}
