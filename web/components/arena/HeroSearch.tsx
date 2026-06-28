"use client";

import { useMemo, useState } from "react";
import type { ArenaHeroInfo } from "@/lib/api";
import { classEmoji, ROLE_LABEL } from "./ArenaBoard";

/** Compact type-to-search hero combobox. Picks one hero from the catalog. */
export function HeroSearch({
  heroes,
  onPick,
  exclude,
  placeholder = "Add hero…",
}: {
  heroes: ArenaHeroInfo[];
  onPick: (hero: ArenaHeroInfo) => void;
  exclude?: Set<string>;
  placeholder?: string;
}) {
  const [q, setQ] = useState("");
  const [open, setOpen] = useState(false);

  const matches = useMemo(() => {
    const needle = q.trim().toLowerCase();
    return heroes
      .filter((h) => !exclude?.has(h.id))
      .filter(
        (h) =>
          !needle ||
          h.name.toLowerCase().includes(needle) ||
          h.id.includes(needle) ||
          h.hero_class.includes(needle),
      )
      .slice(0, 8);
  }, [heroes, q, exclude]);

  return (
    <div className="relative">
      <input
        className="field w-full"
        value={q}
        placeholder={placeholder}
        onChange={(e) => {
          setQ(e.target.value);
          setOpen(true);
        }}
        onFocus={() => setOpen(true)}
        onBlur={() => setTimeout(() => setOpen(false), 120)}
      />
      {open && matches.length > 0 ? (
        <ul className="absolute z-20 mt-1 max-h-64 w-full overflow-auto rounded-lg border border-wos-border-subtle bg-wos-panel shadow-lg">
          {matches.map((h) => (
            <li key={h.id}>
              <button
                type="button"
                className="flex w-full items-center gap-2 px-3 py-1.5 text-left text-sm hover:bg-wos-panel-raised"
                onMouseDown={(e) => {
                  e.preventDefault();
                  onPick(h);
                  setQ("");
                  setOpen(false);
                }}
              >
                <span className="text-base leading-none">{classEmoji(h.hero_class)}</span>
                <span className="flex-1 truncate text-wos-text">{h.name}</span>
                <span className="text-[11px] text-wos-text-muted">
                  {ROLE_LABEL[h.role] ?? h.role}
                </span>
              </button>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
