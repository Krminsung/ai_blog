"use client";

import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useSyncExternalStore,
} from "react";
import { useRouter } from "next/navigation";
import useSWR from "swr";

import { auth, workspaces as workspaceApi } from "@/lib/api/endpoints";
import { ApiError, isApiError } from "@/lib/api/errors";
import {
  clearSession,
  getSession,
  setSession,
  subscribe,
  watchStorage,
  type StoredSession,
  type TokenPair,
} from "@/lib/api/tokens";
import type { User, Workspace } from "@/lib/api/types";

type Status = "loading" | "authenticated" | "anonymous";

interface Profile {
  user: User;
  workspaces: Workspace[];
}

interface SessionContextValue {
  status: Status;
  user: User | null;
  workspace: Workspace | null;
  workspaces: Workspace[];
  session: StoredSession | null;
  /** Persist a token pair from login/MFA and hydrate the profile. */
  adoptTokens: (pair: TokenPair) => Promise<void>;
  switchWorkspace: (workspaceId: string) => Promise<void>;
  refreshProfile: () => Promise<void>;
  signOut: () => Promise<void>;
}

const SessionContext = createContext<SessionContextValue | null>(null);

const EMPTY_WORKSPACES: Workspace[] = [];

/**
 * The token pair lives in `localStorage`, not React state, so it is read
 * through the external store. That keeps the very first client render
 * accurate and mirrors sign-in/sign-out across tabs.
 */
function subscribeToTokens(listener: () => void): () => void {
  const unsubscribe = subscribe(listener);
  const unwatch = watchStorage();
  return () => {
    unsubscribe();
    unwatch();
  };
}

/** Server render has no storage; `undefined` reads as "not yet known". */
function serverSnapshot(): StoredSession | null | undefined {
  return undefined;
}

async function fetchProfile(): Promise<Profile> {
  // Both calls are cheap and the console needs them on every entry, so fetch
  // them together rather than waterfalling.
  const [user, list] = await Promise.all([
    auth.me(),
    workspaceApi.list().catch(() => EMPTY_WORKSPACES),
  ]);
  return { user, workspaces: list };
}

export function SessionProvider({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const session = useSyncExternalStore(
    subscribeToTokens,
    getSession,
    serverSnapshot,
  );

  // Keyed on the session identity so a workspace switch or a re-login
  // re-fetches, and signing out drops the cache entirely.
  const profileKey = session
    ? `profile:${session.workspace_id}:${session.session_id}`
    : null;

  const profile = useSWR<Profile, ApiError>(profileKey, fetchProfile, {
    revalidateOnFocus: false,
    shouldRetryOnError: false,
    onError: (error) => {
      // A rejected token means the stored session is dead; dropping it lets
      // the store notify subscribers and flip the app to anonymous.
      if (isApiError(error) && error.isAuthFailure) clearSession();
    },
  });

  const adoptTokens = useCallback(
    async (pair: TokenPair) => {
      setSession(pair);
      await profile.mutate();
    },
    [profile],
  );

  const switchWorkspace = useCallback(
    async (workspaceId: string) => {
      const stored = getSession();
      if (!stored || stored.workspace_id === workspaceId) return;
      // Workspace scope lives inside the access token, so switching means
      // exchanging the refresh token for a pair bound to the new workspace.
      const pair = await auth.refresh(stored.refresh_token, workspaceId);
      setSession(pair);
      router.refresh();
    },
    [router],
  );

  const refreshProfile = useCallback(async () => {
    await profile.mutate();
  }, [profile]);

  const signOut = useCallback(async () => {
    try {
      await auth.logout();
    } catch {
      // A failed logout still clears local state; the refresh token rotates
      // out of validity on its own.
    }
    clearSession();
    router.push("/login");
  }, [router]);

  const workspaces = useMemo(
    () => (session ? (profile.data?.workspaces ?? EMPTY_WORKSPACES) : EMPTY_WORKSPACES),
    [session, profile.data],
  );

  const workspace = useMemo(() => {
    if (!session) return null;
    return workspaces.find((item) => item.id === session.workspace_id) ?? null;
  }, [session, workspaces]);

  const status: Status =
    session === undefined
      ? "loading"
      : session === null
        ? "anonymous"
        : // A failed profile fetch that was not a 401 should not lock the user
          // out of the console; render it and let each screen show its error.
          profile.data || profile.error
          ? "authenticated"
          : "loading";

  const value = useMemo<SessionContextValue>(
    () => ({
      status,
      user: session ? (profile.data?.user ?? null) : null,
      workspace,
      workspaces,
      session: session ?? null,
      adoptTokens,
      switchWorkspace,
      refreshProfile,
      signOut,
    }),
    [
      status,
      session,
      profile.data,
      workspace,
      workspaces,
      adoptTokens,
      switchWorkspace,
      refreshProfile,
      signOut,
    ],
  );

  return (
    <SessionContext.Provider value={value}>{children}</SessionContext.Provider>
  );
}

export function useSession(): SessionContextValue {
  const context = useContext(SessionContext);
  if (!context) {
    throw new Error("useSession must be used inside <SessionProvider>");
  }
  return context;
}
