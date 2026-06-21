import { MetricCard, MetricGrid } from "@/components/ui";

export function MetricsRow({
  items,
}: {
  items: { label: string; value: string | number; title?: string }[];
}) {
  return (
    <MetricGrid className="mb-4">
      {items.map((m) => (
        <MetricCard key={m.label} label={m.label} value={m.value} title={m.title} />
      ))}
    </MetricGrid>
  );
}
