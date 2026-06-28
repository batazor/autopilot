"use client";

import { useDraggable, useDroppable } from "@dnd-kit/core";
import { CSS } from "@dnd-kit/utilities";
import type { ArenaHeroInfo, ArenaSlotAssignment, ArenaSlotLayout } from "@/lib/api";

export const CLASS_EMOJI: Record<string, string> = {
  infantry: "🛡️",
  lancer: "🐎",
  marksman: "🏹",
};

export const ROLE_LABEL: Record<string, string> = {
  tank: "Tank",
  cc: "Control",
  marksman: "Carry",
  dps: "DPS",
  healer: "Healer",
  support: "Support",
};

export function seatKind(slot: number, layout: ArenaSlotLayout): "front" | "slot4" | "back" {
  if (slot === layout.all_target) return "slot4";
  if (layout.front.includes(slot)) return "front";
  return "back";
}

const SEAT_LABEL: Record<string, string> = {
  front: "Front",
  slot4: "Slot 4 · hits all 5",
  back: "Back",
};

export function classEmoji(cls: string): string {
  return CLASS_EMOJI[cls] ?? "❔";
}

/** Presentational hero chip (no DnD). */
export function HeroChip({
  hero,
  power,
  compact = false,
}: {
  hero: ArenaHeroInfo | undefined;
  power?: number | null;
  compact?: boolean;
}) {
  const name = hero?.name ?? "—";
  const cls = hero?.hero_class ?? "";
  const role = hero?.role ?? "";
  return (
    <div className="flex w-full flex-col gap-0.5">
      <div className="flex items-center gap-1.5">
        <span className="text-base leading-none">{classEmoji(cls)}</span>
        <span className="truncate text-sm font-semibold text-wos-text">{name}</span>
      </div>
      {!compact ? (
        <div className="flex flex-wrap items-center gap-1 text-[11px] text-wos-text-muted">
          {role ? <span className="capitalize">{ROLE_LABEL[role] ?? role}</span> : null}
          {power ? <span>· {Intl.NumberFormat().format(power)}</span> : null}
        </div>
      ) : null}
    </div>
  );
}

/** A hero chip you can drag. `dragId` namespaces pool vs board occupants. */
export function DraggableHeroChip({
  dragId,
  heroId,
  hero,
  power,
  from,
  slot,
}: {
  dragId: string;
  heroId: string;
  hero: ArenaHeroInfo | undefined;
  power?: number | null;
  from: "pool" | "board";
  slot?: number;
}) {
  const { attributes, listeners, setNodeRef, transform, isDragging } = useDraggable({
    id: dragId,
    data: { heroId, from, slot },
  });
  return (
    <div
      ref={setNodeRef}
      style={{ transform: CSS.Translate.toString(transform) }}
      className={`cursor-grab touch-none rounded-lg border border-wos-border-subtle bg-wos-panel-raised px-2.5 py-2 active:cursor-grabbing ${
        isDragging ? "opacity-40" : ""
      }`}
      {...listeners}
      {...attributes}
    >
      <HeroChip hero={hero} power={power} />
    </div>
  );
}

const SEAT_RING: Record<string, string> = {
  front: "border-amber-500/40",
  slot4: "border-emerald-500/50",
  back: "border-wos-border-subtle",
};

/** A board seat: a drop target that holds (and re-drags) its occupant. */
function DroppableSlot({
  slot,
  layout,
  heroId,
  hero,
  power,
  assignment,
  locked,
  onClear,
  onToggleLock,
}: {
  slot: number;
  layout: ArenaSlotLayout;
  heroId: string | null;
  hero: ArenaHeroInfo | undefined;
  power?: number | null;
  assignment?: ArenaSlotAssignment;
  locked: boolean;
  onClear: (slot: number) => void;
  onToggleLock: (slot: number) => void;
}) {
  const kind = seatKind(slot, layout);
  const { setNodeRef, isOver } = useDroppable({ id: `slot:${slot}`, data: { slot } });
  return (
    <div
      ref={setNodeRef}
      className={`relative flex min-h-[96px] w-[150px] flex-col rounded-xl border-2 border-dashed ${
        SEAT_RING[kind]
      } ${isOver ? "bg-wos-accent/10 border-solid" : "bg-wos-panel/40"} p-2 transition-colors`}
    >
      <div className="mb-1 flex items-center justify-between text-[10px] uppercase tracking-wide text-wos-text-muted">
        <span>
          #{slot} · {SEAT_LABEL[kind]}
        </span>
        {heroId ? (
          <span className="flex items-center gap-1">
            <button
              type="button"
              title={locked ? "Unpin (let the optimizer move it)" : "Pin to this seat"}
              onClick={() => onToggleLock(slot)}
              className={locked ? "text-amber-400" : "text-wos-text-muted hover:text-wos-text"}
            >
              {locked ? "📌" : "📍"}
            </button>
            <button
              type="button"
              title="Remove from seat"
              onClick={() => onClear(slot)}
              className="text-wos-text-muted hover:text-red-400"
            >
              ✕
            </button>
          </span>
        ) : null}
      </div>

      {heroId ? (
        <DraggableHeroChip
          dragId={`board:${slot}`}
          heroId={heroId}
          hero={hero}
          power={power}
          from="board"
          slot={slot}
        />
      ) : (
        <div className="flex flex-1 items-center justify-center text-xs text-wos-text-muted">
          drop a hero
        </div>
      )}

      {assignment ? (
        <div className="mt-1 border-t border-wos-border-subtle pt-1 text-[10px] text-wos-text-muted">
          {assignment.note}
          {assignment.counter > 0 ? (
            <span className="text-emerald-400"> · +{Math.round(assignment.counter * 100)}% counter</span>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

/** Bench drop zone — drag a board hero here to take it out of the lineup. */
export function BenchDrop({ children }: { children: React.ReactNode }) {
  const { setNodeRef, isOver } = useDroppable({ id: "bench", data: { bench: true } });
  return (
    <div
      ref={setNodeRef}
      className={`rounded-xl border-2 border-dashed p-2 transition-colors ${
        isOver ? "border-solid border-wos-accent bg-wos-accent/10" : "border-wos-border-subtle"
      }`}
    >
      {children}
    </div>
  );
}

export function ArenaBoard({
  layout,
  placement,
  heroById,
  powerById,
  assignments,
  locked,
  onClearSlot,
  onToggleLock,
}: {
  layout: ArenaSlotLayout;
  placement: Record<number, string | null>;
  heroById: Map<string, ArenaHeroInfo>;
  powerById: Map<string, number | null>;
  assignments?: Map<number, ArenaSlotAssignment>;
  locked: Set<number>;
  onClearSlot: (slot: number) => void;
  onToggleLock: (slot: number) => void;
}) {
  const seat = (slot: number) => (
    <DroppableSlot
      key={slot}
      slot={slot}
      layout={layout}
      heroId={placement[slot] ?? null}
      hero={placement[slot] ? heroById.get(placement[slot] as string) : undefined}
      power={placement[slot] ? powerById.get(placement[slot] as string) : undefined}
      assignment={assignments?.get(slot)}
      locked={locked.has(slot)}
      onClear={onClearSlot}
      onToggleLock={onToggleLock}
    />
  );
  const front = layout.front; // [1, 5]
  const back = layout.back; // [2, 3, 4]
  return (
    <div className="flex flex-col items-center gap-3">
      <div className="text-[10px] uppercase tracking-widest text-wos-text-muted">▲ enemy side</div>
      <div className="flex flex-wrap justify-center gap-3">{front.map(seat)}</div>
      <div className="flex flex-wrap justify-center gap-3">{back.map(seat)}</div>
    </div>
  );
}
