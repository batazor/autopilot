"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import { PageHeader } from "@/components/PageHeader";
import { CopyButton } from "@/components/CopyButton";
import { Icon } from "@/components/ui/Icon";
import {
  fetchLicenseFingerprint,
  fetchLicenseStatus,
  importLicenseFile,
} from "@/lib/api";
import type {
  LicenseFingerprint,
  LicenseStatus,
} from "@/lib/config-pages";

const STATE_LABELS: Record<LicenseStatus["state"], string> = {
  active: "Active",
  missing: "Missing",
  expired: "Expired",
  invalid: "Invalid",
  machine_mismatch: "Machine mismatch",
};

const STATE_PILL: Record<LicenseStatus["state"], string> = {
  active: "status-pill status-pill--lg status-idle",
  missing: "status-pill status-pill--lg status-pending",
  expired: "status-pill status-pill--lg status-pending",
  invalid: "status-pill status-pill--lg status-pending",
  machine_mismatch: "status-pill status-pill--lg status-pending",
};

const STATE_HELP: Record<LicenseStatus["state"], string> = {
  active: "Your bot worker is allowed to run on this host.",
  missing: "No license file found. Send your fingerprint to the maintainer to get one.",
  expired: "This license has expired. Request a renewal from the maintainer.",
  invalid: "This license file is invalid or cannot be parsed.",
  machine_mismatch: "This license was issued for a different machine. Re-send your fingerprint to get a new one.",
};

function formatDate(iso: string | null): string {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
}

export default function LicensePage() {
  const [fingerprint, setFingerprint] = useState<LicenseFingerprint | null>(null);
  const [status, setStatus] = useState<LicenseStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [importing, setImporting] = useState(false);
  const [dragOver, setDragOver] = useState(false);
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      const [fp, st] = await Promise.all([
        fetchLicenseFingerprint(),
        fetchLicenseStatus(),
      ]);
      setFingerprint(fp);
      setStatus(st);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const handleFile = async (file: File) => {
    setError(null);
    setSuccess(null);
    setImporting(true);
    try {
      const result = await importLicenseFile(file);
      setStatus(result.status);
      setSuccess("License imported. You can start the bot now.");
      window.dispatchEvent(new Event("wos:license:updated"));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setImporting(false);
    }
  };

  const onFileSelected = (ev: React.ChangeEvent<HTMLInputElement>) => {
    const file = ev.target.files?.[0];
    ev.target.value = "";
    if (file) void handleFile(file);
  };

  const onDrop = (ev: React.DragEvent<HTMLDivElement>) => {
    ev.preventDefault();
    setDragOver(false);
    const file = ev.dataTransfer.files?.[0];
    if (file) void handleFile(file);
  };

  const daysLeft = status?.days_left;
  const expiringSoon =
    status?.active && typeof daysLeft === "number" && daysLeft < 7;

  return (
    <>
      <PageHeader title="License">
        <p className="muted m-0">
          The bot worker refuses to start without a valid license bound to this
          host. Send your machine fingerprint to the maintainer, then import
          the <code>.licence.jwt</code> file they send back.
        </p>
      </PageHeader>

      {error ? (
        <div className="error-banner" role="alert">
          {error}
        </div>
      ) : null}
      {success ? (
        <div className="success-banner" role="status">
          {success}
        </div>
      ) : null}

      {/* Hero status */}
      <section className="panel mb-4">
        {status === null ? (
          <p className="muted m-0">Loading status…</p>
        ) : (
          <div className="flex flex-col gap-4 md:flex-row md:items-center md:justify-between">
            <div className="flex items-start gap-3">
              <div
                className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-full ${
                  status.active
                    ? "bg-emerald-500/15 text-emerald-300"
                    : "bg-amber-500/15 text-amber-300"
                }`}
                aria-hidden
              >
                <Icon
                  name={status.active ? "check" : "alert"}
                  size="md"
                />
              </div>
              <div className="min-w-0">
                <div className="flex flex-wrap items-center gap-2">
                  <span className={STATE_PILL[status.state]}>
                    <span className="status-pill__dot" aria-hidden />
                    {STATE_LABELS[status.state]}
                  </span>
                  {status.tier ? (
                    <span className="status-pill status-pill--lg status-idle">
                      {status.tier}
                    </span>
                  ) : null}
                  {expiringSoon ? (
                    <span className="status-pill status-pill--lg status-pending">
                      Expires soon
                    </span>
                  ) : null}
                </div>
                <p className="muted mt-1 mb-0">
                  {status.reason ?? STATE_HELP[status.state]}
                </p>
              </div>
            </div>
            <dl className="grid grid-cols-2 gap-x-6 gap-y-2 text-sm md:text-right">
              <div className="md:col-span-2">
                <dt className="muted text-xs uppercase tracking-wide">User</dt>
                <dd className="m-0 text-wos-text">{status.sub ?? "—"}</dd>
              </div>
              <div>
                <dt className="muted text-xs uppercase tracking-wide">Expires</dt>
                <dd className="m-0 text-wos-text">{formatDate(status.expires_at)}</dd>
              </div>
              <div>
                <dt className="muted text-xs uppercase tracking-wide">Days left</dt>
                <dd
                  className={`m-0 font-semibold ${
                    expiringSoon ? "text-amber-300" : "text-wos-text"
                  }`}
                >
                  {typeof daysLeft === "number" ? daysLeft.toFixed(1) : "—"}
                </dd>
              </div>
            </dl>
          </div>
        )}
      </section>

      {/* Activation steps */}
      <ol className="grid gap-3 md:grid-cols-2 mb-4 list-none p-0">
        <li className="panel">
          <div className="flex items-baseline gap-2">
            <span className="text-2xl font-bold text-wos-text-muted">1</span>
            <h2 className="m-0 text-base font-semibold text-wos-text">
              Share your fingerprint
            </h2>
          </div>
          <p className="muted mt-1 mb-3">
            Copy the value below and send it to the maintainer with your email.
            They will issue a license file bound to this host.
          </p>
          {fingerprint === null ? (
            <p className="muted m-0">Loading…</p>
          ) : (
            <>
              <div className="flex flex-wrap items-center gap-2">
                <code className="flex-1 min-w-0 truncate rounded-lg border border-wos-border-subtle bg-wos-panel-raised px-3 py-2 text-base tracking-wider">
                  {fingerprint.fingerprint}
                </code>
                <CopyButton
                  text={fingerprint.fingerprint}
                  label="Copy"
                  title="Copy fingerprint"
                />
              </div>
            </>
          )}
        </li>

        <li className="panel">
          <div className="flex items-baseline gap-2">
            <span className="text-2xl font-bold text-wos-text-muted">2</span>
            <h2 className="m-0 text-base font-semibold text-wos-text">
              Import the license file
            </h2>
          </div>
          <p className="muted mt-1 mb-3">
            Drop the <code>.licence.jwt</code> the maintainer sent you, or
            pick it manually. It is saved to disk and the bot picks it up on
            next restart.
          </p>
          <input
            ref={fileInputRef}
            type="file"
            accept=".jwt,.licence,.txt,text/plain,application/jwt"
            onChange={onFileSelected}
            disabled={importing}
            className="hidden"
          />
          <div
            onDragOver={(e) => {
              e.preventDefault();
              if (!importing) setDragOver(true);
            }}
            onDragLeave={() => setDragOver(false)}
            onDrop={onDrop}
            onClick={() => !importing && fileInputRef.current?.click()}
            role="button"
            tabIndex={0}
            onKeyDown={(e) => {
              if ((e.key === "Enter" || e.key === " ") && !importing) {
                e.preventDefault();
                fileInputRef.current?.click();
              }
            }}
            className={[
              "flex flex-col items-center justify-center gap-2 rounded-xl border-2 border-dashed px-4 py-6 text-center transition cursor-pointer",
              dragOver
                ? "border-sky-400/70 bg-sky-500/10"
                : "border-wos-border-subtle bg-wos-panel-raised/50 hover:border-wos-border hover:bg-wos-panel-raised",
              importing ? "cursor-wait opacity-60" : "",
            ].join(" ")}
          >
            <svg
              className="ui-icon ui-icon--lg"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.75"
              strokeLinecap="round"
              strokeLinejoin="round"
              aria-hidden
            >
              <path d="M12 4v12" />
              <path d="M7 9l5-5 5 5" />
              <path d="M5 20h14" />
            </svg>
            <div className="text-sm font-medium text-wos-text">
              {importing
                ? "Importing…"
                : dragOver
                  ? "Drop file to import"
                  : "Drop file or click to choose"}
            </div>
            <div className="muted text-xs">
              Accepts .jwt / .licence / .txt
            </div>
          </div>
          {status?.license_file ? (
            <div className="muted mt-3 text-xs">
              Saved to <code>{status.license_file}</code>
            </div>
          ) : null}
        </li>
      </ol>

      {/* Limits / features */}
      {status?.active ? (
        <section className="panel mb-4">
          <h2>Plan details</h2>
          <dl className="grid grid-cols-1 gap-x-6 gap-y-3 text-sm sm:grid-cols-2 lg:grid-cols-3">
            <div>
              <dt className="muted text-xs uppercase tracking-wide">Limits</dt>
              <dd className="m-0 text-wos-text">
                {status.max_devices !== null &&
                status.max_players_per_device !== null
                  ? `${status.max_devices} device${
                      status.max_devices === 1 ? "" : "s"
                    } × ${status.max_players_per_device} players`
                  : "—"}
              </dd>
            </div>
            <div>
              <dt className="muted text-xs uppercase tracking-wide">Features</dt>
              <dd className="m-0 text-wos-text">
                {status.features.length > 0 ? (
                  <div className="flex flex-wrap gap-1.5">
                    {status.features.map((f) => (
                      <span
                        key={f}
                        className="rounded-full border border-wos-border-subtle bg-wos-panel-raised px-2 py-0.5 text-xs"
                      >
                        {f}
                      </span>
                    ))}
                  </div>
                ) : (
                  "—"
                )}
              </dd>
            </div>
            <div className="sm:col-span-2 lg:col-span-1">
              <dt className="muted text-xs uppercase tracking-wide">
                Machine ID
              </dt>
              <dd className="m-0">
                <code className="text-xs">{status.machine_id ?? "—"}</code>
              </dd>
            </div>
          </dl>
        </section>
      ) : null}

      {status ? <NextTierCard tier={status.tier} /> : null}

      {status?.admin_enabled ? (
        <section className="panel mt-4">
          <h2>Admin</h2>
          <p className="muted m-0">
            This instance can issue licenses — see{" "}
            <Link href="/license/admin">Admin issuer</Link>.
          </p>
        </section>
      ) : null}
    </>
  );
}

type TierFeature = {
  title: string;
  description: string;
};

const TIER_UPGRADES: Record<
  string,
  { nextTier: string; features: TierFeature[] } | null
> = {
  free: {
    nextTier: "trial",
    features: [
      {
        title: "Full bot runtime",
        description:
          "Trial unlocks running scenarios on your device, OCR, and the approvals queue.",
      },
      {
        title: "Multi-account on one device",
        description:
          "Cycle through several player profiles per emulator instead of one.",
      },
    ],
  },
  trial: {
    nextTier: "pro",
    features: [
      {
        title: "External gift-code accounts",
        description:
          "Redeem promo codes for alliance members and partner farms — accounts the bot doesn't own.",
      },
      {
        title: "Alliance statistics",
        description:
          "Daily alliance power, members, and trends pulled from per-player snapshots.",
      },
      {
        title: "Multiple devices",
        description:
          "Run more emulator instances in parallel under the same license.",
      },
    ],
  },
  pro: null,
};

function NextTierCard({ tier }: { tier: string | null }) {
  const current = (tier || "free").toLowerCase();
  const upgrade = TIER_UPGRADES[current];
  if (!upgrade) return null;
  return (
    <section className="panel mt-4">
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <h2 className="m-0 text-base font-semibold text-wos-text">
          What you unlock with{" "}
        </h2>
        <span className="rounded-full border border-amber-400/40 bg-amber-500/15 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-amber-300">
          {upgrade.nextTier}
        </span>
      </div>
      <p className="muted mt-0 mb-3">
        Your current tier is <code>{current}</code>. Upgrading to{" "}
        <code>{upgrade.nextTier}</code> adds:
      </p>
      <ul className="grid gap-3 list-none p-0 m-0 sm:grid-cols-2">
        {upgrade.features.map((f) => (
          <li
            key={f.title}
            className="rounded-lg border border-wos-border-subtle bg-wos-panel-raised/50 p-3"
          >
            <div className="flex items-start gap-2">
              <span
                className="mt-0.5 inline-flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-emerald-500/15 text-emerald-300"
                aria-hidden
              >
                <svg
                  className="ui-icon ui-icon--sm"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                >
                  <path d="M6 12l4 4 8-9" />
                </svg>
              </span>
              <div className="min-w-0">
                <div className="text-sm font-medium text-wos-text">
                  {f.title}
                </div>
                <p className="muted m-0 text-xs">{f.description}</p>
              </div>
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}
