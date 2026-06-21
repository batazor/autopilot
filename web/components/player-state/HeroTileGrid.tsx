"use client";

import Link from "next/link";
import { useState } from "react";
import { wikiIconUrl } from "@/lib/api";
import { wikiHeroHref } from "@/lib/wiki-links";
import type { HeroStateRow } from "@/lib/types";

function HeroTile({ row, locked }: { row: HeroStateRow; locked: boolean }) {
  const [iconOk, setIconOk] = useState(true);
  const tags: string[] = [];
  if (row.red_dot) tags.push("●");
  if (row.upgrade) tags.push("↑");

  return (
    <Link
      href={wikiHeroHref(row.id)}
      className="wiki-tile player-hero-tile"
      title={`Open wiki: ${row.hero}`}
    >
      {iconOk ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img
          src={wikiIconUrl("heroes", row.id)}
          alt=""
          className="wiki-tile__img"
          onError={() => setIconOk(false)}
        />
      ) : (
        <div className="wiki-tile__placeholder">?</div>
      )}
      <span className="wiki-tile__name">{row.hero}</span>
      <span className="player-hero-tile__id">{row.id}</span>
      {tags.length ? (
        <span className="player-hero-tile__tags">{tags.join(" ")}</span>
      ) : null}
      {locked ? (
        <span className="player-hero-tile__caption">
          {row.shards_required > 0
            ? `🔒 ${row.shards_current}/${row.shards_required}`
            : "🔒 locked"}
        </span>
      ) : (
        <span className="player-hero-tile__caption">
          Lv {row.level}
          {row.seen !== "—" ? ` · ${row.seen}` : ""}
        </span>
      )}
    </Link>
  );
}

type Props = {
  rows: HeroStateRow[];
  locked: boolean;
  colsPerRow?: number;
};

export function HeroTileGrid({ rows, locked, colsPerRow = 4 }: Props) {
  if (!rows.length) return null;

  return (
    <div
      className="wiki-tiles player-hero-grid"
      style={{
        gridTemplateColumns: `repeat(${colsPerRow}, minmax(0, 1fr))`,
      }}
    >
      {rows.map((row) => (
        <HeroTile key={row.id} row={row} locked={locked} />
      ))}
    </div>
  );
}
