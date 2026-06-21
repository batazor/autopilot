"use client";

import { useState } from "react";

export function CopyableCode({ code }: { code: string }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard?.writeText(code);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1400);
    } catch {
      /* clipboard unavailable */
    }
  };
  return (
    <button
      type="button"
      onClick={copy}
      title="Copy code"
      aria-label={`Copy code ${code}`}
      className="group inline-flex cursor-pointer items-center gap-1.5 border-0 bg-transparent p-0 text-left"
    >
      <code>{code}</code>
      <span
        aria-hidden
        className={`text-xs ${copied ? "text-emerald-400" : "text-wos-text-muted opacity-0 transition-opacity group-hover:opacity-100"}`}
      >
        {copied ? "✓" : "⧉"}
      </span>
    </button>
  );
}
