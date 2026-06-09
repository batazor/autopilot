"use client";

import Link from "next/link";
import { useState } from "react";
import { useAttention } from "@/components/attention/useAttention";
import { attentionAction } from "@/lib/attention";
import { Icon } from "@/components/ui/Icon";

/**
 * Global strip under the header: critical fleet problems only. Warnings live
 * on the overview AttentionPanel — a strip that nags on every page must mean
 * "the bot is not making progress", or it trains operators to ignore it.
 */
export function AttentionBanner() {
  const [expanded, setExpanded] = useState(false);
  const { data } = useAttention();

  const critical = (data?.items ?? []).filter((i) => i.severity === "critical");
  if (critical.length === 0) return null;

  return (
    <div className="border-b border-red-500/40 bg-red-500/10 text-xs text-red-100">
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        className="flex w-full items-center gap-3 px-4 py-2 text-left hover:bg-red-500/15"
        aria-expanded={expanded}
      >
        <Icon name="alert" size="sm" className="shrink-0 text-red-400" />
        <span className="flex-1 font-medium">
          {critical.length === 1
            ? critical[0].title
            : `${critical.length} issues need attention — ${critical[0].title}`}
        </span>
        <Icon
          name={expanded ? "chevron-left" : "chevron-right"}
          size="sm"
          className="shrink-0 text-red-300"
        />
      </button>
      {expanded && (
        <ul className="space-y-1 px-4 pb-3 pl-11">
          {critical.map((item) => {
            const action = attentionAction(item);
            return (
              <li key={item.id} className="break-words">
                <span className="font-medium text-red-200">{item.title}</span>
                {item.detail ? (
                  <span className="text-red-100/80"> — {item.detail}</span>
                ) : null}
                {action ? (
                  <>
                    {" "}
                    <Link
                      href={action.href}
                      className="font-medium text-red-200 underline underline-offset-2"
                    >
                      {action.label}
                    </Link>
                  </>
                ) : null}
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
