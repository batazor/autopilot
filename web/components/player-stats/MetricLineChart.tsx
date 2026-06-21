"use client";

type Point = { day: string; value: number };

type Props = {
  series: Point[];
  label: string;
  unit?: string;
  format?: (n: number) => string;
  emptyMessage?: string;
  width?: number;
  height?: number;
};

const PAD = { top: 24, right: 20, bottom: 36, left: 56 };

function defaultFormat(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  return String(n);
}

export function MetricLineChart({
  series,
  label,
  unit = "",
  format = defaultFormat,
  emptyMessage,
  width = 720,
  height = 240,
}: Props) {
  if (!series.length) {
    return (
      <p className="meta">
        {emptyMessage || `No ${label.toLowerCase()} history yet.`}
      </p>
    );
  }

  const innerW = width - PAD.left - PAD.right;
  const innerH = height - PAD.top - PAD.bottom;
  const values = series.map((p) => p.value);
  const minV = Math.min(...values);
  const maxV = Math.max(...values);
  const span = Math.max(maxV - minV, 1);
  const n = series.length;

  const xAt = (i: number) =>
    PAD.left + (n <= 1 ? innerW / 2 : (i / (n - 1)) * innerW);
  const yAt = (v: number) =>
    PAD.top + innerH - ((v - minV) / span) * innerH;

  const linePoints = series.map((p, i) => `${xAt(i)},${yAt(p.value)}`).join(" ");

  return (
    <figure className="power-growth-chart">
      <svg
        viewBox={`0 0 ${width} ${height}`}
        className="power-growth-chart__svg"
        role="img"
        aria-label={`${label} over time`}
      >
        <defs>
          <linearGradient id={`fill-${label}`} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="var(--accent)" stopOpacity="0.25" />
            <stop offset="100%" stopColor="var(--accent)" stopOpacity="0" />
          </linearGradient>
        </defs>

        {[0, 0.25, 0.5, 0.75, 1].map((t) => {
          const y = PAD.top + innerH * (1 - t);
          const val = Math.round(minV + span * t);
          return (
            <g key={t}>
              <line
                x1={PAD.left}
                x2={width - PAD.right}
                y1={y}
                y2={y}
                className="power-growth-chart__grid"
              />
              <text
                x={PAD.left - 8}
                y={y + 4}
                textAnchor="end"
                className="power-growth-chart__tick"
              >
                {format(val)}
              </text>
            </g>
          );
        })}

        {n > 1 ? (
          <polygon
            points={`${PAD.left},${PAD.top + innerH} ${linePoints} ${PAD.left + innerW},${PAD.top + innerH}`}
            fill={`url(#fill-${label})`}
          />
        ) : null}

        <polyline
          points={linePoints}
          fill="none"
          className="power-growth-chart__line"
          strokeWidth={2}
        />

        {series.map((p, i) => (
          <circle
            key={p.day}
            cx={xAt(i)}
            cy={yAt(p.value)}
            r={4}
            className="power-growth-chart__dot"
          >
            <title>{`${p.day}: ${p.value.toLocaleString()}${unit ? " " + unit : ""}`}</title>
          </circle>
        ))}

        {series.map((p, i) => {
          if (n > 12 && i % Math.ceil(n / 8) !== 0 && i !== n - 1) return null;
          return (
            <text
              key={`lbl-${p.day}`}
              x={xAt(i)}
              y={height - 10}
              textAnchor="middle"
              className="power-growth-chart__tick"
            >
              {p.day.slice(5)}
            </text>
          );
        })}
      </svg>
      <figcaption className="meta power-growth-chart__caption">
        Daily {label.toLowerCase()} (last value per day).
      </figcaption>
    </figure>
  );
}
