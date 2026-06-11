"use client";

import { useMemo, useState } from "react";
import { PageHeader } from "@/components/PageHeader";
import { AppTabs } from "@/components/headless";
import { RESEARCH_GAMES } from "@/lib/research-games";
import {
  branchTotalLevels,
  type ResearchBranch,
  type ResearchNode,
} from "@/lib/research-types";

const NODE_W = 176;
const NODE_H = 84;
const COL_GAP = 64;
const ROW_GAP = 24;

const ROMAN = ["", "I", "II", "III", "IV", "V", "VI", "VII", "VIII"];

type Placed = ResearchNode & { x: number; y: number; row: number };

/** Lay nodes out in columns by tier; row = order among same-tier nodes. */
function layout(branch: ResearchBranch): {
  placed: Placed[];
  byId: Map<string, Placed>;
  width: number;
  height: number;
} {
  const rowOf = new Map<number, number>(); // tier -> next free row
  const placed: Placed[] = branch.nodes.map((n) => {
    const row = rowOf.get(n.tier) ?? 0;
    rowOf.set(n.tier, row + 1);
    return {
      ...n,
      row,
      x: (n.tier - 1) * (NODE_W + COL_GAP),
      y: row * (NODE_H + ROW_GAP),
    };
  });
  const byId = new Map(placed.map((p) => [p.id, p]));
  const maxTier = Math.max(...placed.map((p) => p.tier));
  const maxRow = Math.max(...placed.map((p) => p.row)) + 1;
  return {
    placed,
    byId,
    width: maxTier * (NODE_W + COL_GAP) - COL_GAP,
    height: maxRow * (NODE_H + ROW_GAP) - ROW_GAP,
  };
}

function ResearchTreeGraph({ branch }: { branch: ResearchBranch }) {
  const { placed, byId, width, height } = useMemo(() => layout(branch), [branch]);

  return (
    <div className="panel overflow-x-auto">
      <div
        className="relative mx-auto"
        style={{ width, height, minWidth: width }}
      >
        <svg
          className="pointer-events-none absolute inset-0"
          width={width}
          height={height}
        >
          {placed.flatMap((node) =>
            node.requires
              .map((reqId) => byId.get(reqId))
              .filter((p): p is Placed => Boolean(p))
              .map((parent) => {
                const x1 = parent.x + NODE_W;
                const y1 = parent.y + NODE_H / 2;
                const x2 = node.x;
                const y2 = node.y + NODE_H / 2;
                const mx = (x1 + x2) / 2;
                return (
                  <path
                    key={`${parent.id}-${node.id}`}
                    d={`M ${x1} ${y1} C ${mx} ${y1}, ${mx} ${y2}, ${x2} ${y2}`}
                    fill="none"
                    stroke="var(--wos-border-hover)"
                    strokeWidth={2}
                  />
                );
              }),
          )}
        </svg>

        {placed.map((node) => (
          <div
            key={node.id}
            className="absolute flex flex-col gap-1 rounded-lg border p-2 shadow-sm"
            style={{
              left: node.x,
              top: node.y,
              width: NODE_W,
              height: NODE_H,
              background: "var(--wos-panel-raised)",
              borderColor: "var(--wos-border)",
            }}
          >
            <div className="flex items-center justify-between gap-2">
              <span className="truncate text-sm font-medium" title={node.name}>
                {node.name}
              </span>
              <span
                className="shrink-0 rounded px-1.5 py-0.5 text-[10px] font-semibold"
                style={{
                  background: "var(--wos-status-info-bg)",
                  color: "var(--wos-status-info-fg)",
                }}
                title={`Tier ${ROMAN[node.tier]}`}
              >
                {ROMAN[node.tier]}
              </span>
            </div>
            <span className="truncate text-xs text-wos-text-muted" title={node.bonus}>
              {node.bonus}
            </span>
            <span className="mt-auto text-[11px] text-wos-text-secondary">
              0 / {node.levels} levels
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

export default function ResearchTreePage() {
  const [gameId, setGameId] = useState(RESEARCH_GAMES[0]!.id);
  const [branchId, setBranchId] = useState<string>(
    RESEARCH_GAMES[0]!.branches[0]!.id,
  );

  const game = RESEARCH_GAMES.find((g) => g.id === gameId) ?? RESEARCH_GAMES[0]!;
  const branch =
    game.branches.find((b) => b.id === branchId) ?? game.branches[0]!;

  // Switching game keeps the same branch when it exists, else falls back to the
  // first branch of the new game.
  const onGameChange = (next: string) => {
    setGameId(next);
    const nextGame = RESEARCH_GAMES.find((g) => g.id === next);
    if (nextGame && !nextGame.branches.some((b) => b.id === branchId)) {
      setBranchId(nextGame.branches[0]!.id);
    }
  };

  return (
    <>
      <PageHeader title="Research tree">
        <p className="muted m-0">
          Curated research reference per game — sourced from{" "}
          <a
            className="underline"
            href={game.sourceUrl}
            target="_blank"
            rel="noreferrer"
          >
            {game.sourceLabel}
          </a>
          . Edit the per-game data file to correct it.
        </p>
      </PageHeader>

      <AppTabs
        renderPanels={false}
        selectedKey={gameId}
        onChange={onGameChange}
        tabs={RESEARCH_GAMES.map((g) => ({
          key: g.id,
          label: g.label,
          title: g.id,
        }))}
      />

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

      <ResearchTreeGraph branch={branch} />
    </>
  );
}
