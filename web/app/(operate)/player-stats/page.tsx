"use client";

import { Suspense, useEffect, useState } from "react";
import { PowerGrowthChart } from "@/components/player-stats/PowerGrowthChart";
import { useFleet } from "@/components/FleetContextProvider";
import { ErrorBanner } from "@/components/feedback";
import { FleetPageHeader } from "@/components/FleetPageHeader";
import { fetchPlayerStats } from "@/lib/api";
import { playerStateHref } from "@/lib/fleet-links";
import { PageLoading } from "@/components/ui/Spinner";
import type { PlayerStatsView } from "@/lib/types";
import Link from "next/link";

function PlayerStatsContent() {
  const { instanceId, playerId, playersError } = useFleet();

  const [stats, setStats] = useState<PlayerStatsView | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!playerId) {
      setStats(null);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError(null);
    fetchPlayerStats(playerId)
      .then((data) => {
        if (!cancelled) setStats(data);
      })
      .catch((e: Error) => {
        if (!cancelled) {
          setError(e.message);
          setStats(null);
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [playerId]);

  const latest = stats?.series.at(-1);
  const bannerError = error ?? playersError;

  return (
    <div className="page-stack">
      <FleetPageHeader title="Player statistics" showPlayer>
        <p>
          Daily power growth and furnace level milestones (SQLite). Select a player
          in the header.
        </p>
      </FleetPageHeader>

      {playerId ? (
        <div className="toolbar">
          <Link
            href={playerStateHref(playerId, { instanceId })}
            className="btn-secondary"
          >
            Open player state
          </Link>
        </div>
      ) : null}

      {bannerError ? <ErrorBanner message={bannerError} /> : null}
      {loading ? <PageLoading message="Loading statistics…" /> : null}

      {!loading && stats ? (
        <>
          <div className="metric-row">
            <div className="metric-card">
              <span className="metric-card__label">Nickname</span>
              <span className="metric-card__value">{stats.nickname || "—"}</span>
            </div>
            <div className="metric-card">
              <span className="metric-card__label">Latest power</span>
              <span className="metric-card__value">
                {latest ? latest.power.toLocaleString() : "—"}
              </span>
            </div>
            <div className="metric-card">
              <span className="metric-card__label">Furnace level</span>
              <span className="metric-card__value">
                {latest ? String(latest.furnace_level) : "—"}
              </span>
            </div>
            <div className="metric-card">
              <span className="metric-card__label">Days tracked</span>
              <span className="metric-card__value">{stats.series.length}</span>
            </div>
            <div className="metric-card">
              <span className="metric-card__label">Level-ups</span>
              <span className="metric-card__value">{stats.level_events.length}</span>
            </div>
          </div>

          <section className="panel">
            <h2 className="panel__title">Power over time</h2>
            <PowerGrowthChart
              series={stats.series}
              levelEvents={stats.level_events}
            />
          </section>

          {stats.level_events.length > 0 ? (
            <section className="panel">
              <h2 className="panel__title">Level milestones</h2>
              <ul className="player-stats-milestones">
                {stats.level_events.map((ev) => (
                  <li key={`${ev.day}-${ev.level}`}>
                    <time dateTime={ev.day}>{ev.day}</time>
                    <span>Furnace level {ev.level}</span>
                  </li>
                ))}
              </ul>
            </section>
          ) : null}
        </>
      ) : null}

      {!loading && !stats && playerId && !bannerError ? (
        <p className="meta">No statistics for this player yet.</p>
      ) : null}

      {!playerId ? (
        <p className="meta">Select a player in the header to view statistics.</p>
      ) : null}
    </div>
  );
}

export default function PlayerStatsPage() {
  return (
    <Suspense fallback={<PageLoading message="Player statistics…" />}>
      <PlayerStatsContent />
    </Suspense>
  );
}
