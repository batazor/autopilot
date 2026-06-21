import Link from "next/link";
import type { ReactNode } from "react";

export type MetricTone = "neutral" | "ok" | "warn" | "danger" | "accent";

const TONE: Record<MetricTone, { stripe: string; value: string }> = {
  neutral: { stripe: "var(--wos-border)", value: "var(--wos-text)" },
  ok: { stripe: "var(--wos-status-ok-fg)", value: "var(--wos-status-ok-fg)" },
  warn: { stripe: "var(--wos-status-warn-fg)", value: "var(--wos-status-warn-fg)" },
  danger: { stripe: "var(--wos-status-danger-fg)", value: "var(--wos-status-danger-fg)" },
  accent: { stripe: "var(--wos-accent)", value: "var(--wos-accent-muted)" },
};

type MetricCardProps = {
  label: ReactNode;
  value: ReactNode;
  /** Semantic colour for the accent stripe + value. */
  tone?: MetricTone;
  /** Optional muted sub-line under the value. */
  hint?: ReactNode;
  title?: string;
  /** Render as a clickable link with a hover lift. */
  href?: string;
};

/**
 * Unified metric tile: a panel with a tone-coloured accent stripe, an uppercase
 * label, and a large tabular-numerals value. Supersedes the old per-page
 * `.metric-card` / `.stat-card` / inline `panel !p-3` variants. Wrap a set in
 * {@link MetricGrid}.
 */
export function MetricCard({ label, value, tone = "neutral", hint, title, href }: MetricCardProps) {
  const { stripe, value: valueColor } = TONE[tone];
  const base =
    "relative isolate overflow-hidden rounded-xl border border-wos-border-subtle bg-wos-panel p-4 transition-[transform,border-color,box-shadow] duration-200";
  const inner = (
    <>
      <span className="absolute inset-y-0 left-0 w-[3px]" style={{ background: stripe }} aria-hidden />
      <div className="text-[11px] font-semibold uppercase tracking-[0.08em] text-wos-text-muted">
        {label}
      </div>
      <div
        className="mt-1.5 text-2xl font-semibold leading-none tabular-nums"
        style={{ color: valueColor }}
      >
        {value}
      </div>
      {hint ? (
        <div className="mt-1 truncate text-[11px] font-medium text-wos-text-muted">{hint}</div>
      ) : null}
    </>
  );
  if (href) {
    return (
      <Link
        href={href}
        title={title}
        className={`${base} block no-underline hover:-translate-y-0.5 hover:border-wos-border hover:bg-wos-panel-raised/80 hover:shadow-[0_1px_2px_rgba(0,0,0,0.18),0_10px_26px_-12px_rgba(0,0,0,0.5)] motion-reduce:hover:translate-y-0`}
      >
        {inner}
      </Link>
    );
  }
  return (
    <div className={base} title={title}>
      {inner}
    </div>
  );
}
