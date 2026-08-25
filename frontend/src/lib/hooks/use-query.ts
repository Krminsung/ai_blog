"use client";

import useSWR, { type SWRConfiguration } from "swr";
import { useCallback, useState } from "react";

import { ApiError, errorMessage } from "@/lib/api/errors";

/**
 * Thin wrapper over SWR so every screen shares one loading/error contract.
 *
 * `key` is `null` when the request should not fire yet (missing route param,
 * unauthenticated), which SWR treats as "skip".
 */
export function useApi<T>(
  key: string | readonly unknown[] | null,
  fetcher: () => Promise<T>,
  config?: SWRConfiguration<T, ApiError>,
) {
  const { data, error, isLoading, isValidating, mutate } = useSWR<T, ApiError>(
    key,
    fetcher,
    {
      revalidateOnFocus: false,
      shouldRetryOnError: (err) =>
        // 4xx responses are deterministic — retrying just burns quota.
        !(err instanceof ApiError) || err.status >= 500,
      ...config,
    },
  );

  return {
    data,
    error,
    isLoading,
    isValidating,
    mutate,
    errorText: error ? errorMessage(error) : null,
  };
}

/**
 * Polls while `isActive(data)` holds — used for the long-running job screens
 * (generation, publishing, bulk) that have no push channel.
 */
export function usePolledApi<T>(
  key: string | readonly unknown[] | null,
  fetcher: () => Promise<T>,
  isActive: (data: T | undefined) => boolean,
  intervalMs = 4000,
) {
  return useApi<T>(key, fetcher, {
    refreshInterval: (latest) => (isActive(latest) ? intervalMs : 0),
  });
}

interface MutationState {
  isPending: boolean;
  error: string | null;
  fieldErrors: Record<string, string>;
}

/**
 * Imperative writes with the error shape forms need: a top-level message plus
 * per-field reasons pulled out of the backend's `fields` array.
 */
export function useMutation<TArgs extends unknown[], TResult>(
  action: (...args: TArgs) => Promise<TResult>,
) {
  const [state, setState] = useState<MutationState>({
    isPending: false,
    error: null,
    fieldErrors: {},
  });

  const run = useCallback(
    async (...args: TArgs): Promise<TResult | null> => {
      setState({ isPending: true, error: null, fieldErrors: {} });
      try {
        const result = await action(...args);
        setState({ isPending: false, error: null, fieldErrors: {} });
        return result;
      } catch (error) {
        setState({
          isPending: false,
          error: errorMessage(error),
          fieldErrors: error instanceof ApiError ? error.fieldErrors() : {},
        });
        return null;
      }
    },
    [action],
  );

  const reset = useCallback(
    () => setState({ isPending: false, error: null, fieldErrors: {} }),
    [],
  );

  return { ...state, run, reset };
}
