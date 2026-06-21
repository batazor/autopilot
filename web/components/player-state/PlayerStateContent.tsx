"use client";

import { useSearchParams } from "next/navigation";
import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import { AppConfirmDialog, AppMenu } from "@/components/headless";
import { tip } from "@/components/AppTooltip";
import { useFleet } from "@/components/FleetContextProvider";
import { ErrorBanner, useFeedback } from "@/components/feedback";
import { PageHeader } from "@/components/PageHeader";
import { Button } from "@/components/ui";
import {
  deletePlayer,
  fetchPlayerPersisted,
  fetchPlayerStamina,
  fetchPlayerState,
  fetchSuggestedPlayer,
  syncPlayerFromCentury,
  updatePlayerAvatarReference,
} from "@/lib/api";
import { playerStatsHref } from "@/lib/fleet-links";
import { useDashboardEventStream } from "@/lib/useDashboardEventStream";
import type {
  PlayerPersistedView,
  PlayerStaminaView,
  PlayerStateView,
} from "@/lib/types";
import {
  AVATAR_IDENTITY_HELP,
  COLLAPSE_BUILDINGS_ABOVE,
  countLabel,
  matchesBuilding,
} from "@/lib/player-state/helpers";
import { BuildingLevelsTable } from "./BuildingLevelsTable";
import { CollapsiblePanel } from "./CollapsiblePanel";
import { DataTable } from "./DataTable";
import { HeroesSection } from "./HeroesSection";
import { MetricsRow } from "./MetricsRow";
import { PlayerHeaderCard } from "./PlayerHeaderCard";
import { SearchField } from "./SearchField";
import { StaminaPanel } from "./StaminaPanel";

export function PlayerStateContent() {
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
  const [stamina, setStamina] = useState<PlayerStaminaView | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [syncing, setSyncing] = useState(false);
  const [avatarUpdating, setAvatarUpdating] = useState(false);
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

  const refreshStamina = useCallback(async () => {
    if (!playerId) return;
    try {
      setStamina(await fetchPlayerStamina(playerId));
    } catch {
      // Stamina is auxiliary — don't surface its errors on the main banner.
      setStamina(null);
    }
  }, [playerId]);

  useDashboardEventStream({
    topics: ["player"],
    playerId: playerId || undefined,
    enabled: Boolean(playerId),
    onEvent: () => {
      void refreshLive();
      void refreshStamina();
    },
    onFallbackPoll: () => {
      void refreshLive();
      void refreshStamina();
    },
  });

  useEffect(() => {
    if (!playerId) {
      setLive(null);
      setPersisted(null);
      setStamina(null);
      return;
    }
    void refreshLive();
    void refreshPersisted();
    void refreshStamina();
  }, [playerId, refreshLive, refreshPersisted, refreshStamina]);

  const onSync = async () => {
    if (!playerId || syncing) return;
    setSyncing(true);
    setError(null);
    try {
      const result = await syncPlayerFromCentury(playerId);
      showSuccess(`Synced ${result.nickname} · stove ${result.stove_level} · KID ${result.kid}`);
      await Promise.all([refreshPersisted(), refreshLive()]);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSyncing(false);
    }
  };

  const onUpdateAvatarReference = async () => {
    if (!playerId || !instanceId || avatarUpdating) return;
    setAvatarUpdating(true);
    setError(null);
    try {
      const result = await updatePlayerAvatarReference(playerId, instanceId);
      showSuccess(
        `Updated avatar reference ${result.width}×${result.height} for ${result.player_id}`,
      );
      await refreshPersisted();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setAvatarUpdating(false);
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
  const buildingRows = liveActive ? live!.building_levels : p?.building_levels ?? [];
  const buildingShown = buildingRows.filter((r) => matchesBuilding(r, bldgFilter)).length;

  const heroView = p?.heroes;
  const hasAnyData = liveActive || Boolean(p);

  return (
    <>
      <PageHeader title="Player state" fleet={{ showPlayer: true }} />
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
            <span className="text-wos-text-muted">No active player on this instance yet.</span>
          ) : null}
        </div>
        {suggestedPlayer &&
        playerId !== suggestedPlayer &&
        players.includes(suggestedPlayer) ? (
          <Button onClick={() => onPlayerChange(suggestedPlayer)}>
            Use active ({suggestedPlayer})
          </Button>
        ) : null}
        <Button
          onClick={() => {
            void refreshLive();
            void refreshPersisted();
          }}
        >
          Refresh now
        </Button>
        {playerId ? (
          <Button variant="primary" disabled={syncing} onClick={onSync}>
            {syncing ? "Syncing…" : "Sync from Century API"}
          </Button>
        ) : null}
        {playerId ? (
          <span className="inline-flex items-center gap-1">
            <Button
              disabled={!instanceId || avatarUpdating}
              onClick={onUpdateAvatarReference}
              title={
                instanceId
                  ? "Capture the current main-city avatar as this player's identity reference"
                  : "Select an instance first"
              }
            >
              {avatarUpdating ? "Updating avatar…" : "Update avatar reference"}
            </Button>
            <button
              type="button"
              className="flex h-7 w-7 items-center justify-center rounded-full border border-wos-border-subtle text-xs font-semibold text-wos-text-muted hover:border-wos-border hover:text-wos-text"
              aria-label={AVATAR_IDENTITY_HELP}
              {...tip(AVATAR_IDENTITY_HELP, "bottom")}
            >
              ?
            </button>
          </span>
        ) : null}
        {playerId ? (
          <Link href={playerStatsHref(playerId, { instanceId })} className="btn-secondary">
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
          <div className="error-banner">Cannot read player data: {persisted.parse_error}</div>
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
          <PlayerHeaderCard avatar={avatar} nickname={nickname} kid={kid} stoveLevel={stoveLevel} />

          <MetricsRow
            items={[
              { label: "Power", ...summaryMetric(summary.power) },
              { label: "Gems", ...summaryMetric(summary.gems) },
              { label: "Furnace Lv", ...summaryMetric(summary.furnace_level) },
              { label: "Furnace pwr", ...summaryMetric(summary.furnace_power) },
            ]}
          />

          {stamina && stamina.demands.length ? (
            <CollapsiblePanel
              title="Stamina budget"
              meta={stamina.enabled ? undefined : "planner off"}
              defaultOpen
            >
              <StaminaPanel stamina={stamina} />
            </CollapsiblePanel>
          ) : null}

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
            <CollapsiblePanel title="Alliance / Exploration / Arena" defaultOpen={false}>
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

          {heroView ? <HeroesSection heroView={heroView} heroFilter={heroFilter} /> : null}
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
