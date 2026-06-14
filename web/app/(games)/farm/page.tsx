"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { ErrorBanner } from "@/components/feedback";
import { PageHeader } from "@/components/PageHeader";
import { PageLoading } from "@/components/ui/Spinner";
import { fetchLicenseStatus } from "@/lib/api";

type Pending = { username: string; started_at?: string } | null;
type FarmAccount = {
  username: string;
  status: string;
  fid: string | null;
  server: string;
  registered_at: number | null;
};

function R5Gate() {
  return (
    <div className="page-stack">
      <PageHeader title="Farm" />
      <section className="panel">
        <div className="flex items-start gap-4">
          <span
            className="rounded-full border border-amber-400/40 bg-amber-500/15 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-amber-300"
            aria-hidden
          >
            R5
          </span>
          <div className="min-w-0">
            <h2 className="m-0 text-base font-semibold text-wos-text">
              Farm is an owner-only (R5) feature
            </h2>
            <p className="muted mt-1">
              Generating beta farm accounts and confirming their registration is
              gated behind the R5 owner tier and is still in development.
            </p>
            <div className="mt-3">
              <Link href="/license" className="btn-primary">
                Open License
              </Link>
            </div>
          </div>
        </div>
      </section>
    </div>
  );
}

export default function FarmPage() {
  const [tier, setTier] = useState<string | null | undefined>(undefined);
  useEffect(() => {
    let cancelled = false;
    const pull = () => {
      fetchLicenseStatus()
        .then((st) => {
          if (!cancelled) setTier(st.active && st.tier ? st.tier : null);
        })
        .catch(() => {
          if (!cancelled) setTier(null);
        });
    };
    pull();
    window.addEventListener("wos:license:updated", pull);
    return () => {
      cancelled = true;
      window.removeEventListener("wos:license:updated", pull);
    };
  }, []);
  if (tier === undefined) return <PageLoading />;
  if (tier !== "r5") return <R5Gate />;
  return <FarmInner />;
}

function FarmInner() {
  const [pending, setPending] = useState<Pending>(null);
  const [accounts, setAccounts] = useState<FarmAccount[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const refreshAccounts = useCallback(() => {
    fetch("/api/farm/accounts")
      .then((r) => r.json())
      .then((d) => setAccounts(d.accounts ?? []))
      .catch(() => {});
  }, []);

  // Poll the handoff so the Done/Failed buttons appear the moment the
  // registration process fills the form and parks at the captcha.
  useEffect(() => {
    let cancelled = false;
    const poll = () => {
      fetch("/api/farm/registration/pending")
        .then((r) => r.json())
        .then((d) => {
          if (!cancelled) setPending(d.pending ?? null);
        })
        .catch(() => {});
    };
    poll();
    refreshAccounts();
    const t = setInterval(poll, 2000);
    return () => {
      cancelled = true;
      clearInterval(t);
    };
  }, [refreshAccounts]);

  const sendVerdict = async (outcome: "done" | "failed") => {
    if (!pending) return;
    setBusy(true);
    setError(null);
    try {
      const res = await fetch("/api/farm/registration/done", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username: pending.username, outcome }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setPending(null);
      setTimeout(refreshAccounts, 800);
    } catch (e) {
      setError(e instanceof Error ? e.message : "request failed");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="page-stack">
      <PageHeader title="Farm" />
      {error && <ErrorBanner message={error} />}

      <section className="panel">
        <h2 className="m-0 text-base font-semibold text-wos-text">Registration</h2>
        {pending ? (
          <div className="mt-3">
            <p className="muted m-0">
              Account <strong className="text-wos-text">{pending.username}</strong>{" "}
              is filled in the browser. Solve the image-code + slider captcha and
              click <em>Sign Up</em>, then confirm here:
            </p>
            <div className="mt-3 flex gap-2">
              <button
                type="button"
                className="btn-primary"
                disabled={busy}
                onClick={() => sendVerdict("done")}
              >
                Done
              </button>
              <button
                type="button"
                className="btn-secondary"
                disabled={busy}
                onClick={() => sendVerdict("failed")}
              >
                Failed
              </button>
            </div>
          </div>
        ) : (
          <p className="muted mt-3 mb-0">
            No registration waiting. Start one on the host:{" "}
            <code>uv run python -m games.wos.farm.register --ui</code>
          </p>
        )}
      </section>

      <section className="panel">
        <h2 className="m-0 text-base font-semibold text-wos-text">
          Accounts ({accounts.length})
        </h2>
        {accounts.length === 0 ? (
          <p className="muted mt-3 mb-0">No farm accounts yet.</p>
        ) : (
          <table className="mt-3 w-full text-sm">
            <thead>
              <tr className="muted text-left">
                <th className="py-1">Username</th>
                <th className="py-1">Status</th>
                <th className="py-1">FID</th>
                <th className="py-1">Server</th>
              </tr>
            </thead>
            <tbody>
              {accounts.map((a) => (
                <tr key={a.username} className="border-t border-wos-hairline">
                  <td className="py-1 text-wos-text">{a.username}</td>
                  <td className="py-1">{a.status}</td>
                  <td className="py-1">{a.fid ?? "—"}</td>
                  <td className="py-1">{a.server}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </div>
  );
}
