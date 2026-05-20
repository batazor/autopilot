"use client";

import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { fetchHealth } from "@/lib/api";
import { usePollWhenVisible } from "@/lib/hooks";
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
  const [health, setHealth] = useState<HealthView | null>(null);
  const [fetchFailed, setFetchFailed] = useState(false);
  const [checkedOnce, setCheckedOnce] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const view = await fetchHealth();
      setHealth(view);
      setFetchFailed(false);
    } catch {
      setHealth(null);
      setFetchFailed(true);
    } finally {
      setCheckedOnce(true);
    }
  }, []);

  usePollWhenVisible(refresh, HEALTH_POLL_MS);

  const connectivity = connectivityFrom(health, fetchFailed, !checkedOnce);

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
