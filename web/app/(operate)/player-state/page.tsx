"use client";

import { useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useRef, useState } from "react";
import { BuildingLevelsTable } from "@/components/player-state/BuildingLevelsTable";
import { CollapsiblePanel } from "@/components/player-state/CollapsiblePanel";
import { HeroTileGrid } from "@/components/player-state/HeroTileGrid";
import { SearchField } from "@/components/player-state/SearchField";
import { AppConfirmDialog, AppMenu } from "@/components/headless";
import { useFleet } from "@/components/FleetContextProvider";
import { ErrorBanner, useFeedback } from "@/components/feedback";
import { FleetPageHeader } from "@/components/FleetPageHeader";
import {
  deletePlayer,
  fetchPlayerPersisted,
  fetchPlayerState,
  fetchSuggestedPlayer,
  syncPlayerFromCentury,
} from "@/lib/api";
import { playerStatsHref } from "@/lib/fleet-links";
import Link from "next/link";
import { PageLoading } from "@/components/ui/Spinner";
import { useDashboardEventStream } from "@/lib/useDashboardEventStream";
import type {
  HeroMissingRow,
  HeroStateRow,
  PlayerPersistedView,
  PlayerStateView,
} from "@/lib/types";

/** Sections stay expanded when the list is short. */
const COLLAPSE_BUILDINGS_ABOVE = 18;
const COLLAPSE_HEROES_ABOVE = 10;

function filterHeroRows<T extends HeroStateRow | HeroMissingRow>(
  rows: T[],
  query: string,
): T[] {
  const q = query.trim().toLowerCase();
  if (!q) return rows;
  return rows.filter((r) =>
    Object.values(r).join(" ").toLowerCase().includes(q),
  );
}

function countLabel(shown: number, total: number): string {
  return shown === total ? String(total) : `${shown} / ${total}`;
}

function matchesBuilding(r: { id: string; building: string; category: string; level: number | string }, q: string): boolean {
  const needle = q.trim().toLowerCase();
  if (!needle) return true;
  return [r.id, r.building, r.category, String(r.level)]
    .join(" ")
    .toLowerCase()
    .includes(needle);
}

function DataTable({
  columns,
  rows,
}: {
  columns: { key: string; label: string; align?: "left" | "right" }[];
  rows: Record<string, unknown>[];
}) {
  if (!rows.length) return <p className="meta">No rows.</p>;
  return (
    <div className="data-table-wrap">
      <table className="data-table">
        <thead>
          <tr>
            {columns.map((c) => (
              <th key={c.key} style={{ textAlign: c.align ?? "left" }}>
                {c.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr key={String(row.id ?? i)}>
              {columns.map((c) => (
                <td key={c.key} style={{ textAlign: c.align ?? "left" }}>
                  {String(row[c.key] ?? "—")}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function HeroTable({ rows, locked }: { rows: HeroStateRow[]; locked: boolean }) {
  const cols = locked
    ? [
        { key: "id", label: "ID" },
        { key: "hero", label: "Hero" },
        { key: "shards_current", label: "Shards", align: "right" as const },
        { key: "shards_required", label: "Required", align: "right" as const },
        { key: "rarity", label: "Rarity" },
        { key: "class", label: "Class" },
      ]
    : [
        { key: "id", label: "ID" },
        { key: "hero", label: "Hero" },
        { key: "level", label: "Lv", align: "right" as const },
        { key: "rarity", label: "Rarity" },
        { key: "seen", label: "Seen" },
      ];
  return <DataTable columns={cols} rows={rows} />;
}

function MetricsRow({
  items,
}: {
  items: { label: string; value: string | number; title?: string }[];
}) {
  return (
    <div className="mb-4 grid gap-3 [grid-template-columns:repeat(auto-fit,minmax(8rem,1fr))]">
      {items.map((m) => (
        <div key={m.label} className="panel !p-3" title={m.title}>
          <div className="text-xs uppercase tracking-wide text-wos-text-muted">
            {m.label}
          </div>
          <div
            className={`mt-1 text-xl font-semibold ${
              m.title ? "text-wos-text-muted" : "text-wos-text"
            }`}
          >
            {m.value}
          </div>
        </div>
      ))}
    </div>
  );
}

function PlayerHeaderCard({
  avatar,
  nickname,
  kid,
  stoveLevel,
}: {
  avatar: string;
  nickname: string;
  kid: string;
  stoveLevel: string;
}) {
  const displayName = nickname || "—";
  return (
    <section className="panel mb-4">
      <div className="flex items-center gap-4">
        {avatar ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img
            src={avatar}
            alt=""
            width={72}
            height={72}
            className="h-[72px] w-[72px] rounded-xl border border-wos-border-subtle bg-wos-panel-raised object-cover"
          />
        ) : (
          <div
            className="flex h-[72px] w-[72px] items-center justify-center rounded-xl border border-wos-border-subtle bg-wos-panel-raised text-2xl font-semibold text-wos-text-muted"
            aria-hidden
          >
            {displayName.slice(0, 1).toUpperCase()}
          </div>
        )}
        <div className="min-w-0 flex-1">
          <div className="truncate text-lg font-semibold text-wos-text">
            {displayName}
          </div>
          <div className="mt-1 flex flex-wrap items-center gap-1.5 text-xs">
            {kid ? (
              <span className="rounded-full border border-wos-border-subtle bg-wos-panel-raised px-2 py-0.5 font-medium text-wos-text-secondary">
                KID {kid}
              </span>
            ) : null}
            {stoveLevel ? (
              <span className="rounded-full border border-wos-border-subtle bg-wos-panel-raised px-2 py-0.5 font-medium text-wos-text-secondary">
                Stove {stoveLevel}
              </span>
            ) : null}
          </div>
        </div>
      </div>
    </section>
  );
}

function HeroesSection({
  heroView,
  heroFilter,
}: {
  heroView: NonNullable<NonNullable<PlayerPersistedView["player"]>["heroes"]>;
  heroFilter: string;
}) {
  const owned = filterHeroRows(heroView.owned, heroFilter);
  const locked = filterHeroRows(heroView.locked, heroFilter);
  const missing = filterHeroRows(heroView.missing, heroFilter);
  return (
    <>
      <MetricsRow
        items={[
          { label: "Owned", value: String(heroView.metrics.owned) },
          { label: "Locked", value: String(heroView.metrics.locked) },
          { label: "In registry", value: String(heroView.metrics.registry_total) },
          { label: "Notify", value: heroView.metrics.notify ? "yes" : "no" },
        ]}
      />
      <CollapsiblePanel
        title="Heroes · owned"
        meta={countLabel(owned.length, heroView.owned.length)}
        defaultOpen={heroView.owned.length <= COLLAPSE_HEROES_ABOVE}
      >
        <HeroTileGrid rows={owned} locked={false} />
        <details className="player-state-subsection">
          <summary className="meta">Table view</summary>
          <HeroTable rows={owned} locked={false} />
        </details>
      </CollapsiblePanel>
      <CollapsiblePanel
        title="Heroes · collecting shards"
        meta={countLabel(locked.length, heroView.locked.length)}
        defaultOpen={heroView.locked.length <= COLLAPSE_HEROES_ABOVE}
      >
        <HeroTileGrid rows={locked} locked />
        <details className="player-state-subsection">
          <summary className="meta">Table view</summary>
          <HeroTable rows={locked} locked />
        </details>
      </CollapsiblePanel>
      <CollapsiblePanel
        title="Heroes · not yet seen"
        meta={countLabel(missing.length, heroView.missing.length)}
        defaultOpen={heroView.missing.length <= COLLAPSE_HEROES_ABOVE}
      >
        {missing.length ? (
          <DataTable
            columns={[
              { key: "id", label: "ID" },
              { key: "hero", label: "Hero" },
              { key: "rarity", label: "Rarity" },
              { key: "class", label: "Class" },
            ]}
            rows={missing}
          />
        ) : (
          <p className="meta">No heroes matched the filter.</p>
        )}
      </CollapsiblePanel>
    </>
  );
}

function PlayerStatePageInner() {
  const params = useSearchParams();
  const urlPlayerId = params.get("player_id");
  const playerTouchedRef = useRef(Boolean(urlPlayerId));
  const [playerTouched, setPlayerTouched] = useState(Boolean(urlPlayerId));

  const {
    instanceId,
    playerId,
    setPlayerId,
    players,
    refreshPlayers,
    instancesError,
    playersError,
  } = useFleet();
  const { showSuccess } = useFeedback();

  const [suggestedPlayer, setSuggestedPlayer] = useState("");
  const [live, setLive] = useState<PlayerStateView | null>(null);
  const [persisted, setPersisted] = useState<PlayerPersistedView | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [syncing, setSyncing] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [deleteConfirmOpen, setDeleteConfirmOpen] = useState(false);
  const [bldgFilter, setBldgFilter] = useState("");
  const [heroFilter, setHeroFilter] = useState("");

  useEffect(() => {
    if (!instanceId) {
      setSuggestedPlayer("");
      return;
    }
    let cancelled = false;
    fetchSuggestedPlayer(instanceId)
      .then((pid) => {
        if (!cancelled) setSuggestedPlayer(pid);
      })
      .catch(() => {
        if (!cancelled) setSuggestedPlayer("");
      });
    return () => {
      cancelled = true;
    };
  }, [instanceId]);

  useEffect(() => {
    if (urlPlayerId || playerTouchedRef.current) return;
    if (suggestedPlayer && players.includes(suggestedPlayer)) {
      setPlayerId(suggestedPlayer);
    }
  }, [suggestedPlayer, players, urlPlayerId, setPlayerId]);

  const onPlayerChange = (next: string) => {
    playerTouchedRef.current = true;
    setPlayerTouched(true);
    setPlayerId(next);
  };

  const refreshLive = useCallback(async () => {
    if (!playerId) return;
    try {
      setLive(await fetchPlayerState(playerId));
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [playerId]);

  const refreshPersisted = useCallback(async () => {
    if (!playerId) return;
    try {
      setPersisted(await fetchPlayerPersisted(playerId));
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [playerId]);

  useDashboardEventStream({
    topics: ["player"],
    playerId: playerId || undefined,
    enabled: Boolean(playerId),
    onEvent: () => {
      void refreshLive();
    },
    onFallbackPoll: refreshLive,
  });

  useEffect(() => {
    if (!playerId) {
      setLive(null);
      setPersisted(null);
      return;
    }
    void refreshLive();
    void refreshPersisted();
  }, [playerId, refreshLive, refreshPersisted]);

  const onSync = async () => {
    if (!playerId || syncing) return;
    setSyncing(true);
    setError(null);
    try {
      const result = await syncPlayerFromCentury(playerId);
      showSuccess(
        `Synced ${result.nickname} · stove ${result.stove_level} · KID ${result.kid}`,
      );
      await Promise.all([refreshPersisted(), refreshLive()]);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSyncing(false);
    }
  };

  const onDelete = () => {
    if (!playerId || deleting) return;
    setDeleteConfirmOpen(true);
  };

  const runDelete = async () => {
    if (!playerId || deleting) return;
    setDeleting(true);
    setError(null);
    try {
      const result = await deletePlayer(playerId);
      showSuccess(`Deleted player ${result.player_id}`);
      setLive(null);
      setPersisted(null);
      setPlayerId("");
      playerTouchedRef.current = false;
      setPlayerTouched(false);
      setDeleteConfirmOpen(false);
      await refreshPlayers();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setDeleting(false);
    }
  };

  const bannerError = error ?? playersError ?? instancesError;

  const p = persisted?.player ?? null;
  const summary = p?.summary ?? {};
  const liveActive = Boolean(live && live.field_count > 0);

  // power/gems/furnace default to 0 in the state schema, so a zero on a
  // never-synced player is "no data", not a measurement. After a Century
  // sync, zeros are real values and shown as-is.
  const centurySynced = Number(summary.century_player_sync_at ?? 0) > 0;
  const summaryMetric = (v: unknown): { value: string; title?: string } => {
    const noData = v == null || v === "" || Number(v) === 0;
    if (noData && !centurySynced) {
      return { value: "—", title: "Not synced — use “Sync from Century API”" };
    }
    return { value: String(v ?? "—") };
  };

  // Prefer the freshest source for overlapping fields: live (worker) where
  // present, otherwise the last saved snapshot.
  const nickname = live?.nickname || String(summary.nickname ?? "") || "";
  const avatar = live?.avatar_image || "";
  const kid = live?.kid || "";
  const stoveLevel = live?.stove_level || "";
  const buildingRows = liveActive
    ? live!.building_levels
    : p?.building_levels ?? [];
  const buildingShown = buildingRows.filter((r) => matchesBuilding(r, bldgFilter)).length;

  const heroView = p?.heroes;
  const hasAnyData = liveActive || Boolean(p);

  return (
    <>
      <FleetPageHeader title="Player state" showPlayer />
      <p className="muted">
        Current state for the selected player. Pick an instance to pre-select its
        active player.
      </p>
      <ErrorBanner message={bannerError} />

      <div className="mb-3 flex flex-wrap items-center gap-2">
        <div className="min-w-0 flex-1 text-sm text-wos-text-secondary">
          {suggestedPlayer ? (
            <>
              Active player on instance: <code>{suggestedPlayer || "—"}</code>
              {!playerTouched && playerId === suggestedPlayer ? (
                <span className="text-wos-text-muted"> (auto-selected)</span>
              ) : null}
            </>
          ) : instanceId ? (
            <span className="text-wos-text-muted">
              No active player on this instance yet.
            </span>
          ) : null}
        </div>
        {suggestedPlayer &&
        playerId !== suggestedPlayer &&
        players.includes(suggestedPlayer) ? (
          <button
            type="button"
            className="btn-secondary"
            onClick={() => onPlayerChange(suggestedPlayer)}
          >
            Use active ({suggestedPlayer})
          </button>
        ) : null}
        <button
          type="button"
          className="btn-secondary"
          onClick={() => {
            void refreshLive();
            void refreshPersisted();
          }}
        >
          Refresh now
        </button>
        {playerId ? (
          <button
            type="button"
            className="btn-primary"
            disabled={syncing}
            onClick={onSync}
          >
            {syncing ? "Syncing…" : "Sync from Century API"}
          </button>
        ) : null}
        {playerId ? (
          <Link
            href={playerStatsHref(playerId, { instanceId })}
            className="btn-secondary"
          >
            Statistics
          </Link>
        ) : null}
        {playerId ? (
          <AppMenu
            items={[
              {
                label: deleting ? "Deleting…" : "Delete player…",
                onClick: onDelete,
                danger: true,
                disabled: deleting,
                title: "Wipe all data for this player",
              },
            ]}
            anchor="bottom end"
            buttonTitle="More actions"
            ariaLabel="Player actions"
          />
        ) : null}
      </div>

      {playerId ? (
        <div className="player-state-filters">
          <SearchField
            label="Buildings"
            value={bldgFilter}
            onChange={setBldgFilter}
            placeholder="id, name, category, level…"
          />
          <SearchField
            label="Heroes"
            value={heroFilter}
            onChange={setHeroFilter}
            placeholder="id, name, rarity, class…"
          />
        </div>
      ) : null}

      {!playerId ? (
        <section className="panel">
          <p className="meta m-0">Pick a player to see their state.</p>
        </section>
      ) : persisted?.parse_error ? (
        <section className="panel">
          <div className="error-banner">
            Cannot read player data: {persisted.parse_error}
          </div>
        </section>
      ) : !hasAnyData ? (
        <section className="panel">
          <p className="meta m-0">
            No data for this player yet — start the worker, run scenarios, or sync
            from the Century API.
          </p>
        </section>
      ) : (
        <>
          <PlayerHeaderCard
            avatar={avatar}
            nickname={nickname}
            kid={kid}
            stoveLevel={stoveLevel}
          />

          <MetricsRow
            items={[
              { label: "Power", ...summaryMetric(summary.power) },
              { label: "Gems", ...summaryMetric(summary.gems) },
              { label: "Furnace Lv", ...summaryMetric(summary.furnace_level) },
              { label: "Furnace pwr", ...summaryMetric(summary.furnace_power) },
            ]}
          />

          {p?.event_timers?.length ? (
            <CollapsiblePanel
              title="Event timers"
              meta={String(p.event_timers.length)}
              defaultOpen={p.event_timers.length <= 8}
            >
              <DataTable
                columns={[
                  { key: "event", label: "Event" },
                  { key: "status", label: "Status" },
                  { key: "remaining", label: "Remaining", align: "right" },
                  { key: "reset_at", label: "Reset at" },
                  { key: "raw_text", label: "OCR" },
                  { key: "confidence", label: "Conf", align: "right" },
                  { key: "source_region", label: "Source" },
                ]}
                rows={p.event_timers}
              />
            </CollapsiblePanel>
          ) : null}

          <CollapsiblePanel
            title="Building levels"
            meta={countLabel(buildingShown, buildingRows.length)}
            defaultOpen={buildingRows.length <= COLLAPSE_BUILDINGS_ABOVE}
          >
            <BuildingLevelsTable rows={buildingRows} filter={bldgFilter} />
          </CollapsiblePanel>

          {p ? (
            <CollapsiblePanel title="Buildings HUD" defaultOpen>
              <DataTable
                columns={[
                  { key: "queue1", label: "Queue 1" },
                  { key: "queue2", label: "Queue 2" },
                  { key: "hud", label: "HUD" },
                ]}
                rows={[p.buildings_hud]}
              />
            </CollapsiblePanel>
          ) : null}

          {p ? (
            <CollapsiblePanel title="Resources" defaultOpen={false}>
              <DataTable
                columns={[
                  { key: "wood", label: "Wood", align: "right" },
                  { key: "food", label: "Food", align: "right" },
                  { key: "iron", label: "Iron", align: "right" },
                  { key: "meat", label: "Meat", align: "right" },
                  { key: "silver_keys", label: "Silver keys", align: "right" },
                  { key: "gold_keys", label: "Gold keys", align: "right" },
                  { key: "diamond", label: "Diamond", align: "right" },
                ]}
                rows={[p.resources]}
              />
            </CollapsiblePanel>
          ) : null}

          {p ? (
            <CollapsiblePanel
              title="Alliance / Exploration / Arena"
              defaultOpen={false}
            >
              <DataTable
                columns={[
                  { key: "alliance", label: "Alliance" },
                  { key: "alliance_power", label: "Alliance power", align: "right" },
                  { key: "members", label: "Members" },
                  { key: "exploration_level", label: "Expl Lv", align: "right" },
                  { key: "exploration_power", label: "Expl power", align: "right" },
                  { key: "arena_rank", label: "Arena rank", align: "right" },
                  { key: "arena_power", label: "Arena power", align: "right" },
                  { key: "contentment", label: "Contentment" },
                ]}
                rows={[p.alliance_block]}
              />
            </CollapsiblePanel>
          ) : null}

          {heroView ? (
            <HeroesSection heroView={heroView} heroFilter={heroFilter} />
          ) : null}
        </>
      )}

      <AppConfirmDialog
        open={deleteConfirmOpen}
        onClose={() => {
          if (!deleting) setDeleteConfirmOpen(false);
        }}
        onConfirm={runDelete}
        title={`Delete player ${playerId}?`}
        confirmLabel={deleting ? "Deleting…" : "Delete player"}
        variant="danger"
        busy={deleting}
      >
        <p>This will permanently wipe all data for this player. This is irreversible.</p>
      </AppConfirmDialog>
    </>
  );
}

export default function PlayerStatePage() {
  return (
    <Suspense fallback={<PageLoading />}>
      <PlayerStatePageInner />
    </Suspense>
  );
}
