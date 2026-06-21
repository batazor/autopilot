"use client";

import type { PlayerLevelEvent, PlayerPowerDay } from "@/lib/types";

type Props = {
  series: PlayerPowerDay[];
  levelEvents: PlayerLevelEvent[];
  width?: number;
  height?: number;
};

const PAD = { top: 24, right: 20, bottom: 36, left: 56 };

function formatPower(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  return String(n);
}

export function PowerGrowthChart({
  series,
  levelEvents,
  width = 720,
  height = 280,
}: Props) {
  if (!series.length) {
    return <p className="meta">No power history yet — stats are recorded when persisted state updates.</p>;
  }

  const innerW = width - PAD.left - PAD.right;
  const innerH = height - PAD.top - PAD.bottom;
  const powers = series.map((p) => p.power);
  const minP = Math.min(...powers);
  const maxP = Math.max(...powers);
  const span = Math.max(maxP - minP, 1);
  const n = series.length;

  const xAt = (i: number) => PAD.left + (n <= 1 ? innerW / 2 : (i / (n - 1)) * innerW);
  const yAt = (power: number) =>
    PAD.top + innerH - ((power - minP) / span) * innerH;

  const linePoints = series.map((p, i) => `${xAt(i)},${yAt(p.power)}`).join(" ");

  const dayToIndex = new Map(series.map((p, i) => [p.day, i]));

  return (
    <figure className="power-growth-chart">
      <svg
        viewBox={`0 0 ${width} ${height}`}
        className="power-growth-chart__svg"
        role="img"
        aria-label="Player power over time with furnace level-ups"
      >
        <defs>
          <linearGradient id="powerFill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="var(--accent)" stopOpacity="0.25" />
            <stop offset="100%" stopColor="var(--accent)" stopOpacity="0" />
          </linearGradient>
        </defs>

        {[0, 0.25, 0.5, 0.75, 1].map((t) => {
          const y = PAD.top + innerH * (1 - t);
          const val = Math.round(minP + span * t);
          return (
            <g key={t}>
              <line
                x1={PAD.left}
                x2={width - PAD.right}
                y1={y}
                y2={y}
                className="power-growth-chart__grid"
              />
              <text x={PAD.left - 8} y={y + 4} textAnchor="end" className="power-growth-chart__tick">
                {formatPower(val)}
              </text>
            </g>
          );
        })}

        {series.length > 1 ? (
          <polygon
            points={`${PAD.left},${PAD.top + innerH} ${linePoints} ${PAD.left + innerW},${PAD.top + innerH}`}
            fill="url(#powerFill)"
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
            cy={yAt(p.power)}
            r={4}
            className="power-growth-chart__dot"
          >
            <title>{`${p.day}: ${p.power.toLocaleString()} power (furnace ${p.furnace_level})`}</title>
          </circle>
        ))}

        {levelEvents.map((ev) => {
          const idx = dayToIndex.get(ev.day);
          if (idx === undefined) return null;
          const x = xAt(idx);
          const yBase = PAD.top + innerH;
          const peakY = PAD.top + 8;
          return (
            <g key={`${ev.day}-${ev.level}`} className="power-growth-chart__level">
              <line
                x1={x}
                x2={x}
                y1={yBase}
                y2={peakY + 14}
                strokeDasharray="4 3"
              />
              <polygon
                points={`${x},${peakY} ${x - 6},${peakY + 14} ${x + 6},${peakY + 14}`}
                className="power-growth-chart__peak"
              />
              <text
                x={x}
                y={peakY - 4}
                textAnchor="middle"
                className="power-growth-chart__level-label"
              >
                Lv {ev.level}
              </text>
              <title>{`Furnace level ${ev.level} on ${ev.day}`}</title>
            </g>
          );
        })}

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
        Daily power (last value per day). Triangles mark furnace level-ups.
      </figcaption>
    </figure>
  );
}
