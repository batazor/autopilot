"use client";

import { Chip, Pill } from "@/components/ui";
import type { ArenaHeroInfo, ArenaLineupResult, ArenaPlacement } from "@/lib/api";
import { classEmoji, ROLE_LABEL } from "./ArenaBoard";

function winTone(p: number | null): "ok" | "busy" | "danger" | "neutral" {
  if (p === null) return "neutral";
  if (p >= 0.6) return "ok";
  if (p >= 0.45) return "busy";
  return "danger";
}

function confTone(c: string): "ok" | "busy" | "neutral" {
  if (c === "high") return "ok";
  if (c === "medium") return "busy";
  return "neutral";
}

function pct(p: number | null): string {
  return p === null ? "—" : `${Math.round(p * 100)}%`;
}

function AltRow({
  place,
  heroById,
  rank,
}: {
  place: ArenaPlacement;
  heroById: Map<string, ArenaHeroInfo>;
  rank: number;
}) {
  const order = [...place.slots].sort((a, b) => a.slot - b.slot);
  return (
    <div className="flex items-center justify-between gap-2 rounded-lg border border-wos-border-subtle bg-wos-panel/40 px-2.5 py-1.5 text-xs">
      <span className="text-wos-text-muted">#{rank + 2}</span>
      <div className="flex flex-1 flex-wrap gap-1">
        {order.map((s) => {
          const h = heroById.get(s.hero_id);
          return (
            <span key={s.slot} className="text-wos-text">
              {classEmoji(s.hero_class)}
              {h?.name ?? s.hero_name}
              <span className="text-wos-text-muted">·{s.slot}</span>
            </span>
          );
        })}
      </div>
      <span className="tabular-nums text-wos-text-muted">win {pct(place.win_prob)}</span>
    </div>
  );
}

export function PlacementResult({
  result,
  heroById,
}: {
  result: ArenaLineupResult;
  heroById: Map<string, ArenaHeroInfo>;
}) {
  const best = result.best;
  if (!best) {
    return (
      <p className="text-sm text-wos-text-muted">
        No lineup — add heroes to your roster and press Optimize.
      </p>
    );
  }
  const order = [...best.slots].sort((a, b) => a.slot - b.slot);
  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center gap-2">
        <Pill tone={winTone(best.win_prob)} dot>
          win {pct(best.win_prob)}
        </Pill>
        {best.power_ratio != null ? <Chip>power ×{best.power_ratio.toFixed(2)}</Chip> : null}
        <Chip>score {Math.round(best.score)}</Chip>
        {result.enemy_strength > 0 ? (
          <Chip>
            my {Math.round(best.strength_total)} vs enemy {Math.round(result.enemy_strength)}
          </Chip>
        ) : null}
        <Pill tone={result.counter_enabled ? "ok" : "neutral"}>
          counters {result.counter_enabled ? "on" : "off"}
        </Pill>
        <Pill tone={confTone(result.confidence)} title="high = real Power on both sides; medium = stat estimate; low = rarity/mixed">
          {result.confidence} confidence
        </Pill>
      </div>

      {/* Per-seat breakdown */}
      <div className="flex flex-col gap-1.5">
        {order.map((s) => {
          const h = heroById.get(s.hero_id);
          return (
            <div
              key={s.slot}
              className="flex items-center gap-2 rounded-lg border border-wos-border-subtle bg-wos-panel-raised px-2.5 py-1.5"
            >
              <span className="w-12 shrink-0 text-[11px] uppercase tracking-wide text-wos-text-muted">
                #{s.slot} {s.seat === "slot4" ? "★" : ""}
              </span>
              <span className="text-base leading-none">{classEmoji(s.hero_class)}</span>
              <span className="flex-1 truncate text-sm font-medium text-wos-text">
                {h?.name ?? s.hero_name}
                <span className="ml-1 text-[11px] font-normal capitalize text-wos-text-muted">
                  {ROLE_LABEL[s.role] ?? s.role}
                </span>
              </span>
              <span className="hidden text-[11px] text-wos-text-muted sm:inline">{s.note}</span>
              {s.counter > 0 ? (
                <span className="text-[11px] text-emerald-400">+{Math.round(s.counter * 100)}%</span>
              ) : null}
            </div>
          );
        })}
      </div>

      {best.warnings.length > 0 ? (
        <div className="flex flex-col gap-1">
          {best.warnings.map((w) => (
            <div key={w} className="flex items-start gap-1.5 text-xs text-amber-400">
              <span>⚠</span>
              <span>{w}</span>
            </div>
          ))}
        </div>
      ) : null}

      {result.bench.length > 0 ? (
        <div className="flex flex-wrap items-center gap-1.5 text-xs">
          <span className="text-wos-text-muted">Bench:</span>
          {result.bench.map((id) => (
            <Chip key={id}>{heroById.get(id)?.name ?? id}</Chip>
          ))}
        </div>
      ) : null}

      {result.alternatives.length > 0 ? (
        <div className="flex flex-col gap-1.5">
          <h3 className="text-xs font-semibold uppercase tracking-wide text-wos-text-muted">
            Alternatives
          </h3>
          {result.alternatives.map((alt, i) => (
            <AltRow key={i} place={alt} heroById={heroById} rank={i} />
          ))}
        </div>
      ) : null}

      {result.notes.length > 0 ? (
        <div className="flex flex-col gap-1 text-[11px] text-wos-text-muted">
          {result.notes.map((n) => (
            <p key={n}>· {n}</p>
          ))}
        </div>
      ) : null}
    </div>
  );
}
