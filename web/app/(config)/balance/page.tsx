"use client";

import { useCallback, useEffect, useState } from "react";
import { PageHeader } from "@/components/PageHeader";
import { fetchBalanceFile, fetchBalanceFiles } from "@/lib/api";
import type { BalanceFileMeta } from "@/lib/config-pages";

export default function BalancePage() {
  const [files, setFiles] = useState<BalanceFileMeta[]>([]);
  const [active, setActive] = useState("defaults");
  const [yamlText, setYamlText] = useState("");
  const [path, setPath] = useState("");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchBalanceFiles()
      .then(setFiles)
      .catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, []);

  const load = useCallback(async (fileId: string) => {
    setError(null);
    try {
      const data = await fetchBalanceFile(fileId);
      setPath(data.path);
      setYamlText(JSON.stringify(data.content, null, 2));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  useEffect(() => {
    if (active) load(active);
  }, [active, load]);

  return (
    <>
      <PageHeader title="Balance"><p className="muted">Read-only view of optimizer balance YAML (edit in repo).</p></PageHeader>
      <div className="toolbar">
        {files.map((f) => (
          <button
            key={f.id}
            type="button"
            className={active === f.id ? "btn-primary" : "btn-secondary"}
            onClick={() => setActive(f.id)}
          >
            {f.filename}
          </button>
        ))}
      </div>
      {error && <p className="error-banner">{error}</p>}
      {path && <p className="muted">{path}</p>}
      <section className="panel">
        <pre className="code-block">{yamlText}</pre>
      </section>
    </>
  );
}
