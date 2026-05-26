"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { BuildingLevelsTable } from "@/components/player-state/BuildingLevelsTable";
import { CollapsiblePanel } from "@/components/player-state/CollapsiblePanel";
import { HeroTileGrid } from "@/components/player-state/HeroTileGrid";
import { SearchField } from "@/components/player-state/SearchField";
import { AppListbox, AppTabs } from "@/components/headless";
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

type TabKey = "redis" | "persisted" | "heroes";

const TABS: { key: TabKey; label: string }[] = [
  { key: "redis", label: "Redis (live)" },
  { key: "persisted", label: "Persisted (SQLite)" },
  { key: "heroes", label: "Heroes" },
];

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

function MetricsRow({ items }: { items: { label: string; value: string | number }[] }) {
  return (
    <div
      className="toolbar"
      style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(8rem, 1fr))" }}
    >
      {items.map((m) => (
        <div key={m.label} className="panel" style={{ padding: "0.75rem 1rem" }}>
          <div className="meta">{m.label}</div>
          <div style={{ fontSize: "1.25rem", fontWeight: 600 }}>{m.value}</div>
        </div>
      ))}
    </div>
  );
}

function PersistedPanel({
  persisted,
  syncing,
  onSync,
  bldgFilter,
}: {
  persisted: PlayerPersistedView;
  syncing: boolean;
  onSync: () => void;
  bldgFilter: string;
}) {
  const p = persisted.player;
  if (persisted.parse_error) {
    return (
      <section className="panel">
        <div className="error-banner">Cannot parse state DB: {persisted.parse_error}</div>
        {persisted.raw_yaml ? (
          <pre style={{ overflow: "auto", fontSize: "0.8rem" }}>{persisted.raw_yaml}</pre>
        ) : null}
      </section>
    );
  }
  if (!p) {
    return (
      <section className="panel">
        <p className="meta">Player not found in `{persisted.state_path}`.</p>
      </section>
    );
  }

  const s = p.summary;
  const bldgTotal = p.building_levels.length;
  const bldgNeedle = bldgFilter.trim().toLowerCase();
  const bldgShown = bldgNeedle
    ? p.building_levels.filter((r) =>
        [r.id, r.building, r.category, String(r.level)]
          .join(" ")
          .toLowerCase()
          .includes(bldgNeedle),
      ).length
    : bldgTotal;

  return (
    <>
      <div className="toolbar">
        <button
          type="button"
          className="btn-primary"
          disabled={syncing}
          onClick={onSync}
        >
          {syncing ? "Syncing…" : "Sync from Century API"}
        </button>
        <span className="meta">
          File: <code>{persisted.state_path}</code>
        </span>
      </div>
      <MetricsRow
        items={[
          { label: "Power", value: String(s.power ?? "—") },
          { label: "Gems", value: String(s.gems ?? "—") },
          { label: "Furnace Lv", value: String(s.furnace_level ?? "—") },
          { label: "Furnace pwr", value: String(s.furnace_power ?? "—") },
        ]}
      />
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
      <CollapsiblePanel
        title="Building levels"
        meta={countLabel(bldgShown, bldgTotal)}
        defaultOpen={bldgTotal <= COLLAPSE_BUILDINGS_ABOVE}
      >
        <BuildingLevelsTable rows={p.building_levels} filter={bldgFilter} />
      </CollapsiblePanel>
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
      <CollapsiblePanel title="Full JSON record" defaultOpen={false}>
        <pre style={{ overflow: "auto", fontSize: "0.75rem" }}>
          {JSON.stringify(p.gamer, null, 2)}
        </pre>
      </CollapsiblePanel>
    </>
  );
}

function PlayerStatePageInner() {
  const params = useSearchParams();
  const router = useRouter();
  const tabParam = (params.get("tab") as TabKey) || "redis";
  const tab: TabKey = TABS.some((t) => t.key === tabParam) ? tabParam : "redis";
  const urlPlayerId = params.get("player_id");
  const playerTouchedRef = useRef(Boolean(urlPlayerId));
  const [playerTouched, setPlayerTouched] = useState(Boolean(urlPlayerId));

  const {
    instanceId,
    playerId,
    setPlayerId,
    players,
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
  const [bldgFilter, setBldgFilter] = useState("");
  const [heroFilter, setHeroFilter] = useState("");
  const [fieldFilter, setFieldFilter] = useState("");

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

  const setTab = (key: TabKey) => {
    const url = new URL(window.location.href);
    url.searchParams.set("tab", key);
    router.replace(url.pathname + url.search);
  };

  const refreshLive = useCallback(async () => {
    if (!playerId || tab !== "redis") return;
    try {
      setLive(await fetchPlayerState(playerId));
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [playerId, tab]);

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
    enabled: tab === "redis" && Boolean(playerId),
    onEvent: () => {
      void refreshLive();
    },
    onFallbackPoll: refreshLive,
  });

  useEffect(() => {
    if (playerId && (tab === "persisted" || tab === "heroes")) {
      void refreshPersisted();
    }
  }, [playerId, tab, refreshPersisted]);

  const onSync = async () => {
    if (!playerId || syncing) return;
    setSyncing(true);
    setError(null);
    try {
      const result = await syncPlayerFromCentury(playerId);
      showSuccess(
        `Synced ${result.nickname} · stove ${result.stove_level} · KID ${result.kid}`,
      );
      await refreshPersisted();
      if (tab === "redis") await refreshLive();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSyncing(false);
    }
  };

  const onDelete = async () => {
    if (!playerId || deleting) return;
    const confirmed = window.confirm(
      `Удалить все данные игрока ${playerId}?\n\n` +
        "Будут стёрты:\n" +
        "  • Redis: wos:player:<id>:* (state, scenario, TTL)\n" +
        "  • SQLite: gamers, player_power_daily, player_level_events\n\n" +
        "Это необратимо.",
    );
    if (!confirmed) return;
    setDeleting(true);
    setError(null);
    try {
      const result = await deletePlayer(playerId);
      const sqliteSum = Object.values(result.sqlite || {}).reduce(
        (a, b) => a + b,
        0,
      );
      showSuccess(
        `Удалён ${result.player_id} · Redis: ${result.redis_keys_deleted} ключ(ей) · SQLite: ${sqliteSum} строк`,
      );
      setLive(null);
      setPersisted(null);
      setPlayerId("");
      playerTouchedRef.current = false;
      setPlayerTouched(false);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setDeleting(false);
    }
  };

  const heroView = persisted?.player?.heroes;

  const filteredHashFields = useMemo(() => {
    if (!live?.fields) return [];
    const q = fieldFilter.trim().toLowerCase();
    const entries = Object.entries(live.fields);
    if (!q) return entries;
    return entries.filter(
      ([k, v]) => k.toLowerCase().includes(q) || v.toLowerCase().includes(q),
    );
  }, [live?.fields, fieldFilter]);

  const bannerError = error ?? playersError ?? instancesError;

  return (
    <>
      <FleetPageHeader title="Player state" showPlayer />
      <p className="meta">
        <strong>Redis</strong> — live <code>wos:player:&lt;id&gt;:state</code> from
        the worker. <strong>Persisted</strong> — <code>db/state/wos.db</code>. Pick an
        instance to pre-select the active player from Redis.
      </p>
      <ErrorBanner message={bannerError} />

      <div className="toolbar" style={{ flexWrap: "wrap" }}>
        {suggestedPlayer ? (
          <span className="meta">
            Active player on instance:{" "}
            <code>{suggestedPlayer || "—"}</code>
            {!playerTouched && playerId === suggestedPlayer ? (
              <span> (auto-selected)</span>
            ) : null}
          </span>
        ) : instanceId ? (
          <span className="meta">No active player on this instance yet.</span>
        ) : null}
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
          <Link href={playerStatsHref(playerId, { instanceId })} className="btn btn--ghost">
            Statistics
          </Link>
        ) : null}
        {playerId ? (
          <button
            type="button"
            className="btn-danger"
            disabled={deleting}
            onClick={onDelete}
            title="Стереть Redis-state и SQLite-записи этого игрока"
          >
            {deleting ? "Удаление…" : "Удалить игрока"}
          </button>
        ) : null}
      </div>

      <AppTabs
        tabs={TABS}
        selectedKey={tab}
        onChange={(key) => setTab(key as TabKey)}
        renderPanels={false}
      />

      {tab === "redis" ? (
        <div className="player-state-filters">
          <SearchField
            label="Buildings"
            value={bldgFilter}
            onChange={setBldgFilter}
            placeholder="id, name, category, level…"
          />
          <SearchField
            label="Redis fields"
            value={fieldFilter}
            onChange={setFieldFilter}
            placeholder="hash key or value…"
          />
        </div>
      ) : null}
      {tab === "persisted" ? (
        <div className="player-state-filters">
          <SearchField
            label="Buildings"
            value={bldgFilter}
            onChange={setBldgFilter}
            placeholder="id, name, category, level…"
          />
        </div>
      ) : null}
      {tab === "heroes" ? (
        <div className="player-state-filters">
          <SearchField
            label="Heroes"
            value={heroFilter}
            onChange={setHeroFilter}
            placeholder="id, name, rarity, class…"
          />
        </div>
      ) : null}

      {tab === "redis" ? (
        live && live.field_count > 0 ? (
          <>
            <MetricsRow
              items={[
                { label: "Nickname", value: live.nickname || "—" },
                { label: "Stove (Century)", value: live.stove_level || "—" },
                { label: "KID", value: live.kid || "—" },
                { label: "Hash fields", value: live.field_count },
              ]}
            />
            {live.avatar_image ? (
              // eslint-disable-next-line @next/next/no-img-element
              <img
                src={live.avatar_image}
                alt=""
                width={80}
                height={80}
                style={{ borderRadius: 8, marginBottom: "0.75rem" }}
              />
            ) : null}
            <CollapsiblePanel
              title="Building levels"
              meta={countLabel(
                live.building_levels.filter((r) => {
                  const q = bldgFilter.trim().toLowerCase();
                  if (!q) return true;
                  return [r.id, r.building, r.category, String(r.level)]
                    .join(" ")
                    .toLowerCase()
                    .includes(q);
                }).length,
                live.building_levels.length,
              )}
              defaultOpen={
                live.building_levels.length <= COLLAPSE_BUILDINGS_ABOVE
              }
            >
              <BuildingLevelsTable
                rows={live.building_levels}
                filter={bldgFilter}
              />
            </CollapsiblePanel>
            <CollapsiblePanel
              title="All hash fields"
              meta={countLabel(
                filteredHashFields.length,
                Object.keys(live.fields).length,
              )}
              defaultOpen={false}
            >
              {filteredHashFields.length ? (
                <div className="data-table-wrap">
                  <table className="data-table">
                    <thead>
                      <tr>
                        <th>Key</th>
                        <th>Value</th>
                      </tr>
                    </thead>
                    <tbody>
                      {filteredHashFields.map(([k, v]) => (
                        <tr key={k}>
                          <td>
                            <code>{k}</code>
                          </td>
                          <td>
                            <code>{v || "—"}</code>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : (
                <p className="meta">No hash fields matched the filter.</p>
              )}
            </CollapsiblePanel>
          </>
        ) : (
          <section className="panel">
            <p className="meta">
              No Redis hash yet for this player — start the worker or pick another
              id.
            </p>
          </section>
        )
      ) : null}

      {tab === "persisted" && persisted ? (
        <PersistedPanel
          persisted={persisted}
          syncing={syncing}
          onSync={onSync}
          bldgFilter={bldgFilter}
        />
      ) : null}

      {tab === "heroes" && persisted?.player ? (
        <>
          <div className="toolbar">
            <button
              type="button"
              className="btn-primary"
              disabled={syncing}
              onClick={onSync}
            >
              Sync from Century API
            </button>
          </div>
          {heroView ? (
            <>
              <MetricsRow
                items={[
                  { label: "Owned", value: String(heroView.metrics.owned) },
                  { label: "Locked", value: String(heroView.metrics.locked) },
                  {
                    label: "In registry",
                    value: String(heroView.metrics.registry_total),
                  },
                  {
                    label: "Notify",
                    value: heroView.metrics.notify ? "yes" : "no",
                  },
                ]}
              />
              {(() => {
                const owned = filterHeroRows(heroView.owned, heroFilter);
                const locked = filterHeroRows(heroView.locked, heroFilter);
                const missing = filterHeroRows(heroView.missing, heroFilter);
                return (
                  <>
                    <CollapsiblePanel
                      title="Owned"
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
                      title="Locked · collecting shards"
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
                      title="Not yet seen"
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
              })()}
            </>
          ) : (
            <p className="meta">No hero data for this player.</p>
          )}
        </>
      ) : null}

      {tab === "heroes" && persisted && !persisted.player ? (
        <section className="panel">
          <p className="meta">
            No persisted record — open the{" "}
            <button
              type="button"
              className="btn-secondary"
              style={{ display: "inline", padding: "0 0.35rem" }}
              onClick={() => setTab("persisted")}
            >
              Persisted
            </button>{" "}
            tab or run scenarios that persist state.
          </p>
        </section>
      ) : null}
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
