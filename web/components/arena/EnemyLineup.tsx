"use client";

import { Chip } from "@/components/ui";
import type { ArenaHeroInfo, ArenaSlotLayout } from "@/lib/api";
import { classEmoji, seatKind } from "./ArenaBoard";
import { HeroSearch } from "./HeroSearch";

export type EnemySlotState = {
  hero_class: string;
  id?: string;
  name?: string;
  power?: number | null;
};

const SEAT_HINT: Record<string, string> = {
  front: "front (tank/CC)",
  slot4: "slot 4 (carry)",
  back: "back (DPS)",
};

export function EnemyLineup({
  layout,
  classes,
  heroes,
  enemy,
  onChange,
}: {
  layout: ArenaSlotLayout;
  classes: string[];
  heroes: ArenaHeroInfo[];
  enemy: Record<number, EnemySlotState>;
  onChange: (slot: number, next: EnemySlotState | null) => void;
}) {
  const slots = Array.from({ length: layout.count }, (_, i) => i + 1);
  return (
    <div className="flex flex-col gap-2">
      {slots.map((slot) => {
        const e = enemy[slot];
        const kind = seatKind(slot, layout);
        return (
          <div
            key={slot}
            className="rounded-lg border border-wos-border-subtle bg-wos-panel/40 p-2"
          >
            <div className="mb-1.5 flex items-center justify-between text-[11px] uppercase tracking-wide text-wos-text-muted">
              <span>
                #{slot} · {SEAT_HINT[kind]}
              </span>
              {e ? (
                <button
                  type="button"
                  className="hover:text-red-400"
                  onClick={() => onChange(slot, null)}
                >
                  clear
                </button>
              ) : null}
            </div>
            <div className="flex flex-wrap items-center gap-1.5">
              {classes.map((c) => (
                <Chip
                  key={c}
                  active={e?.hero_class === c}
                  onClick={() =>
                    onChange(slot, {
                      ...(e ?? {}),
                      hero_class: c,
                      // a manual class pick clears any specific-hero label
                      id: e?.id && heroes.find((h) => h.id === e.id)?.hero_class === c ? e.id : undefined,
                      name: undefined,
                    })
                  }
                >
                  {classEmoji(c)} {c}
                </Chip>
              ))}
              <input
                type="number"
                className="field w-24"
                placeholder="power"
                value={e?.power ?? ""}
                onChange={(ev) =>
                  onChange(slot, {
                    ...(e ?? {}),
                    hero_class: e?.hero_class ?? "",
                    power: ev.target.value ? Number(ev.target.value) : null,
                  })
                }
              />
              <div className="min-w-[140px] flex-1">
                <HeroSearch
                  heroes={heroes}
                  placeholder={e?.name ?? "known hero…"}
                  onPick={(h) =>
                    onChange(slot, {
                      ...(e ?? {}),
                      id: h.id,
                      name: h.name,
                      hero_class: h.hero_class,
                    })
                  }
                />
              </div>
            </div>
          </div>
        );
      })}
    </div>
  );
}
