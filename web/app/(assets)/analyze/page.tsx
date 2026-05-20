"use client";

import { useCallback, useEffect, useState } from "react";
import { AppListbox } from "@/components/headless";
import { PageHeader } from "@/components/PageHeader";
import { fetchAnalyzeAudit, fetchWikiScopes } from "@/lib/api";
import type { AnalyzeIssue } from "@/lib/config-pages";
import type { WikiScope } from "@/lib/wiki";

const SEV_CLASS: Record<string, string> = {
  error: "pill-danger",
  warning: "pill-paused",
  info: "pill-live",
};

export default function AnalyzePage() {
  const [scopes, setScopes] = useState<WikiScope[]>([]);
  const [scope, setScope] = useState("all");
  const [issues, setIssues] = useState<AnalyzeIssue[]>([]);
  const [manifestCount, setManifestCount] = useState(0);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      const data = await fetchAnalyzeAudit(scope);
      setIssues(data.issues);
      setManifestCount(data.manifest_count);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [scope]);

  useEffect(() => {
    fetchWikiScopes().then(setScopes).catch(() => {});
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <>
      <PageHeader title="Analyze"><p className="muted">Overlay rule audit against area.json regions.</p></PageHeader>
      <div className="toolbar">
        <AppListbox
          inline
          label="Scope"
          value={scope}
          onChange={setScope}
          options={scopes.map((s) => ({ value: s.key, label: s.label }))}
          minWidth={160}
        />
        <button type="button" className="btn-secondary" onClick={load}>
          Re-audit
        </button>
      </div>
      {error && <p className="error-banner">{error}</p>}
      <p className="muted">
        {manifestCount} manifests · {issues.length} issues
      </p>
      <section className="panel">
        <div className="data-table-wrap">
          <table className="data-table">
            <thead>
              <tr>
                <th>Severity</th>
                <th>Manifest</th>
                <th>Rule</th>
                <th>Source</th>
                <th>Message</th>
              </tr>
            </thead>
            <tbody>
              {issues.map((row, i) => (
                <tr key={`${row.manifest}-${row.rule}-${i}`}>
                  <td>
                    <span className={`pill ${SEV_CLASS[row.severity] ?? ""}`}>
                      {row.severity}
                    </span>
                  </td>
                  <td className="muted">{row.manifest}</td>
                  <td>
                    <code>{row.rule}</code>
                  </td>
                  <td>{row.source}</td>
                  <td>{row.message}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </>
  );
}
