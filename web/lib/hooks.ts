"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { fetchInstances, fetchPlayers } from "@/lib/api";

/** True while the browser tab is in the foreground. */
export function useDocumentVisible(): boolean {
  const [visible, setVisible] = useState(() =>
    typeof document === "undefined"
      ? true
      : document.visibilityState !== "hidden",
  );

  useEffect(() => {
    const onChange = () =>
      setVisible(document.visibilityState !== "hidden");
    document.addEventListener("visibilitychange", onChange);
    return () => document.removeEventListener("visibilitychange", onChange);
  }, []);

  return visible;
}

type UseInstancesOptions = {
  /** Initial selection before the list loads. */
  initialInstanceId?: string;
  /** When the list arrives, select this id if it exists (e.g. URL query). */
  preferInstanceId?: string | null;
  /** Client-only persisted id (e.g. localStorage); read inside effects, not during render. */
  getPersistedInstanceId?: () => string;
};

export function useInstances(options: UseInstancesOptions = {}) {
  const {
    initialInstanceId = "",
    preferInstanceId = null,
    getPersistedInstanceId,
  } = options;
  const [instances, setInstances] = useState<string[]>([]);
  const [instanceId, setInstanceId] = useState(initialInstanceId);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    fetchInstances()
      .then((ids) => {
        if (cancelled) return;
        setInstances(ids);
        setInstanceId((current) => {
          if (preferInstanceId && ids.includes(preferInstanceId)) {
            return preferInstanceId;
          }
          const persisted = getPersistedInstanceId?.()?.trim() ?? "";
          if (persisted && ids.includes(persisted)) return persisted;
          if (current && ids.includes(current)) return current;
          return ids[0] ?? "";
        });
        setError(null);
      })
      .catch((e) => {
        if (!cancelled) {
          setError(e instanceof Error ? e.message : String(e));
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
    // Fetch the instance list once per mount — not on every instanceId change.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (preferInstanceId && instances.includes(preferInstanceId)) {
      setInstanceId(preferInstanceId);
    }
  }, [preferInstanceId, instances]);

  return { instances, instanceId, setInstanceId, loading, error };
}

type UsePlayersOptions = {
  initialPlayerId?: string;
  preferPlayerId?: string | null;
  /** Client-only persisted id (e.g. localStorage); read inside effects, not during render. */
  getPersistedPlayerId?: () => string;
};

export function usePlayers(options: UsePlayersOptions = {}) {
  const {
    initialPlayerId = "",
    preferPlayerId = null,
    getPersistedPlayerId,
  } = options;
  const [players, setPlayers] = useState<string[]>([]);
  const [playerId, setPlayerId] = useState(initialPlayerId);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    fetchPlayers()
      .then((ids) => {
        if (cancelled) return;
        setPlayers(ids);
        setPlayerId((current) => {
          if (preferPlayerId && ids.includes(preferPlayerId)) {
            return preferPlayerId;
          }
          const persisted = getPersistedPlayerId?.()?.trim() ?? "";
          if (persisted && ids.includes(persisted)) return persisted;
          if (current && ids.includes(current)) return current;
          return ids[0] ?? "";
        });
        setError(null);
      })
      .catch((e) => {
        if (!cancelled) {
          setError(e instanceof Error ? e.message : String(e));
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (preferPlayerId && players.includes(preferPlayerId)) {
      setPlayerId(preferPlayerId);
    }
  }, [preferPlayerId, players]);

  return { players, playerId, setPlayerId, loading, error };
}

/** Bump a string/number cache key only when `next` differs from the last seen value. */
export function useStableCacheKey(
  next: string | number | null | undefined,
): string | number | undefined {
  const ref = useRef<string | number | undefined>(undefined);
  if (next == null || next === "") return undefined;
  if (ref.current !== next) {
    ref.current = next;
  }
  return ref.current;
}
