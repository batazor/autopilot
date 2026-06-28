import { Card } from "@/components/ui";
import type { AllianceRankCount } from "@/lib/types";

export function RankBreakdown({ ranks }: { ranks: AllianceRankCount[] }) {
  const max = Math.max(1, ...ranks.map((r) => r.count));
  return (
    <Card title="Rank composition">
      <div className="flex flex-col gap-2">
        {ranks.map((r) => (
          <div key={r.rank} className="flex items-center gap-3">
            <span className="w-8 shrink-0 font-medium">{r.label}</span>
            <div className="h-2 flex-1 overflow-hidden rounded-full bg-wos-surface">
              <div
                className="h-full rounded-full bg-wos-accent/70"
                style={{ width: `${(r.count / max) * 100}%` }}
              />
            </div>
            <span className="w-8 shrink-0 text-right tabular-nums">{r.count}</span>
          </div>
        ))}
      </div>
    </Card>
  );
}
