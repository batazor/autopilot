import { useEffect, useRef } from "react";

/**
 * Declarative `setInterval`: invokes `callback` every `delay` ms, always calling
 * the latest `callback` without restarting the timer (avoids stale-closure bugs).
 * Pass `delay = null` to pause — the canonical replacement for
 * `useEffect(() => { if (!enabled) return; const id = setInterval(...); return
 * () => clearInterval(id); }, [...])`.
 */
export function useInterval(callback: () => void, delay: number | null): void {
  const saved = useRef(callback);
  useEffect(() => {
    saved.current = callback;
  }, [callback]);
  useEffect(() => {
    if (delay === null) return;
    const id = setInterval(() => saved.current(), delay);
    return () => clearInterval(id);
  }, [delay]);
}
