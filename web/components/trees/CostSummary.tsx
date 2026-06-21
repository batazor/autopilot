import { fmtDuration, fmtNum } from "@/lib/trees/format";

export function CostSummary({
  title,
  rows,
  totalTime,
  note,
}: {
  title: string;
  rows: { icon: string; name: string; amount: number }[];
  totalTime: number;
  note: string;
}) {
  if (!rows.length && totalTime <= 0) return null;
  return (
    <div className="cost-summary">
      <div className="cost-summary__title">{title}</div>
      <div className="cost-summary__stats">
        {rows.map((r) => (
          <span key={r.name} title={r.name} className="cost-stat">
            <span className="cost-stat__icon">{r.icon}</span>
            {fmtNum(r.amount)}
          </span>
        ))}
        <span title="Total time" className="cost-stat cost-stat--time">
          <span className="cost-stat__icon">⏱</span>
          {fmtDuration(totalTime)}
        </span>
      </div>
      <div className="cost-summary__note">{note}</div>
    </div>
  );
}
