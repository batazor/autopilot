"use client";

import { useMemo, useState } from "react";
import { PageHeader } from "@/components/PageHeader";
import { AppTabs } from "@/components/headless";
import { BUILDING_GAMES } from "@/lib/buildings-games";
import {
  CATEGORY_LABEL,
  type Building,
  type BuildingCategory,
  type BuildingGame,
} from "@/lib/buildings-types";

const CATEGORY_ORDER: BuildingCategory[] = [
  "inner",
  "military",
  "resource",
  "other",
];

/** Furnace upgrade dependency spine: each hub level + the buildings it needs. */
function HubSpine({ game }: { game: BuildingGame }) {
  const nameOf = useMemo(() => {
    const map = new Map(game.buildings.map((b) => [b.id, b.name]));
    return (id: string) => map.get(id) ?? id;
  }, [game]);

  const hubName = nameOf(game.hubId);

  return (
    <section className="panel">
      <div className="fleet-section__head">
        <h2>{hubName} upgrade requirements</h2>
        <span className="fleet-count">max Lv {game.hubMaxLevel}</span>
      </div>
      <p className="muted mb-3 text-sm">
        {hubName} level gates and caps every other building. To advance it, the
        listed buildings must first reach the shown level.
      </p>

      <ol className="relative ml-3 border-l-2 border-[color:var(--wos-border-hover)] pl-5">
        {game.hubRequirements.map((req) => (
          <li key={req.hubLevel} className="relative mb-4 last:mb-0">
            <span
              className="absolute -left-[1.65rem] top-1 h-3 w-3 rounded-full border-2"
              style={{
                background: "var(--wos-accent)",
                borderColor: "var(--wos-bg)",
              }}
            />
            <div className="flex flex-wrap items-center gap-2">
              <span
                className="rounded px-2 py-0.5 text-sm font-semibold"
                style={{
                  background: "var(--wos-status-info-bg)",
                  color: "var(--wos-status-info-fg)",
                }}
              >
                {hubName} Lv {req.hubLevel}
              </span>
              <span className="text-xs text-wos-text-muted">needs</span>
              {req.requires.map((r) => (
                <span
                  key={`${r.building}-${r.level}`}
                  className="rounded-lg border px-2 py-0.5 text-sm"
                  style={{
                    background: "var(--wos-panel-raised)",
                    borderColor: "var(--wos-border)",
                  }}
                >
                  {nameOf(r.building)}{" "}
                  <span className="font-semibold">Lv {r.level}</span>
                </span>
              ))}
            </div>
          </li>
        ))}
      </ol>

      {game.note ? (
        <p className="muted mt-4 text-xs">{game.note}</p>
      ) : null}
    </section>
  );
}

function BuildingCard({ b }: { b: Building }) {
  return (
    <div
      className="flex items-center justify-between gap-2 rounded-lg border p-2 text-sm"
      style={{
        background: "var(--wos-panel-raised)",
        borderColor: "var(--wos-border)",
      }}
    >
      <span className="truncate font-medium" title={b.name}>
        {b.name}
      </span>
      {b.maxLevel ? (
        <span className="shrink-0 text-xs text-wos-text-muted">
          max Lv {b.maxLevel}
        </span>
      ) : null}
    </div>
  );
}

function BuildingCatalog({ game }: { game: BuildingGame }) {
  return (
    <section className="panel">
      <h2>Buildings ({game.buildings.length})</h2>
      <div className="mt-3 flex flex-col gap-4">
        {CATEGORY_ORDER.map((cat) => {
          const items = game.buildings.filter((b) => b.category === cat);
          if (!items.length) return null;
          return (
            <div key={cat}>
              <h3 className="mb-2 text-sm font-semibold text-wos-text-secondary">
                {CATEGORY_LABEL[cat]} ({items.length})
              </h3>
              <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
                {items.map((b) => (
                  <BuildingCard key={b.id} b={b} />
                ))}
              </div>
            </div>
          );
        })}
      </div>
    </section>
  );
}

export default function BuildingsPage() {
  const [gameId, setGameId] = useState(BUILDING_GAMES[0]!.id);
  const game = BUILDING_GAMES.find((g) => g.id === gameId) ?? BUILDING_GAMES[0]!;

  return (
    <>
      <PageHeader title="Buildings">
        <p className="muted m-0">
          Building level dependencies per game — sourced from{" "}
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

      {BUILDING_GAMES.length > 1 ? (
        <AppTabs
          renderPanels={false}
          selectedKey={gameId}
          onChange={setGameId}
          tabs={BUILDING_GAMES.map((g) => ({
            key: g.id,
            label: g.label,
            title: g.id,
          }))}
        />
      ) : null}

      <div className="flex flex-col gap-4">
        <HubSpine game={game} />
        <BuildingCatalog game={game} />
      </div>
    </>
  );
}
