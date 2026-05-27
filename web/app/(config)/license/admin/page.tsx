"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { PageHeader } from "@/components/PageHeader";
import { CopyButton } from "@/components/CopyButton";
import { fetchLicenseStatus, issueLicense } from "@/lib/api";
import type { LicenseIssueResult, LicenseStatus } from "@/lib/config-pages";

function slugForEmail(sub: string): string {
  return (
    sub
      .replace("@", "-at-")
      .replace(/[^A-Za-z0-9]+/g, "-")
      .replace(/^-+|-+$/g, "")
      .toLowerCase() || "user"
  );
}

function downloadEnvelope(result: LicenseIssueResult): void {
  const sub = String(result.envelope.issued_to ?? "user");
  const filename = `${slugForEmail(sub)}.licence.json`;
  const blob = new Blob([JSON.stringify(result.envelope, null, 2)], {
    type: "application/json",
  });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  // Revoke after a tick so Safari has time to read the blob.
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

const ADMIN_TOKEN_KEY = "wos.licenseAdminToken";

type FormState = {
  sub: string;
  machineId: string;
  days: number;
  tier: string;
  features: string;
  maxDevices: number;
  maxPlayersPerDevice: number;
};

const DEFAULTS: FormState = {
  sub: "",
  machineId: "",
  days: 30,
  tier: "pro",
  features: "",
  maxDevices: 1,
  maxPlayersPerDevice: 3,
};

function parseFeatures(raw: string): string[] {
  return raw
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);
}

export default function LicenseAdminPage() {
  const [status, setStatus] = useState<LicenseStatus | null>(null);
  const [adminToken, setAdminToken] = useState("");
  const [form, setForm] = useState<FormState>(DEFAULTS);
  const [result, setResult] = useState<LicenseIssueResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    try {
      const stored = window.localStorage.getItem(ADMIN_TOKEN_KEY);
      if (stored) setAdminToken(stored);
    } catch {
      /* ignore */
    }
  }, []);

  const loadStatus = useCallback(async () => {
    try {
      setStatus(await fetchLicenseStatus());
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  useEffect(() => {
    loadStatus();
  }, [loadStatus]);

  const canSubmit = useMemo(
    () =>
      !submitting &&
      adminToken.trim().length > 0 &&
      form.sub.trim().length > 0 &&
      form.machineId.trim().length > 0 &&
      form.days >= 1 &&
      form.days <= 365 &&
      form.maxDevices >= 1 &&
      form.maxPlayersPerDevice >= 1,
    [adminToken, form, submitting],
  );

  const onSubmit = async (ev: React.FormEvent) => {
    ev.preventDefault();
    setError(null);
    setResult(null);
    setSubmitting(true);
    try {
      try {
        window.localStorage.setItem(ADMIN_TOKEN_KEY, adminToken);
      } catch {
        /* ignore */
      }
      const out = await issueLicense(
        {
          sub: form.sub.trim(),
          machine_id: form.machineId.trim(),
          days: form.days,
          tier: form.tier.trim() || "pro",
          features: parseFeatures(form.features),
          max_devices: form.maxDevices,
          max_players_per_device: form.maxPlayersPerDevice,
        },
        adminToken.trim(),
      );
      setResult(out);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSubmitting(false);
    }
  };

  if (status && !status.admin_enabled) {
    return (
      <>
        <PageHeader title="License — admin issuer">
          <p className="muted">
            Issuer endpoint is not enabled on this instance.
          </p>
        </PageHeader>
        <section className="card">
          <p>
            To enable: place the private key at{" "}
            <code>.secrets/license_signer.key</code> (generate with{" "}
            <code>uv run gen-license-keypair</code>) and set{" "}
            <code>WOS_ADMIN_TOKEN</code> in the API process environment.
          </p>
        </section>
      </>
    );
  }

  return (
    <>
      <PageHeader title="License — admin issuer">
        <p className="muted">
          Mint a JWT bound to a user's machine fingerprint. Paste the resulting
          token into the user's <code>WOS_LICENSE</code> env variable.
        </p>
      </PageHeader>

      {error ? (
        <div className="error-banner" role="alert" style={{ marginBottom: 16 }}>
          {error}
        </div>
      ) : null}

      <section className="card" style={{ marginBottom: 16 }}>
        <h2>Admin token</h2>
        <p className="muted">
          Must match <code>WOS_ADMIN_TOKEN</code> on the API server. Stored
          locally in your browser, never sent except to <code>/api/license/issue</code>.
        </p>
        <input
          type="password"
          value={adminToken}
          onChange={(e) => setAdminToken(e.target.value)}
          placeholder="admin token"
          style={{ width: "100%", marginTop: 8 }}
        />
      </section>

      <section className="card" style={{ marginBottom: 16 }}>
        <h2>Issue license</h2>
        <form onSubmit={onSubmit} className="grid-form">
          <label>
            <span>User email / id</span>
            <input
              type="email"
              required
              value={form.sub}
              onChange={(e) => setForm({ ...form, sub: e.target.value })}
              placeholder="alice@example.com"
            />
          </label>

          <label>
            <span>Machine fingerprint</span>
            <input
              type="text"
              required
              value={form.machineId}
              onChange={(e) => setForm({ ...form, machineId: e.target.value })}
              placeholder="ABCD-EFGH-IJKL-MNOP"
              style={{ fontFamily: "monospace" }}
            />
          </label>

          <label>
            <span>Days (1-365)</span>
            <input
              type="number"
              min={1}
              max={365}
              value={form.days}
              onChange={(e) =>
                setForm({ ...form, days: Number(e.target.value) })
              }
            />
          </label>

          <label>
            <span>Tier</span>
            <input
              type="text"
              value={form.tier}
              onChange={(e) => setForm({ ...form, tier: e.target.value })}
              placeholder="pro"
            />
          </label>

          <label>
            <span>Features (comma-separated)</span>
            <input
              type="text"
              value={form.features}
              onChange={(e) => setForm({ ...form, features: e.target.value })}
              placeholder="heroes, mail, alliance"
            />
          </label>

          <label>
            <span>Max devices</span>
            <input
              type="number"
              min={1}
              max={100}
              value={form.maxDevices}
              onChange={(e) =>
                setForm({ ...form, maxDevices: Number(e.target.value) })
              }
            />
          </label>

          <label>
            <span>Max players per device</span>
            <input
              type="number"
              min={1}
              max={100}
              value={form.maxPlayersPerDevice}
              onChange={(e) =>
                setForm({
                  ...form,
                  maxPlayersPerDevice: Number(e.target.value),
                })
              }
            />
          </label>

          <div style={{ gridColumn: "1 / -1", marginTop: 12 }}>
            <button type="submit" className="btn-primary" disabled={!canSubmit}>
              {submitting ? "Issuing…" : "Issue license"}
            </button>
          </div>
        </form>
      </section>

      {result ? (
        <section className="card">
          <h2>Issued license</h2>
          <p className="muted">
            Download the <code>.licence.json</code> file and send it to the
            user. They import it via <code>/license</code> in their UI (or drop
            it into <code>license-data/licence.json</code> manually).
          </p>
          <div style={{ display: "flex", gap: 8, marginTop: 12, flexWrap: "wrap" }}>
            <button
              type="button"
              className="btn-primary"
              onClick={() => downloadEnvelope(result)}
            >
              Download .licence.json
            </button>
            <CopyButton text={result.token} label="Copy raw token" />
            <CopyButton
              text={`WOS_LICENSE=${result.token}`}
              label="Copy .env line"
            />
          </div>
          <details style={{ marginTop: 16 }}>
            <summary className="muted">Envelope contents</summary>
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
              {JSON.stringify(result.envelope, null, 2)}
            </pre>
          </details>
        </section>
      ) : null}
    </>
  );
}
