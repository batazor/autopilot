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
  device_serial: string | null;
  registered_at: number | null;
};

const STATUSES = ["pending", "registered", "bound", "failed"] as const;

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

  // generate form
  const [count, setCount] = useState(1);
  const [seed, setSeed] = useState("");
  // table state
  const [filter, setFilter] = useState<string>("all");
  const [secrets, setSecrets] = useState<Record<string, string>>({});
  const [fidEdits, setFidEdits] = useState<Record<string, string>>({});
  const [bindEdits, setBindEdits] = useState<Record<string, string>>({});

  const refreshAccounts = useCallback(() => {
    fetch("/api/farm/accounts")
      .then((r) => r.json())
      .then((d) => setAccounts(d.accounts ?? []))
      .catch(() => {});
  }, []);

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

  const post = async (url: string, body?: unknown) => {
    setError(null);
    const res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return res.json();
  };

  const guard = async (fn: () => Promise<void>) => {
    setBusy(true);
    try {
      await fn();
    } catch (e) {
      setError(e instanceof Error ? e.message : "request failed");
    } finally {
      setBusy(false);
    }
  };

  const generate = () =>
    guard(async () => {
      await post("/api/farm/generate", {
        count,
        seed: seed.trim() || null,
      });
      setSeed("");
      refreshAccounts();
    });

  const sendVerdict = (outcome: "done" | "failed") =>
    guard(async () => {
      if (!pending) return;
      await post("/api/farm/registration/done", {
        username: pending.username,
        outcome,
      });
      setPending(null);
      setTimeout(refreshAccounts, 800);
    });

  const reveal = (username: string) =>
    guard(async () => {
      const d = await fetch(`/api/farm/accounts/${username}/secret`).then((r) =>
        r.json(),
      );
      setSecrets((s) => ({ ...s, [username]: d.password }));
    });

  const saveFid = (username: string) =>
    guard(async () => {
      await post(`/api/farm/accounts/${username}/fid`, {
        fid: fidEdits[username] ?? "",
      });
      refreshAccounts();
    });

  const bind = (username: string) =>
    guard(async () => {
      await post(`/api/farm/accounts/${username}/bind`, {
        device_serial: bindEdits[username] ?? "",
      });
      refreshAccounts();
    });

  const remove = (username: string) =>
    guard(async () => {
      await fetch(`/api/farm/accounts/${username}`, { method: "DELETE" });
      refreshAccounts();
    });

  const counts = STATUSES.map((s) => ({
    status: s,
    n: accounts.filter((a) => a.status === s).length,
  }));
  const shown =
    filter === "all" ? accounts : accounts.filter((a) => a.status === filter);

  return (
    <div className="page-stack">
      <PageHeader title="Farm" />
      {error && <ErrorBanner message={error} />}

      {/* Generate */}
      <section className="panel">
        <h2 className="m-0 text-base font-semibold text-wos-text">Generate</h2>
        <div className="mt-3 flex flex-wrap items-end gap-3">
          <label className="text-sm">
            <span className="muted block">Count</span>
            <input
              type="number"
              min={1}
              max={50}
              value={count}
              onChange={(e) => setCount(Math.max(1, Number(e.target.value) || 1))}
              className="mt-1 w-20 rounded border border-wos-hairline bg-transparent px-2 py-1"
            />
          </label>
          <label className="text-sm">
            <span className="muted block">Seed (optional)</span>
            <input
              type="text"
              value={seed}
              placeholder="reproducible batch"
              onChange={(e) => setSeed(e.target.value)}
              className="mt-1 w-48 rounded border border-wos-hairline bg-transparent px-2 py-1"
            />
          </label>
          <button type="button" className="btn-primary" disabled={busy} onClick={generate}>
            Generate {count}
          </button>
        </div>
      </section>

      {/* Registration handoff */}
      <section className="panel">
        <h2 className="m-0 text-base font-semibold text-wos-text">Registration</h2>
        {pending ? (
          <div className="mt-3">
            <p className="muted m-0">
              Account <strong className="text-wos-text">{pending.username}</strong>{" "}
              is filled in the browser. Solve the image-code + slider captcha and
              click <em>Sign Up</em>, then confirm:
            </p>
            <div className="mt-3 flex gap-2">
              <button type="button" className="btn-primary" disabled={busy} onClick={() => sendVerdict("done")}>
                Done
              </button>
              <button type="button" className="btn-secondary" disabled={busy} onClick={() => sendVerdict("failed")}>
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

      {/* Accounts */}
      <section className="panel">
        <div className="flex flex-wrap items-center gap-2">
          <h2 className="m-0 text-base font-semibold text-wos-text">
            Accounts ({accounts.length})
          </h2>
          <div className="ml-auto flex flex-wrap gap-1 text-xs">
            <FilterChip label="all" active={filter === "all"} onClick={() => setFilter("all")} />
            {counts.map((c) => (
              <FilterChip
                key={c.status}
                label={`${c.status} ${c.n}`}
                active={filter === c.status}
                onClick={() => setFilter(c.status)}
              />
            ))}
          </div>
        </div>

        {shown.length === 0 ? (
          <p className="muted mt-3 mb-0">No accounts for this filter.</p>
        ) : (
          <table className="mt-3 w-full text-sm">
            <thead>
              <tr className="muted text-left">
                <th className="py-1">Username</th>
                <th className="py-1">Password</th>
                <th className="py-1">Status</th>
                <th className="py-1">FID</th>
                <th className="py-1">Device</th>
                <th className="py-1" />
              </tr>
            </thead>
            <tbody>
              {shown.map((a) => (
                <tr key={a.username} className="border-t border-wos-hairline align-top">
                  <td className="py-1 text-wos-text">{a.username}</td>
                  <td className="py-1">
                    {secrets[a.username] ? (
                      <span className="flex items-center gap-1">
                        <code>{secrets[a.username]}</code>
                        <button
                          type="button"
                          className="btn-secondary px-1 py-0 text-xs"
                          onClick={() => navigator.clipboard?.writeText(secrets[a.username])}
                        >
                          Copy
                        </button>
                      </span>
                    ) : (
                      <button
                        type="button"
                        className="btn-secondary px-1 py-0 text-xs"
                        disabled={busy}
                        onClick={() => reveal(a.username)}
                      >
                        Reveal
                      </button>
                    )}
                  </td>
                  <td className="py-1">{a.status}</td>
                  <td className="py-1">
                    <span className="flex items-center gap-1">
                      <input
                        type="text"
                        defaultValue={a.fid ?? ""}
                        placeholder="—"
                        onChange={(e) =>
                          setFidEdits((m) => ({ ...m, [a.username]: e.target.value }))
                        }
                        className="w-24 rounded border border-wos-hairline bg-transparent px-1 py-0.5"
                      />
                      <button
                        type="button"
                        className="btn-secondary px-1 py-0 text-xs"
                        disabled={busy}
                        onClick={() => saveFid(a.username)}
                      >
                        Save
                      </button>
                    </span>
                  </td>
                  <td className="py-1">
                    {a.device_serial ? (
                      <span className="text-wos-text">{a.device_serial}</span>
                    ) : (
                      <span className="flex items-center gap-1">
                        <input
                          type="text"
                          placeholder="serial"
                          onChange={(e) =>
                            setBindEdits((m) => ({ ...m, [a.username]: e.target.value }))
                          }
                          className="w-28 rounded border border-wos-hairline bg-transparent px-1 py-0.5"
                        />
                        <button
                          type="button"
                          className="btn-secondary px-1 py-0 text-xs"
                          disabled={busy}
                          onClick={() => bind(a.username)}
                        >
                          Bind
                        </button>
                      </span>
                    )}
                  </td>
                  <td className="py-1 text-right">
                    <button
                      type="button"
                      className="btn-secondary px-1 py-0 text-xs"
                      disabled={busy}
                      onClick={() => remove(a.username)}
                    >
                      Delete
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </div>
  );
}

function FilterChip({
  label,
  active,
  onClick,
}: {
  label: string;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`rounded-full border px-2 py-0.5 ${
        active
          ? "border-wos-text/40 bg-wos-surface text-wos-text"
          : "border-wos-hairline muted"
      }`}
    >
      {label}
    </button>
  );
}
