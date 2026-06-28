"use client";

import {
  closestCenter,
  DndContext,
  type DragEndEvent,
  PointerSensor,
  useSensor,
  useSensors,
} from "@dnd-kit/core";
import { useEffect, useMemo, useState } from "react";
import { useFleet } from "@/components/FleetContextProvider";
import { Button, Card, Spinner, Toggle } from "@/components/ui";
import {
  type ArenaHeroesView,
  type ArenaHeroInfo,
  type ArenaLineupResult,
  type ArenaSlotAssignment,
  fetchArenaHeroes,
  fetchArenaRoster,
  optimizeArenaLineup,
} from "@/lib/api";
import { ArenaBoard, BenchDrop, DraggableHeroChip } from "./ArenaBoard";
import { type EnemySlotState, EnemyLineup } from "./EnemyLineup";
import { HeroSearch } from "./HeroSearch";
import { PlacementResult } from "./PlacementResult";

type RosterEntry = {
  id: string;
  power: number | null;
  star: number;
  level: number;
  skill: number;
  gear: number[] | null;
};

export function ArenaOptimizer() {
  const [view, setView] = useState<ArenaHeroesView | null>(null);
  const [loadErr, setLoadErr] = useState<string | null>(null);

  const [roster, setRoster] = useState<RosterEntry[]>([]);
  const [placement, setPlacement] = useState<Record<number, string | null>>({});
  const [locked, setLocked] = useState<Set<number>>(new Set());
  const [enemy, setEnemy] = useState<Record<number, EnemySlotState>>({});
  const [counterEnabled, setCounterEnabled] = useState(true);

  const [result, setResult] = useState<ArenaLineupResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [loadingRoster, setLoadingRoster] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const { playerId } = useFleet();

  useEffect(() => {
    fetchArenaHeroes()
      .then(setView)
      .catch((e) => setLoadErr(e instanceof Error ? e.message : String(e)));
  }, []);

  const sensors = useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 6 } }));

  const heroById = useMemo(() => {
    const m = new Map<string, ArenaHeroInfo>();
    view?.heroes.forEach((h) => m.set(h.id, h));
    return m;
  }, [view]);
  const powerById = useMemo(() => {
    const m = new Map<string, number | null>();
    roster.forEach((r) => m.set(r.id, r.power));
    return m;
  }, [roster]);
  const assignments = useMemo(() => {
    const m = new Map<number, ArenaSlotAssignment>();
    result?.best?.slots.forEach((s) => m.set(s.slot, s));
    return m;
  }, [result]);

  const placedIds = useMemo(
    () => new Set(Object.values(placement).filter(Boolean) as string[]),
    [placement],
  );
  const rosterIds = useMemo(() => new Set(roster.map((r) => r.id)), [roster]);

  if (loadErr) return <div className="error-banner">Failed to load hero catalog: {loadErr}</div>;
  if (!view) {
    return (
      <div className="flex items-center gap-2 text-sm text-wos-text-muted">
        <Spinner /> Loading hero catalog…
      </div>
    );
  }
  const layout = view.layout;

  // --- roster mutations ----------------------------------------------------
  const addHero = (h: ArenaHeroInfo) => {
    if (rosterIds.has(h.id)) return;
    setRoster((r) => [...r, { id: h.id, power: null, star: 1, level: 1, skill: 1, gear: null }]);
  };
  const loadFromAccount = async () => {
    if (!playerId) return;
    setErr(null);
    setLoadingRoster(true);
    try {
      const view2 = await fetchArenaRoster(playerId);
      setRoster(
        view2.heroes.map((h) => ({
          id: h.id,
          power: null,
          star: h.star ?? 1,
          level: h.level ?? 1,
          skill: h.skill ?? 1,
          gear: h.gear ?? null,
        })),
      );
      setPlacement({});
      setLocked(new Set());
      setResult(null);
      if (view2.heroes.length === 0) {
        setErr(`No heroes have been read for ${playerId} yet — add them manually below.`);
      }
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setLoadingRoster(false);
    }
  };
  const removeHero = (id: string) => {
    setRoster((r) => r.filter((x) => x.id !== id));
    setPlacement((p) => {
      const next = { ...p };
      for (const k of Object.keys(next)) if (next[Number(k)] === id) next[Number(k)] = null;
      return next;
    });
    setLocked((l) => {
      const next = new Set(l);
      for (const [slot, hid] of Object.entries(placement)) if (hid === id) next.delete(Number(slot));
      return next;
    });
  };
  const patchHero = (id: string, patch: Partial<RosterEntry>) =>
    setRoster((r) => r.map((x) => (x.id === id ? { ...x, ...patch } : x)));

  // --- board mutations -----------------------------------------------------
  const clearSlot = (slot: number) => {
    setPlacement((p) => ({ ...p, [slot]: null }));
    setLocked((l) => {
      const n = new Set(l);
      n.delete(slot);
      return n;
    });
  };
  const toggleLock = (slot: number) =>
    setLocked((l) => {
      const n = new Set(l);
      if (n.has(slot)) n.delete(slot);
      else n.add(slot);
      return n;
    });

  const onDragEnd = (e: DragEndEvent) => {
    const a = e.active.data.current as { heroId: string; from: "pool" | "board"; slot?: number } | undefined;
    const o = e.over?.data.current as { slot?: number; bench?: boolean } | undefined;
    if (!a || !o) return;

    // Drag out of the lineup → onto the bench.
    if (o.bench) {
      if (a.from === "board" && a.slot != null) clearSlot(a.slot);
      return;
    }
    if (o.slot == null) return;
    const target = o.slot;
    const occupant = placement[target] ?? null;

    setPlacement((p) => {
      const next = { ...p };
      if (a.from === "board" && a.slot != null) next[a.slot] = occupant; // swap occupant back
      next[target] = a.heroId;
      return next;
    });
    setLocked((l) => {
      const n = new Set(l);
      n.add(target); // a hand-placed seat is pinned
      if (a.from === "board" && a.slot != null) {
        if (occupant) n.add(a.slot);
        else n.delete(a.slot);
      }
      return n;
    });
  };

  // --- optimize ------------------------------------------------------------
  const optimize = async () => {
    setErr(null);
    setBusy(true);
    try {
      const my_heroes = roster.map((r) => ({
        id: r.id,
        star: r.star,
        level: r.level,
        skill: r.skill,
        ...(r.power ? { power: r.power } : {}),
        ...(r.gear && r.gear.length ? { gear: r.gear } : {}),
      }));
      const enemyList = Object.entries(enemy)
        .filter(([, e]) => e.hero_class)
        .map(([slot, e]) => ({
          slot: Number(slot),
          hero_class: e.hero_class,
          ...(e.id ? { id: e.id } : {}),
          ...(e.power ? { power: e.power } : {}),
        }));
      const lockedReq: Record<string, string> = {};
      for (const slot of locked) {
        const hid = placement[slot];
        if (hid) lockedReq[String(slot)] = hid;
      }
      const res = await optimizeArenaLineup({
        my_heroes,
        enemy: enemyList,
        locked: lockedReq,
        counter_enabled: counterEnabled,
        top_k: 3,
      });
      setResult(res);
      if (res.best) {
        const next: Record<number, string | null> = {};
        for (let s = 1; s <= layout.count; s++) next[s] = null;
        res.best.slots.forEach((s) => {
          next[s.slot] = s.hero_id;
        });
        setPlacement(next);
      }
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const resetBoard = () => {
    setPlacement({});
    setLocked(new Set());
    setResult(null);
  };

  const poolHeroes = roster.filter((r) => !placedIds.has(r.id));

  return (
    <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={onDragEnd}>
      <div className="flex flex-col gap-4 lg:grid lg:grid-cols-[minmax(0,380px)_minmax(0,1fr)]">
        {/* LEFT — inputs */}
        <div className="flex flex-col gap-4">
          <Card title="My heroes">
            <div className="flex flex-col gap-2">
              <div className="flex items-center gap-2">
                <div className="flex-1">
                  <HeroSearch heroes={view.heroes} exclude={rosterIds} onPick={addHero} />
                </div>
                <Button
                  variant="secondary"
                  pending={loadingRoster}
                  disabled={!playerId}
                  onClick={loadFromAccount}
                  title={playerId ? `Load ${playerId}'s read roster` : "Pick a player in the header"}
                >
                  Load account
                </Button>
              </div>
              {roster.length === 0 ? (
                <p className="text-xs text-wos-text-muted">
                  Add the heroes you can field, or <strong>Load account</strong> to pull the selected
                  player&apos;s read roster. Enter each hero&apos;s in-game Power for the most accurate
                  win prediction — the account stores level + stars, not Power.
                </p>
              ) : null}
              {roster.map((r) => {
                const h = heroById.get(r.id);
                const onBoard = placedIds.has(r.id);
                return (
                  <div key={r.id} className="flex items-center gap-2">
                    <div className="flex-1">
                      {onBoard ? (
                        <div className="rounded-lg border border-wos-border-subtle bg-wos-panel/40 px-2.5 py-2 text-sm text-wos-text-muted">
                          {h?.name ?? r.id}{" "}
                          <span className="text-[11px]">· on seat</span>
                        </div>
                      ) : (
                        <DraggableHeroChip
                          dragId={`pool:${r.id}`}
                          heroId={r.id}
                          hero={h}
                          power={r.power}
                          from="pool"
                        />
                      )}
                    </div>
                    <input
                      type="number"
                      className="field w-24"
                      placeholder="power"
                      value={r.power ?? ""}
                      onChange={(e) =>
                        patchHero(r.id, { power: e.target.value ? Number(e.target.value) : null })
                      }
                    />
                    <input
                      type="number"
                      min={1}
                      max={6}
                      className="field w-14"
                      title="stars"
                      value={r.star}
                      onChange={(e) =>
                        patchHero(r.id, { star: Math.max(1, Math.min(6, Number(e.target.value) || 1)) })
                      }
                    />
                    <button
                      type="button"
                      className="text-wos-text-muted hover:text-red-400"
                      title="remove"
                      onClick={() => removeHero(r.id)}
                    >
                      ✕
                    </button>
                  </div>
                );
              })}
              {poolHeroes.length > 0 ? (
                <BenchDrop>
                  <p className="text-center text-[11px] text-wos-text-muted">
                    drag a seated hero here to bench them
                  </p>
                </BenchDrop>
              ) : null}
            </div>
          </Card>

          <Card title="Enemy lineup">
            <EnemyLineup
              layout={layout}
              classes={view.classes}
              heroes={view.heroes}
              enemy={enemy}
              onChange={(slot, next) =>
                setEnemy((cur) => {
                  const copy = { ...cur };
                  if (next === null) delete copy[slot];
                  else copy[slot] = next;
                  return copy;
                })
              }
            />
          </Card>
        </div>

        {/* RIGHT — board + result */}
        <div className="flex flex-col gap-4">
          <Card title="Lineup board">
            <div className="mb-3 flex flex-wrap items-center justify-end gap-3">
              <label className="flex items-center gap-1.5 text-xs text-wos-text-muted">
                <Toggle checked={counterEnabled} onChange={setCounterEnabled} aria-label="counters" />
                class counters
              </label>
              <Button variant="secondary" onClick={resetBoard}>
                Reset
              </Button>
              <Button
                variant="primary"
                pending={busy}
                disabled={roster.length === 0}
                onClick={optimize}
              >
                Optimize
              </Button>
            </div>
            {err ? <div className="error-banner mb-3">{err}</div> : null}
            <ArenaBoard
              layout={layout}
              placement={placement}
              heroById={heroById}
              powerById={powerById}
              assignments={result ? assignments : undefined}
              locked={locked}
              onClearSlot={clearSlot}
              onToggleLock={toggleLock}
            />
          </Card>

          <Card title="Recommendation">
            {result ? (
              <PlacementResult result={result} heroById={heroById} />
            ) : (
              <p className="text-sm text-wos-text-muted">
                Set up your roster and the enemy, then press{" "}
                <span className="font-medium text-wos-text">Optimize</span>. Pin a seat with 📍 to
                force a hero there and optimize the rest around it.
              </p>
            )}
          </Card>
        </div>
      </div>
    </DndContext>
  );
}
