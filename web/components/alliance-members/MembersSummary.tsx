import { MetricCard, MetricGrid } from "@/components/ui";
import type { AllianceMembersAnalysis } from "@/lib/types";
import { fmtPower } from "./format";

export function MembersSummary({ data }: { data: AllianceMembersAnalysis }) {
  const { power, activity } = data.analytics;
  return (
    <MetricGrid>
      <MetricCard label="Members" value={data.member_count} />
      <MetricCard
        label="Online now"
        value={activity.online_now}
        tone={activity.online_now > 0 ? "ok" : "neutral"}
      />
      <MetricCard
        label={`Inactive ≥ ${activity.threshold_days}d`}
        value={activity.inactive_count}
        tone={activity.inactive_count > 0 ? "warn" : "ok"}
        hint={activity.unknown_count ? `${activity.unknown_count} unknown` : undefined}
      />
      <MetricCard label="Total power" value={fmtPower(power.total)} />
      <MetricCard
        label="Avg power"
        value={fmtPower(power.avg)}
        hint={`median ${fmtPower(power.median)}`}
      />
      <MetricCard
        label="Power range"
        value={`${fmtPower(power.min)} – ${fmtPower(power.max)}`}
      />
    </MetricGrid>
  );
}
