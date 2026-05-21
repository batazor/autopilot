"use client";

import { useEffect, useState } from "react";
import { AppListbox } from "@/components/headless";
import { ErrorBanner } from "@/components/feedback";
import { FleetPageHeader } from "@/components/FleetPageHeader";
import { MetricLineChart } from "@/components/player-stats/MetricLineChart";
import { fetchAlliances, fetchAllianceStats } from "@/lib/api";
import { PageLoading } from "@/components/ui/Spinner";
import type { AllianceStatsView } from "@/lib/types";

export default function AllianceStatsPage() {
  const [alliances, setAlliances] = useState<string[]>([]);
  const [selected, setSelected] = useState("");
  const [stats, setStats] = useState<AllianceStatsView | null>(null);
  const [loadingList, setLoadingList] = useState(false);
  const [loadingStats, setLoadingStats] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoadingList(true);
    setError(null);
    fetchAlliances()
      .then((names) => {
        if (cancelled) return;
        setAlliances(names);
        if (names.length && !selected) setSelected(names[0]);
      })
      .catch((e: Error) => {
        if (!cancelled) setError(e.message);
      })
      .finally(() => {
        if (!cancelled) setLoadingList(false);
      });
    return () => {
      cancelled = true;
    };
    // run once on mount; selected initializes from the list
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!selected) {
      setStats(null);
      return;
    }
    let cancelled = false;
    setLoadingStats(true);
    setError(null);
    fetchAllianceStats(selected)
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
        if (!cancelled) setLoadingStats(false);
      });
    return () => {
      cancelled = true;
    };
  }, [selected]);

  const latest = stats?.series.at(-1);

  return (
    <div className="page-stack">
      <FleetPageHeader title="Alliance statistics">
        <p>
          Daily alliance power and member count, captured from per-player
          snapshots (SQLite).
        </p>
      </FleetPageHeader>

      <div className="toolbar">
        <AppListbox
          label="Alliance"
          options={alliances.map((a) => ({ value: a, label: a }))}
          value={selected}
          onChange={setSelected}
          placeholder={loadingList ? "Loading…" : "Select alliance…"}
          loading={loadingList}
          inline
        />
      </div>

      {error ? <ErrorBanner message={error} /> : null}
      {loadingStats ? <PageLoading message="Loading alliance statistics…" /> : null}

      {!loadingList && alliances.length === 0 ? (
        <p className="meta">
          No alliances recorded yet — snapshots are written when a player with
          an alliance name has their state persisted.
        </p>
      ) : null}

      {!loadingStats && stats ? (
        <>
          <div className="metric-row">
            <div className="metric-card">
              <span className="metric-card__label">Alliance</span>
              <span className="metric-card__value">{stats.alliance_name}</span>
            </div>
            <div className="metric-card">
              <span className="metric-card__label">Latest power</span>
              <span className="metric-card__value">
                {latest ? latest.power.toLocaleString() : "—"}
              </span>
            </div>
            <div className="metric-card">
              <span className="metric-card__label">Members</span>
              <span className="metric-card__value">
                {latest ? `${latest.members_count} / ${latest.members_max}` : "—"}
              </span>
            </div>
            <div className="metric-card">
              <span className="metric-card__label">Days tracked</span>
              <span className="metric-card__value">{stats.series.length}</span>
            </div>
          </div>

          <section className="panel">
            <h2 className="panel__title">Alliance power over time</h2>
            <MetricLineChart
              label="Alliance power"
              series={stats.series.map((d) => ({ day: d.day, value: d.power }))}
              emptyMessage="No alliance power history yet."
            />
          </section>

          <section className="panel">
            <h2 className="panel__title">Member count over time</h2>
            <MetricLineChart
              label="Members"
              series={stats.series.map((d) => ({
                day: d.day,
                value: d.members_count,
              }))}
              emptyMessage="No member-count history yet."
            />
          </section>
        </>
      ) : null}
    </div>
  );
}
