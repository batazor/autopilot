"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { fetchInstanceGames, setActiveGame } from "@/lib/api";
import { useInstances, usePlayers } from "@/lib/hooks";
import {
  loadFleetInstanceId,
  loadFleetPlayerId,
  saveFleetInstanceId,
  saveFleetPlayerId,
} from "@/lib/fleet-prefs";

// Keep in sync with config/games.py::GAMES — defaults match
// :func:`config.games.default_game` so untyped UI paths land on WOS.
export const KNOWN_GAMES = ["wos", "kingshot"] as const;
export type KnownGame = (typeof KNOWN_GAMES)[number];
export const DEFAULT_GAME: KnownGame = "wos";

export type FleetContextValue = {
  instances: string[];
  players: string[];
  instanceId: string;
  playerId: string;
  game: string;
  instanceGames: Record<string, string>;
  setInstanceId: (id: string) => void;
  setPlayerId: (id: string) => void;
  setGame: (game: string) => void;
  refreshPlayers: () => Promise<string[]>;
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
  const urlGame = searchParams.get("game");

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
    refresh: refreshPlayers,
  } = usePlayers({
    preferPlayerId: urlPlayerId,
    getPersistedPlayerId: loadFleetPlayerId,
    instanceId: instanceId || undefined,
  });

  // ``instanceGames`` is the device→game registry fetched from
  // ``/api/instances/games``. It seeds the ``game`` value when the URL
  // doesn't pin one and falls back to ``DEFAULT_GAME`` while loading or if
  // the instance is unknown (new device not yet in the registry).
  const [instanceGames, setInstanceGames] = useState<Record<string, string>>({});
  const [gameOverride, setGameOverride] = useState<string>("");

  useEffect(() => {
    let cancelled = false;
    fetchInstanceGames()
      .then((map) => {
        if (!cancelled) setInstanceGames(map);
      })
      .catch(() => {
        // Silently fall back to DEFAULT_GAME — the picker still works.
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const game = useMemo(() => {
    if (gameOverride) return gameOverride;
    if (urlGame) return urlGame;
    const fromInstance = instanceGames[instanceId];
    if (fromInstance) return fromInstance;
    return DEFAULT_GAME;
  }, [gameOverride, urlGame, instanceGames, instanceId]);

  // Keep ``lib/api``'s active-game cache in sync so module-scoped query
  // builders (``labelingScopeQuery``, etc.) emit ``?game=`` automatically
  // without each callsite threading it.
  useEffect(() => {
    setActiveGame(game);
  }, [game]);

  const replaceQuery = useCallback(
    (patch: { instanceId?: string; playerId?: string; game?: string }) => {
      const params = new URLSearchParams(searchParams.toString());
      if (patch.instanceId !== undefined) {
        if (patch.instanceId) params.set("instance_id", patch.instanceId);
        else params.delete("instance_id");
      }
      if (patch.playerId !== undefined) {
        if (patch.playerId) params.set("player_id", patch.playerId);
        else params.delete("player_id");
      }
      if (patch.game !== undefined) {
        if (patch.game) params.set("game", patch.game);
        else params.delete("game");
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
      // Clearing the override lets the selected instance's registered game
      // take precedence after the user picks a different device.
      setGameOverride("");
      replaceQuery({ instanceId: id, game: "" });
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

  const setGame = useCallback(
    (next: string) => {
      const value = (next || "").trim();
      setGameOverride(value);
      replaceQuery({ game: value });
    },
    [replaceQuery],
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
      game,
      instanceGames,
      setInstanceId,
      setPlayerId,
      setGame,
      refreshPlayers,
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
      game,
      instanceGames,
      setInstanceId,
      setPlayerId,
      setGame,
      refreshPlayers,
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
