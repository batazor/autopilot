"use client";

import { useEffect, useState } from "react";
import { Icon } from "@/components/ui/Icon";
import { fetchVersion } from "@/lib/api";
import type { VersionView } from "@/lib/types";

// Refresh cadence — the backend already caches 1h against GHCR, so polling
// every 10 min on the client just keeps the badge fresh after a release.
const POLL_MS = 10 * 60 * 1000;

function useVersion(): VersionView | null {
  const [v, setV] = useState<VersionView | null>(null);
  useEffect(() => {
    let cancelled = false;
    const pull = () => {
      fetchVersion()
        .then((r) => {
          if (!cancelled) setV(r);
        })
        .catch(() => {
          if (!cancelled) setV(null);
        });
    };
    pull();
    const id = window.setInterval(pull, POLL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, []);
  return v;
}

function releasesUrl(v: VersionView): string {
  if (v.remote?.html_url) return v.remote.html_url;
  return `https://github.com/${v.repo}/tags`;
}

function currentLabel(v: VersionView): string {
  const ver = v.current.version || "dev";
  if (ver !== "dev" && ver !== "latest") return ver;
  const rev = v.current.revision;
  if (rev) return rev.slice(0, 7);
  return "dev";
}

/** Compact pill for the mobile header. Hidden unless an update is available. */
export function VersionBadge() {
  const v = useVersion();
  if (!v || !v.update_available) return null;
  const remoteVer = v.remote?.tag || "new";
  return (
    <a
      href={releasesUrl(v)}
      target="_blank"
      rel="noreferrer noopener"
      className="inline-flex shrink-0 items-center gap-1 rounded-md border border-emerald-400/40 bg-emerald-500/15 px-2 py-0.5 text-[11px] font-semibold uppercase tracking-wide text-emerald-200 no-underline transition hover:bg-emerald-500/25"
      title={`Update available: ${currentLabel(v)} → ${remoteVer}`}
    >
      <Icon name="arrow-up" size="sm" />
      <span>Update</span>
    </a>
  );
}

/** Verbose row for the sidebar footer — always renders (shows current build). */
export function VersionFooterRow() {
  const v = useVersion();
  if (!v) return null;
  const current = currentLabel(v);
  const url = releasesUrl(v);

  if (v.update_available) {
    const remoteVer = v.remote?.tag || "new";
    return (
      <a
        href={url}
        target="_blank"
        rel="noreferrer noopener"
        className="group flex items-center gap-2 rounded-md border border-emerald-400/40 bg-emerald-500/10 px-2 py-1.5 text-[11px] no-underline transition hover:bg-emerald-500/20"
        title={`Update available: ${current} → ${remoteVer}`}
      >
        <span className="inline-flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-emerald-500/20 text-emerald-200">
          <Icon name="arrow-up" size="sm" />
        </span>
        <span className="min-w-0 flex-1">
          <span className="block font-semibold text-emerald-200">
            Update available
          </span>
          <span className="block truncate text-[10px] text-emerald-200/70">
            {current} → {remoteVer}
          </span>
        </span>
      </a>
    );
  }

  return (
    <span
      className="flex items-center gap-1.5 text-[11px] text-wos-text-muted"
      title={v.reason === "dev_build" ? "Development build" : `Current build ${current}`}
    >
      <Icon name="info" size="sm" />
      <span className="truncate">
        {current.startsWith("v") ? current : `v${current}`}
        {v.reason === "github_unreachable" ? " · update check offline" : null}
      </span>
    </span>
  );
}
