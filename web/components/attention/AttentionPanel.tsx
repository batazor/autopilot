"use client";

import Link from "next/link";
import { useAttention } from "@/components/attention/useAttention";
import { attentionAction } from "@/lib/attention";
import type { AttentionItem } from "@/lib/types";

/**
 * Overview "needs attention" section: every open problem (critical and
 * warning), one row each, with a link to the page where it gets fixed.
 * Renders nothing when the fleet is healthy — absence is the success state.
 */
export function AttentionPanel() {
  const { data } = useAttention();
  const items = data?.items ?? [];
  if (items.length === 0) return null;

  return (
    <section className="panel my-6 border-red-500/30">
      <div className="fleet-section__head">
        <h2>Needs attention</h2>
        <span className="fleet-count">{items.length}</span>
      </div>
      <ul className="divide-y divide-white/5">
        {items.map((item) => (
          <AttentionRow key={item.id} item={item} />
        ))}
      </ul>
    </section>
  );
}

function AttentionRow({ item }: { item: AttentionItem }) {
  const action = attentionAction(item);
  const critical = item.severity === "critical";
  return (
    <li className="flex items-center gap-3 py-2 text-sm">
      <span
        className={`h-2 w-2 shrink-0 rounded-full ${critical ? "bg-red-400" : "bg-amber-400"}`}
        title={item.severity}
      />
      <div className="min-w-0 flex-1">
        <span className="font-medium">{item.title}</span>
        {item.detail ? (
          <span className="text-[color:var(--text-muted,inherit)] opacity-70">
            {" "}
            — {item.detail}
          </span>
        ) : null}
      </div>
      {action ? (
        <Link
          href={action.href}
          className="btn-secondary shrink-0"
          onClick={(e) => e.stopPropagation()}
        >
          {action.label}
        </Link>
      ) : null}
    </li>
  );
}
