"use client";

import { useState } from "react";

export function CopyButton({
  text,
  label = "Copy",
  title,
  className = "",
}: {
  text: string;
  label?: string;
  title?: string;
  className?: string;
}) {
  const [copied, setCopied] = useState(false);

  const onCopy = async () => {
    if (!text) return;
    try {
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(text);
      } else {
        const ta = document.createElement("textarea");
        ta.value = text;
        ta.style.position = "fixed";
        ta.style.left = "-9999px";
        document.body.appendChild(ta);
        ta.select();
        document.execCommand("copy");
        document.body.removeChild(ta);
      }
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1600);
    } catch {
      setCopied(false);
    }
  };

  return (
    <button
      type="button"
      className={`btn-secondary queue-copy-btn ${className}`.trim()}
      onClick={() => void onCopy()}
      disabled={!text}
      title={title ?? "Copy to clipboard"}
      aria-label={title ?? "Copy to clipboard"}
    >
      {copied ? "Copied" : label}
    </button>
  );
}
