"use client";

import { useCallback, useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { PageHeader } from "@/components/PageHeader";
import { PageLoading } from "@/components/ui";
import { AppListbox, AppTabs } from "@/components/headless";
import {
  fetchAllianceTech,
  fetchBuildings,
  fetchPlayers,
  fetchResearch,
  fetchTreeProgress,
  type TreeProgress,
} from "@/lib/api";
import type { BuildingsView, ResearchView } from "@/lib/types";
import { BuildingsPanel } from "./BuildingsPanel";
import { ResearchPanel } from "./ResearchPanel";

type DataType = "research" | "buildings" | "alliance";

function parseDataType(raw: string | null): DataType {
  return raw === "buildings" || raw === "alliance" ? raw : "research";
}

export function TreesContent() {
  const params = useSearchParams();
  const research = useQuery<ResearchView>({
    queryKey: ["research"],
    queryFn: fetchResearch,
  });
  const buildings = useQuery<BuildingsView>({
    queryKey: ["buildings"],
    queryFn: fetchBuildings,
  });
  const allianceTech = useQuery<ResearchView>({
    queryKey: ["alliance-tech"],
    queryFn: fetchAllianceTech,
  });

  const games = research.data?.games ?? [];
  const buildingsGameId = buildings.data?.game ?? "wos";

  // Navigation state lives in the URL query (?game=&tab=&branch=) so views are
  // shareable/bookmarkable. We mirror local state to it via the History API
  // (router.replace soft-navigates and would drop the useSearchParams updates).
  const [gameId, setGameId] = useState<string | null>(params.get("game"));
  const [type, setType] = useState<DataType>(parseDataType(params.get("tab")));
  const [branchId, setBranchId] = useState<string | null>(params.get("branch"));
  const [playerId, setPlayerId] = useState<string>(params.get("player") ?? "");

  const players = useQuery<string[]>({
    queryKey: ["players"],
    queryFn: () => fetchPlayers(),
  });
  const progress = useQuery<TreeProgress>({
    queryKey: ["tree-progress", playerId],
    queryFn: () => fetchTreeProgress(playerId),
    enabled: Boolean(playerId),
  });

  const game = games.find((g) => g.id === gameId) ?? games[0];
  const allianceGame = (allianceTech.data?.games ?? []).find(
    (g) => g.id === (game?.id ?? "wos"),
  );

  const syncUrl = useCallback(
    (next: {
      game?: string;
      tab?: DataType;
      branch?: string | null;
      player?: string | null;
    }) => {
      const url = new URL(window.location.href);
      if (next.game !== undefined) url.searchParams.set("game", next.game);
      if (next.tab !== undefined) url.searchParams.set("tab", next.tab);
      if (next.branch !== undefined) {
        if (next.branch) url.searchParams.set("branch", next.branch);
        else url.searchParams.delete("branch");
      }
      if (next.player !== undefined) {
        if (next.player) url.searchParams.set("player", next.player);
        else url.searchParams.delete("player");
      }
      window.history.replaceState(null, "", url.pathname + url.search);
    },
    [],
  );

  // Adopt the URL on external navigation (back/forward, a shared link).
  useEffect(() => {
    const g = params.get("game");
    const t = params.get("tab");
    const b = params.get("branch");
    if (g) setGameId(g);
    if (t) setType(parseDataType(t));
    setBranchId(b);
  }, [params]);

  const onGameChange = (next: string) => {
    setGameId(next);
    syncUrl({ game: next });
  };
  const onTypeChange = (next: DataType) => {
    setType(next);
    syncUrl({ tab: next });
  };
  const onBranchChange = (next: string) => {
    setBranchId(next);
    syncUrl({ branch: next });
  };
  const onPlayerChange = (next: string) => {
    setPlayerId(next);
    syncUrl({ player: next || null });
  };

  const isLoading =
    research.isLoading || buildings.isLoading || allianceTech.isLoading;
  const error = research.error ?? buildings.error ?? allianceTech.error;

  return (
    <>
      <PageHeader title="Game trees">
        <p className="muted m-0">
          Research &amp; building dependency trees per game — served from{" "}
          <code>games/&lt;game&gt;/db/</code> (single source of truth).
        </p>
      </PageHeader>

      {error ? (
        <div className="error-banner">
          {error instanceof Error ? error.message : String(error)}
        </div>
      ) : null}
      {isLoading ? <PageLoading /> : null}

      {game ? (
        <>
          {games.length > 1 ? (
            <AppTabs
              renderPanels={false}
              selectedKey={game.id}
              onChange={onGameChange}
              tabs={games.map((g) => ({ key: g.id, label: g.label, title: g.id }))}
            />
          ) : null}

          <AppTabs
            variant="toolbar"
            renderPanels={false}
            selectedKey={type}
            onChange={(k) => onTypeChange(k as DataType)}
            tabs={[
              { key: "research", label: "Research" },
              { key: "buildings", label: "Buildings" },
              { key: "alliance", label: "Alliance tech" },
            ]}
            afterTabs={
              <AppListbox
                inline
                label="Player"
                value={playerId}
                onChange={onPlayerChange}
                loading={players.isLoading}
                minWidth={160}
                options={[
                  { value: "", label: "— no progress —" },
                  ...(players.data ?? []).map((p) => ({ value: p, label: p })),
                ]}
              />
            }
          />

          <div className="mt-3">
            {type === "research" ? (
              <ResearchPanel
                key={game.id}
                game={game}
                branchId={branchId}
                onBranch={onBranchChange}
                progress={progress.data}
              />
            ) : type === "alliance" ? (
              allianceGame ? (
                <ResearchPanel
                  key={`alliance-${allianceGame.id}`}
                  game={allianceGame}
                  branchId={branchId}
                  onBranch={onBranchChange}
                  progress={progress.data}
                  exportPrefix="alliance-tech"
                />
              ) : (
                <p className="muted">No alliance tech data for {game.label} yet.</p>
              )
            ) : buildings.data && game.id === buildingsGameId ? (
              <BuildingsPanel
                view={buildings.data}
                progress={progress.data}
                playerId={playerId}
              />
            ) : (
              <p className="muted">No building data for {game.label} yet.</p>
            )}
          </div>
        </>
      ) : null}
    </>
  );
}
