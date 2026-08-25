"use client";

import { useSyncExternalStore } from "react";

/** No client-side source of truth to watch — the value flips once, at mount. */
const noopSubscribe = () => () => {};

/**
 * `false` during SSR and the hydration render, `true` afterwards.
 *
 * Anything that reaches outside the React tree at render time — portals in
 * particular — must not emit markup on the server, or hydration diverges.
 * Reading through `useSyncExternalStore` gives that without a state-setting
 * effect.
 */
export function useIsMounted(): boolean {
  return useSyncExternalStore(
    noopSubscribe,
    () => true,
    () => false,
  );
}
