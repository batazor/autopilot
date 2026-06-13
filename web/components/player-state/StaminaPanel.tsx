"use client";

import type { PlayerStaminaView, StaminaDemandRow } from "@/lib/types";

function fmtDuration(seconds: number | null): string {
  if (seconds === null || !Number.isFinite(seconds)) return "—";
  if (seconds <= 0) return "now";
  const m = Math.round(seconds / 60);
  if (m < 60) return `${m}m`;
  const h = Math.floor(m / 60);
  return `${h}h ${m % 60}m`;
}

function fmtAge(readAt: number | null): string {
  if (!readAt) return "never read";
  const ageS = Date.now() / 1000 - readAt;
  if (ageS < 0) return "just now";
  return `${fmtDuration(ageS)} ago`;
}

function prettyVerdict(row: StaminaDemandRow): string {
  if (row.selected) return "running";
  if (!row.verdict) return "—";
  return row.verdict.replace(/_/g, " ");
}

function Pill({ tone, children }: { tone: "ok" | "warn" | "muted"; children: React.ReactNode }) {
  const bg =
    tone === "ok"
      ? "var(--wos-status-ok-bg)"
      : tone === "warn"
        ? "var(--wos-status-warn-bg)"
        : "var(--wos-panel-raised)";
  const fg =
    tone === "ok"
      ? "var(--wos-status-ok-fg)"
      : tone === "warn"
        ? "var(--wos-status-warn-fg)"
        : "var(--wos-text-muted)";
  return (
    <span
      className="inline-block rounded px-2 py-0.5 text-xs"
      style={{ background: bg, color: fg }}
    >
      {children}
    </span>
  );
}

function Bar({ ratio, tone }: { ratio: number; tone: "accent" | "warn" }) {
  const pct = Math.max(0, Math.min(1, ratio)) * 100;
  const color = tone === "warn" ? "var(--wos-status-warn-fg)" : "var(--wos-accent)";
  return (
    <div
      className="h-1.5 w-full overflow-hidden rounded-full"
      style={{ background: "var(--wos-panel-raised)" }}
    >
      <div className="h-full rounded-full" style={{ width: `${pct}%`, background: color }} />
    </div>
  );
}

export function StaminaPanel({ stamina }: { stamina: PlayerStaminaView }) {
  const { est, cap, regen_per_hour, seconds_to_cap, demands, recent } = stamina;
  const ratio = est !== null && cap > 0 ? est / cap : 0;
  const nearCap = ratio >= 0.9 || stamina.overflow_pressure;

  return (
    <div>
      {!stamina.enabled ? (
        <div className="mb-3 text-xs text-wos-text-muted">
          Planner is disabled (<code>budget.yaml · enabled: false</code>) — this is the
          snapshot it would act on once enabled.
        </div>
      ) : null}

      {/* Stamina gauge */}
      <div className="mb-1 flex items-baseline gap-2">
        <span
          className="text-2xl font-semibold"
          style={{ color: nearCap ? "var(--wos-status-warn-fg)" : "var(--wos-text)" }}
        >
          {est === null ? "—" : Math.floor(est)}
        </span>
        <span className="text-sm text-wos-text-secondary">/ {cap}</span>
        {nearCap && est !== null ? (
          <span className="text-xs" style={{ color: "var(--wos-status-warn-fg)" }}>
            caps in {fmtDuration(seconds_to_cap)} — surplus will burn
          </span>
        ) : null}
      </div>
      <div className="mb-4">
        <Bar ratio={ratio} tone={nearCap ? "warn" : "accent"} />
      </div>

      {/* Metric cards */}
      <div className="mb-4 grid gap-3 [grid-template-columns:repeat(auto-fit,minmax(8rem,1fr))]">
        <div className="panel !p-3">
          <div className="text-xs uppercase tracking-wide text-wos-text-muted">Regen</div>
          <div className="mt-1 text-xl font-semibold text-wos-text">{regen_per_hour}/h</div>
        </div>
        <div className="panel !p-3">
          <div className="text-xs uppercase tracking-wide text-wos-text-muted">Caps in</div>
          <div className="mt-1 text-xl font-semibold text-wos-text">
            {fmtDuration(seconds_to_cap)}
          </div>
        </div>
        <div className="panel !p-3">
          <div className="text-xs uppercase tracking-wide text-wos-text-muted">Last read</div>
          <div className="mt-1 text-xl font-semibold text-wos-text">
            {fmtAge(stamina.stamina_read_at)}
          </div>
        </div>
      </div>

      {/* Demand table */}
      <div className="data-table-wrap mb-3">
        <table className="data-table">
          <thead>
            <tr>
              <th>Demand</th>
              <th>Prio</th>
              <th>Cost</th>
              <th>Today</th>
              <th>Window</th>
              <th>Verdict</th>
            </tr>
          </thead>
          <tbody>
            {demands.map((d) => {
              const unlimited = d.daily_quota === null;
              const qRatio = unlimited || !d.daily_quota ? 0 : d.quota_used / d.daily_quota;
              return (
                <tr key={d.id}>
                  <td style={{ fontWeight: d.selected ? 600 : 400 }}>{d.id}</td>
                  <td className="text-wos-text-secondary">{d.priority}</td>
                  <td className="text-wos-text-secondary">{d.cost}</td>
                  <td>
                    {unlimited ? (
                      <span className="text-xs text-wos-text-muted">∞ sink</span>
                    ) : (
                      <div className="flex items-center gap-2">
                        <div className="min-w-[3rem] flex-1">
                          <Bar ratio={qRatio} tone="accent" />
                        </div>
                        <span className="text-xs text-wos-text-secondary">
                          {d.quota_used}/{d.daily_quota}
                        </span>
                      </div>
                    )}
                  </td>
                  <td>
                    <Pill tone={d.active ? "ok" : "muted"}>{d.active ? "active" : "closed"}</Pill>
                  </td>
                  <td>
                    <span
                      style={{ color: d.selected ? "var(--wos-status-ok-fg)" : undefined }}
                      className={d.selected ? "" : "text-wos-text-secondary"}
                    >
                      {prettyVerdict(d)}
                    </span>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {/* Last decision + recent trace */}
      <div className="text-xs text-wos-text-secondary">
        <span className="text-wos-text-muted">Last decision: </span>
        <span className="font-medium text-wos-text">{stamina.action}</span>
        {stamina.target ? ` → ${stamina.target}` : ""} ({stamina.reason})
      </div>
      {recent.length > 1 ? (
        <details className="mt-2 text-xs text-wos-text-muted">
          <summary className="cursor-pointer">Recent decisions ({recent.length})</summary>
          <ul className="mt-1 space-y-0.5">
            {recent.slice(0, 10).map((r, i) => (
              <li key={i}>
                {String(r.action ?? "")}
                {r.target ? ` → ${String(r.target)}` : ""} · {String(r.reason ?? "")}
              </li>
            ))}
          </ul>
        </details>
      ) : null}
    </div>
  );
}
