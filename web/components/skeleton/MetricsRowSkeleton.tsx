type MetricsRowSkeletonProps = {
  count?: number;
  className?: string;
};

export function MetricsRowSkeleton({
  count = 5,
  className = "",
}: MetricsRowSkeletonProps) {
  return (
    <div className={`metrics-row ${className}`.trim()} aria-hidden>
      {Array.from({ length: count }, (_, i) => (
        <div key={i} className="metric-card skeleton-block">
          <div className="skeleton-line skeleton-line--sm" />
          <div className="skeleton-line skeleton-line--lg" />
        </div>
      ))}
    </div>
  );
}
