"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  type ReactNode,
} from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useInstances, usePlayers } from "@/lib/hooks";
import {
  loadFleetInstanceId,
  loadFleetPlayerId,
  saveFleetInstanceId,
  saveFleetPlayerId,
} from "@/lib/fleet-prefs";

export type FleetContextValue = {
  instances: string[];
  players: string[];
  instanceId: string;
  playerId: string;
  setInstanceId: (id: string) => void;
  setPlayerId: (id: string) => void;
  instancesLoading: boolean;
  playersLoading: boolean;
  instancesError: string | null;
  playersError: string | null;
};

const FleetContext = createContext<FleetContextValue | null>(null);

export function FleetContextProvider({ children }: { children: ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();

  const urlInstanceId = searchParams.get("instance_id");
  const urlPlayerId = searchParams.get("player_id");

  const {
    instances,
    instanceId,
    setInstanceId: setInstanceIdState,
    loading: instancesLoading,
    error: instancesError,
  } = useInstances({
    preferInstanceId: urlInstanceId,
    getPersistedInstanceId: loadFleetInstanceId,
  });

  const {
    players,
    playerId,
    setPlayerId: setPlayerIdState,
    loading: playersLoading,
    error: playersError,
  } = usePlayers({
    preferPlayerId: urlPlayerId,
    getPersistedPlayerId: loadFleetPlayerId,
  });

  const replaceQuery = useCallback(
    (patch: { instanceId?: string; playerId?: string }) => {
      const params = new URLSearchParams(searchParams.toString());
      if (patch.instanceId !== undefined) {
        if (patch.instanceId) params.set("instance_id", patch.instanceId);
        else params.delete("instance_id");
      }
      if (patch.playerId !== undefined) {
        if (patch.playerId) params.set("player_id", patch.playerId);
        else params.delete("player_id");
      }
      const q = params.toString();
      router.replace(q ? `${pathname}?${q}` : pathname, { scroll: false });
    },
    [pathname, router, searchParams],
  );

  const setInstanceId = useCallback(
    (id: string) => {
      setInstanceIdState(id);
      saveFleetInstanceId(id);
      replaceQuery({ instanceId: id });
    },
    [replaceQuery, setInstanceIdState],
  );

  const setPlayerId = useCallback(
    (id: string) => {
      setPlayerIdState(id);
      saveFleetPlayerId(id);
      replaceQuery({ playerId: id });
    },
    [replaceQuery, setPlayerIdState],
  );

  useEffect(() => {
    if (!instances.length) return;
    if (urlInstanceId && instances.includes(urlInstanceId)) {
      setInstanceIdState(urlInstanceId);
      saveFleetInstanceId(urlInstanceId);
    }
  }, [urlInstanceId, instances, setInstanceIdState]);

  useEffect(() => {
    if (!players.length) return;
    if (urlPlayerId && players.includes(urlPlayerId)) {
      setPlayerIdState(urlPlayerId);
      saveFleetPlayerId(urlPlayerId);
    }
  }, [urlPlayerId, players, setPlayerIdState]);

  const value = useMemo(
    () => ({
      instances,
      players,
      instanceId,
      playerId,
      setInstanceId,
      setPlayerId,
      instancesLoading,
      playersLoading,
      instancesError,
      playersError,
    }),
    [
      instances,
      players,
      instanceId,
      playerId,
      setInstanceId,
      setPlayerId,
      instancesLoading,
      playersLoading,
      instancesError,
      playersError,
    ],
  );

  return (
    <FleetContext.Provider value={value}>{children}</FleetContext.Provider>
  );
}

export function useFleet(): FleetContextValue {
  const ctx = useContext(FleetContext);
  if (!ctx) {
    throw new Error("useFleet must be used within FleetContextProvider");
  }
  return ctx;
}

/** Optional fleet context for pages outside Operate/Debug layouts. */
export function useFleetOptional(): FleetContextValue | null {
  return useContext(FleetContext);
}
