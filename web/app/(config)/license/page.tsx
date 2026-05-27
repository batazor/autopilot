"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { PageHeader } from "@/components/PageHeader";
import { CopyButton } from "@/components/CopyButton";
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

const STATE_CLASS: Record<LicenseStatus["state"], string> = {
  active: "success-text",
  missing: "muted",
  expired: "error-text",
  invalid: "error-text",
  machine_mismatch: "error-text",
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

  const onFileSelected = async (
    ev: React.ChangeEvent<HTMLInputElement>,
  ) => {
    const file = ev.target.files?.[0];
    // Always clear so the same file can be picked again after an error.
    ev.target.value = "";
    if (!file) return;
    setError(null);
    setSuccess(null);
    setImporting(true);
    try {
      const result = await importLicenseFile(file);
      setStatus(result.status);
      setSuccess(
        `License imported and saved to ${result.license_file}. ` +
          `Restart the bot to apply.`,
      );
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setImporting(false);
    }
  };

  const daysLeft = status?.days_left;
  const expiringSoon =
    status?.active && typeof daysLeft === "number" && daysLeft < 7;

  return (
    <>
      <PageHeader title="License">
        <p className="muted">
          The bot worker refuses to start without a valid license bound to this
          host. Send your fingerprint below to the maintainer, then import the
          <code> .licence.json</code> file they send back.
        </p>
      </PageHeader>

      {error ? (
        <div className="error-banner" role="alert" style={{ marginBottom: 16 }}>
          {error}
        </div>
      ) : null}
      {success ? (
        <div
          className="success-banner"
          role="status"
          style={{
            marginBottom: 16,
            padding: 12,
            border: "1px solid rgba(0, 200, 0, 0.4)",
            borderRadius: 4,
          }}
        >
          {success}
        </div>
      ) : null}

      <section className="card" style={{ marginBottom: 16 }}>
        <h2>Status</h2>
        {status === null ? (
          <p className="muted">Loading…</p>
        ) : (
          <div className="grid-2col">
            <div>
              <div className="muted">State</div>
              <div className={STATE_CLASS[status.state]}>
                <strong>{STATE_LABELS[status.state]}</strong>
              </div>
            </div>
            <div>
              <div className="muted">User</div>
              <div>{status.sub ?? "—"}</div>
            </div>
            <div>
              <div className="muted">Tier</div>
              <div>
                {status.tier ?? "—"}
                {status.tier === "trial" ? (
                  <span
                    style={{
                      marginLeft: 8,
                      padding: "1px 6px",
                      borderRadius: 4,
                      fontSize: 11,
                      background: "var(--wos-status-warn-bg)",
                      color: "var(--wos-status-warn-fg)",
                    }}
                  >
                    TRIAL
                  </span>
                ) : null}
              </div>
            </div>
            <div>
              <div className="muted">Limits</div>
              <div>
                {status.max_devices !== null && status.max_players_per_device !== null
                  ? `${status.max_devices} device${status.max_devices === 1 ? "" : "s"} × ${status.max_players_per_device} players`
                  : "—"}
              </div>
            </div>
            <div>
              <div className="muted">Features</div>
              <div>
                {status.features.length > 0
                  ? status.features.join(", ")
                  : "—"}
              </div>
            </div>
            <div>
              <div className="muted">Expires</div>
              <div>{formatDate(status.expires_at)}</div>
            </div>
            <div>
              <div className="muted">Days left</div>
              <div className={expiringSoon ? "error-text" : ""}>
                {typeof daysLeft === "number" ? daysLeft.toFixed(1) : "—"}
              </div>
            </div>
            <div style={{ gridColumn: "1 / -1" }}>
              <div className="muted">License file path</div>
              <code style={{ fontSize: 12 }}>{status.license_file}</code>
            </div>
          </div>
        )}
        {status && !status.active ? (
          <p className="muted" style={{ marginTop: 12 }}>
            {status.reason ?? "License is not active."}
          </p>
        ) : null}
        {expiringSoon ? (
          <p className="error-text" style={{ marginTop: 12 }}>
            License expires soon — request a renewal from the maintainer.
          </p>
        ) : null}
      </section>

      <section className="card" style={{ marginBottom: 16 }}>
        <h2>Import license file</h2>
        <p className="muted">
          Select the <code>.licence.json</code> file the maintainer sent
          you. It will be verified against this host and saved to the path
          shown above. The bot picks it up on next restart.
        </p>
        <input
          ref={fileInputRef}
          type="file"
          accept=".json,application/json,.licence"
          onChange={onFileSelected}
          disabled={importing}
          style={{ display: "none" }}
        />
        <button
          type="button"
          className="btn-primary"
          onClick={() => fileInputRef.current?.click()}
          disabled={importing}
          style={{ marginTop: 8 }}
        >
          {importing ? "Importing…" : "Choose file…"}
        </button>
      </section>

      <section className="card">
        <h2>Machine fingerprint</h2>
        <p className="muted">
          Copy this value and send it to the maintainer along with your email.
          They will issue a license file bound to this host.
        </p>
        {fingerprint === null ? (
          <p className="muted">Loading…</p>
        ) : (
          <>
            <div
              style={{
                display: "flex",
                alignItems: "center",
                gap: 12,
                marginTop: 12,
              }}
            >
              <code
                style={{
                  padding: "6px 12px",
                  background: "var(--wos-bg-elevated, #1c1c1c)",
                  borderRadius: 4,
                  fontSize: 16,
                  letterSpacing: 1,
                }}
              >
                {fingerprint.fingerprint}
              </code>
              <CopyButton text={fingerprint.fingerprint} label="Copy fingerprint" />
            </div>
            <details style={{ marginTop: 16 }}>
              <summary className="muted">Components (for support)</summary>
              <pre
                style={{
                  fontSize: 12,
                  marginTop: 8,
                  padding: 12,
                  background: "var(--wos-bg-elevated, #1c1c1c)",
                  borderRadius: 4,
                  overflow: "auto",
                }}
              >
                {JSON.stringify(fingerprint.components, null, 2)}
              </pre>
            </details>
          </>
        )}
      </section>

      {status?.admin_enabled ? (
        <section className="card" style={{ marginTop: 16 }}>
          <h2>Admin</h2>
          <p className="muted">
            This instance can issue licenses — see{" "}
            <a href="/license/admin">Admin issuer</a>.
          </p>
        </section>
      ) : null}
    </>
  );
}
