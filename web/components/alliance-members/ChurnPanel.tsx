import { Card, Pill } from "@/components/ui";
import type { AllianceChurn, AllianceMemberBrief } from "@/lib/types";
import { fmtPower } from "./format";

function MemberList({ members }: { members: AllianceMemberBrief[] }) {
  if (!members.length) return <p className="meta">—</p>;
  return (
    <ul className="flex flex-col gap-1 text-sm">
      {members.map((m) => (
        <li key={`${m.rank}:${m.name}`} className="flex items-center justify-between gap-2">
          <span className="truncate">
            {m.name || "—"} <span className="meta">R{m.rank}</span>
          </span>
          <span className="shrink-0 tabular-nums meta">{fmtPower(m.power)}</span>
        </li>
      ))}
    </ul>
  );
}

export function ChurnPanel({ churn }: { churn: AllianceChurn }) {
  if (!churn.available) {
    const msg =
      churn.reason === "partial_scan"
        ? `Last scan was partial (${churn.parsed ?? "?"}/${churn.expected ?? "?"} read) — changes hidden to avoid false "left".`
        : "Need at least two scans to compare. Scan again later to see who joined or left.";
    return (
      <Card title="Membership changes">
        <p className="meta">{msg}</p>
      </Card>
    );
  }
  return (
    <Card title="Membership changes">
      <div className="grid gap-4 sm:grid-cols-2">
        <div>
          <div className="mb-2">
            <Pill tone="ok" dot>
              Joined {churn.joined.length}
            </Pill>
          </div>
          <MemberList members={churn.joined} />
        </div>
        <div>
          <div className="mb-2">
            <Pill tone="danger" dot>
              Left {churn.left.length}
            </Pill>
          </div>
          <MemberList members={churn.left} />
        </div>
      </div>
    </Card>
  );
}
