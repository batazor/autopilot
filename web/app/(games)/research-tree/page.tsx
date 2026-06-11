"use client";

import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { PageHeader } from "@/components/PageHeader";
import { AppTabs } from "@/components/headless";
import { TechTreeFlow, type FlowTreeNode } from "@/components/TechTreeFlow";
import { fetchResearch } from "@/lib/api";
import type { ResearchBranchView, ResearchView } from "@/lib/types";

const ROMAN = ["", "I", "II", "III", "IV", "V", "VI", "VII", "VIII"];

function branchTotalLevels(branch: ResearchBranchView): number {
  return branch.nodes.reduce((sum, n) => sum + n.levels, 0);
}

function toFlowNodes(branch: ResearchBranchView): FlowTreeNode[] {
  return branch.nodes.map((n) => ({
    id: n.id,
    tier: n.tier,
    title: n.name,
    subtitle: n.bonus,
    footer: `0 / ${n.levels} levels`,
    badge: ROMAN[n.tier] ?? String(n.tier),
    requires: n.requires,
  }));
}

export default function ResearchTreePage() {
  const { data, error, isLoading } = useQuery<ResearchView>({
    queryKey: ["research"],
    queryFn: fetchResearch,
  });

  const games = data?.games ?? [];
  const [gameId, setGameId] = useState<string | null>(null);
  const [branchId, setBranchId] = useState<string | null>(null);

  const game = games.find((g) => g.id === gameId) ?? games[0];
  const branch =
    game?.branches.find((b) => b.id === branchId) ?? game?.branches[0];

  const flowNodes = useMemo(
    () => (branch ? toFlowNodes(branch) : []),
    [branch],
  );

  const onGameChange = (next: string) => {
    setGameId(next);
    const nextGame = games.find((g) => g.id === next);
    if (nextGame && !nextGame.branches.some((b) => b.id === branchId)) {
      setBranchId(nextGame.branches[0]?.id ?? null);
    }
  };

  return (
    <>
      <PageHeader title="Research tree">
        <p className="muted m-0">
          Curated research reference per game — served from{" "}
          <code>games/&lt;game&gt;/db/research.yaml</code>
          {game ? (
            <>
              {" "}
              · sourced from{" "}
              <a
                className="underline"
                href={game.source_url}
                target="_blank"
                rel="noreferrer"
              >
                {game.source_label}
              </a>
            </>
          ) : null}
          .
        </p>
      </PageHeader>

      {error ? (
        <div className="error-banner">
          {error instanceof Error ? error.message : String(error)}
        </div>
      ) : null}
      {isLoading ? <p className="muted">Loading…</p> : null}

      {game && branch ? (
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
            variant="section"
            renderPanels={false}
            selectedKey={branch.id}
            onChange={(k) => setBranchId(k)}
            tabs={game.branches.map((b) => ({
              key: b.id,
              label: `${b.label} (${branchTotalLevels(b)})`,
              title: b.blurb,
            }))}
          />

          <p className="muted mb-3 mt-1 text-sm">{branch.blurb}</p>

          <TechTreeFlow nodes={flowNodes} />
        </>
      ) : null}
    </>
  );
}
