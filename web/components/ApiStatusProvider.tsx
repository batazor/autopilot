"use client";

import { useQuery } from "@tanstack/react-query";
import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  type ReactNode,
} from "react";
import { fetchHealth } from "@/lib/api";
import type { HealthView } from "@/lib/types";

const HEALTH_POLL_MS = 5000;

export type ApiConnectivity = "checking" | "ok" | "api_offline" | "redis_unreachable";

export type ApiStatusContextValue = {
  connectivity: ApiConnectivity;
  health: HealthView | null;
  refresh: () => Promise<void>;
};

const ApiStatusContext = createContext<ApiStatusContextValue | null>(null);

function connectivityFrom(
  health: HealthView | null,
  fetchFailed: boolean,
  checking: boolean,
): ApiConnectivity {
  if (checking) return "checking";
  if (fetchFailed) return "api_offline";
  if (health?.redis === "unreachable") return "redis_unreachable";
  return "ok";
}

export function ApiStatusProvider({ children }: { children: ReactNode }) {
  const query = useQuery<HealthView>({
    queryKey: ["health"],
    queryFn: fetchHealth,
    refetchInterval: HEALTH_POLL_MS,
  });

  const refresh = useCallback(async () => {
    await query.refetch();
  }, [query]);

  const health = query.data ?? null;
  const fetchFailed = query.isError;
  const checking = !query.isFetchedAfterMount && query.isFetching;
  const connectivity = connectivityFrom(health, fetchFailed, checking);

  const value = useMemo(
    () => ({ connectivity, health, refresh }),
    [connectivity, health, refresh],
  );

  return (
    <ApiStatusContext.Provider value={value}>{children}</ApiStatusContext.Provider>
  );
}

export function useApiStatus(): ApiStatusContextValue {
  const ctx = useContext(ApiStatusContext);
  if (!ctx) {
    throw new Error("useApiStatus must be used within ApiStatusProvider");
  }
  return ctx;
}

/**
 * True when the whole API is unreachable. The global ``ApiStatusIndicator``
 * already announces this ("API offline"), so per-widget fetch-error banners
 * should suppress themselves on this signal — one place is enough instead of
 * every failing query repeating the same "… 500 …" message.
 */
export function useApiOffline(): boolean {
  return useApiStatus().connectivity === "api_offline";
}
