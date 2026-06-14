"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { AppListbox } from "@/components/headless";
import { ErrorBanner } from "@/components/feedback";
import { FleetPageHeader } from "@/components/FleetPageHeader";
import { MetricLineChart } from "@/components/player-stats/MetricLineChart";
import { fetchAlliances, fetchAllianceStats, fetchLicenseStatus } from "@/lib/api";
import { tierAtLeast } from "@/lib/nav-locks";
import { PageLoading } from "@/components/ui/Spinner";
import type { AllianceStatsView } from "@/lib/types";

function R4Gate() {
  return (
    <div className="page-stack">
      <FleetPageHeader title="Alliance statistics" />
      <section className="panel">
        <div className="flex items-start gap-4">
          <span
            className="rounded-full border border-amber-400/40 bg-amber-500/15 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-amber-300"
            aria-hidden
          >
            R4
          </span>
          <div className="min-w-0">
            <h2 className="m-0 text-base font-semibold text-wos-text">
              Alliance statistics is an R4 feature
            </h2>
            <p className="muted mt-1">
              Daily alliance power, rank, level, and member-count charts are
              part of the R4 tier. Activate an R4 license to view this page.
            </p>
            <div className="mt-3">
              <Link href="/license" className="btn-primary">
                Open License
              </Link>
            </div>
          </div>
        </div>
      </section>
    </div>
  );
}

export default function AllianceStatsPage() {
  const [tier, setTier] = useState<string | null | undefined>(undefined);
  useEffect(() => {
    let cancelled = false;
    const pull = () => {
      fetchLicenseStatus()
        .then((st) => {
          if (!cancelled) setTier(st.active && st.tier ? st.tier : null);
        })
        .catch(() => {
          if (!cancelled) setTier(null);
        });
    };
    pull();
    window.addEventListener("wos:license:updated", pull);
    return () => {
      cancelled = true;
      window.removeEventListener("wos:license:updated", pull);
    };
  }, []);
  if (tier === undefined) return <PageLoading />;
  if (!tierAtLeast(tier, "r4")) return <R4Gate />;
  return <AllianceStatsInner />;
}

function AllianceStatsInner() {
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
          Daily alliance power, rank, level, and member count captured from the
          Alliance overview screen.
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
          an alliance name is captured or when the Alliance overview screen is
          synced.
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
              <span className="metric-card__label">Rank</span>
              <span className="metric-card__value">
                {latest ? `#${latest.rank || "—"}` : "—"}
              </span>
            </div>
            <div className="metric-card">
              <span className="metric-card__label">Level</span>
              <span className="metric-card__value">
                {latest ? String(latest.level || "—") : "—"}
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
            <h2 className="panel__title">Alliance rank over time</h2>
            <MetricLineChart
              label="Alliance rank"
              series={stats.series.map((d) => ({ day: d.day, value: d.rank }))}
              emptyMessage="No alliance rank history yet."
            />
          </section>

          <section className="panel">
            <h2 className="panel__title">Alliance level over time</h2>
            <MetricLineChart
              label="Alliance level"
              series={stats.series.map((d) => ({ day: d.day, value: d.level }))}
              emptyMessage="No alliance level history yet."
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
