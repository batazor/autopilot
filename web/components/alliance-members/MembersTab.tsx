"use client";

import { useEffect, useState } from "react";
import { Button, Chip } from "@/components/ui";
import { ErrorBanner } from "@/components/feedback";
import { PageLoading } from "@/components/ui/Spinner";
import { useFleet } from "@/components/FleetContextProvider";
import { fetchAllianceMembersAnalysis, scanAllianceMembers } from "@/lib/api";
import { downloadCsv, type CsvColumn } from "@/lib/csv";
import type { AllianceMemberRow, AllianceMembersAnalysis } from "@/lib/types";
import { lastActiveLabel } from "./format";
import { MembersSummary } from "./MembersSummary";
import { RankBreakdown } from "./RankBreakdown";
import { ChurnPanel } from "./ChurnPanel";
import { MembersTable } from "./MembersTable";

const INACTIVE_PRESETS = [1, 3, 7];

// Raw (non-compact) numbers so the CSV stays analysable in Excel / Sheets.
const MEMBER_CSV_COLUMNS: CsvColumn<AllianceMemberRow>[] = [
  { header: "Name", value: (m) => m.name },
  { header: "Rank", value: (m) => m.rank },
  { header: "Power", value: (m) => m.power },
  { header: "Level", value: (m) => m.level },
  { header: "Online", value: (m) => (m.online ? "yes" : "no") },
  { header: "Last active", value: (m) => lastActiveLabel(m) },
  { header: "Last online (s)", value: (m) => m.last_online_seconds ?? "" },
  {
    header: "Days inactive",
    value: (m) =>
      m.online || m.last_online_seconds == null
        ? ""
        : Math.round((m.last_online_seconds / 86_400) * 10) / 10,
  },
];

function csvFilename(allianceName: string): string {
  const safe = allianceName.replace(/[^\w.-]+/g, "_") || "alliance";
  const day = new Date().toISOString().slice(0, 10);
  return `${safe}-members-${day}.csv`;
}

export function MembersTab({ allianceName }: { allianceName: string }) {
  const { instanceId, playerId } = useFleet();
  const [inactiveDays, setInactiveDays] = useState(3);
  const [data, setData] = useState<AllianceMembersAnalysis | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [scanning, setScanning] = useState(false);
  const [scanMsg, setScanMsg] = useState<string | null>(null);

  useEffect(() => {
    if (!allianceName) {
      setData(null);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError(null);
    fetchAllianceMembersAnalysis(allianceName, inactiveDays)
      .then((d) => {
        if (!cancelled) setData(d);
      })
      .catch((e: Error) => {
        if (!cancelled) {
          setError(e.message);
          setData(null);
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [allianceName, inactiveDays]);

  async function onScan() {
    setScanning(true);
    setScanMsg(null);
    try {
      await scanAllianceMembers(instanceId, playerId);
      setScanMsg(
        `Scan queued on ${instanceId}. It runs the selected account's alliance — refresh in a minute once it completes.`,
      );
    } catch (e) {
      setScanMsg(e instanceof Error ? e.message : String(e));
    } finally {
      setScanning(false);
    }
  }

  function onExport() {
    if (!data?.members.length) return;
    downloadCsv(csvFilename(data.alliance_name), data.members, MEMBER_CSV_COLUMNS);
  }

  const hasMembers = Boolean(data?.members.length);

  return (
    <div className="page-stack">
      <div className="toolbar items-center justify-between">
        <div className="inline-flex items-center gap-2">
          <span className="meta">Inactive after</span>
          {INACTIVE_PRESETS.map((d) => (
            <Chip key={d} active={inactiveDays === d} onClick={() => setInactiveDays(d)}>
              {d}d
            </Chip>
          ))}
        </div>
        <div className="inline-flex items-center gap-2">
          <Button
            variant="secondary"
            disabled={!hasMembers}
            onClick={onExport}
            title={hasMembers ? "Download the member list as CSV" : "No members to export"}
          >
            Export CSV
          </Button>
          <Button
            variant="secondary"
            pending={scanning}
            disabled={!instanceId}
            onClick={onScan}
            title={
              instanceId
                ? "Queue a roster scan on the selected account"
                : "Select an instance in the header first"
            }
          >
            Scan now
          </Button>
        </div>
      </div>

      {!instanceId ? (
        <p className="meta">Select an instance in the header to enable scanning.</p>
      ) : null}
      {scanMsg ? <p className="meta">{scanMsg}</p> : null}
      {error ? <ErrorBanner message={error} /> : null}
      {loading ? <PageLoading message="Loading member analysis…" /> : null}

      {!loading && data ? (
        <>
          <MembersSummary data={data} />
          <div className="grid gap-4 md:grid-cols-2">
            <RankBreakdown ranks={data.analytics.ranks} />
            <ChurnPanel churn={data.analytics.churn} />
          </div>
          <MembersTable
            members={data.members}
            inactiveDays={data.analytics.activity.threshold_days}
          />
        </>
      ) : null}

      {!loading && !data && !error && allianceName ? (
        <p className="meta">
          No roster captured yet for {allianceName}. Press “Scan now” to read the member
          list, then refresh.
        </p>
      ) : null}
    </div>
  );
}
