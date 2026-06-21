"use client";

import { useEffect, useMemo, useState } from "react";
import { Button, Chip } from "@/components/ui";
import {
  autofillBuildingLevels,
  fetchBuildingCatalog,
  type BuildingCatalogEntry,
} from "@/lib/api";

type Props = {
  /** JSON string: { building_id: level_key }. */
  value: string;
  onChange: (json: string) => void;
};

function parse(value: string): Record<string, string> {
  try {
    const o = JSON.parse(value);
    if (o && typeof o === "object" && !Array.isArray(o)) {
      const out: Record<string, string> = {};
      for (const [k, v] of Object.entries(o)) out[k] = String(v);
      return out;
    }
  } catch {
    /* ignore — treat as empty */
  }
  return {};
}

/**
 * Building-levels editor: one level dropdown per building, plus an "Auto-fill"
 * button that backfills the prerequisite closure implied by what's set (set
 * Furnace 30 → Embassy 29, camps, Research Center, … appear automatically).
 */
export function BuildingLevelsEditor({ value, onChange }: Props) {
  const [catalog, setCatalog] = useState<BuildingCatalogEntry[]>([]);
  const [filter, setFilter] = useState("");
  const [busy, setBusy] = useState(false);
  const [added, setAdded] = useState<string[] | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    fetchBuildingCatalog()
      .then(setCatalog)
      .catch((e) => setErr(e instanceof Error ? e.message : String(e)));
  }, []);

  const levels = useMemo(() => parse(value), [value]);

  const commit = (next: Record<string, string>) => {
    const clean: Record<string, string> = {};
    for (const [k, v] of Object.entries(next)) {
      if (v && v !== "0") clean[k] = v;
    }
    onChange(JSON.stringify(clean, null, 2));
  };

  const setLevel = (id: string, lvl: string) => {
    setAdded(null);
    commit({ ...levels, [id]: lvl });
  };

  const autofill = async () => {
    setErr(null);
    setBusy(true);
    try {
      const res = await autofillBuildingLevels(levels);
      setAdded(res.added);
      commit(res.levels);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const setCount = Object.keys(levels).filter((k) => levels[k] && levels[k] !== "0").length;
  const shown = catalog.filter((b) =>
    `${b.name} ${b.id}`.toLowerCase().includes(filter.toLowerCase().trim()),
  );
  // Buildings with a level set float to the top, then alphabetical.
  shown.sort((a, b) => {
    const sa = levels[a.id] ? 0 : 1;
    const sb = levels[b.id] ? 0 : 1;
    return sa - sb || a.name.localeCompare(b.name);
  });

  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap items-center gap-2">
        <Button type="button" variant="secondary" pending={busy} onClick={autofill}>
          Auto-fill prerequisites
        </Button>
        <Button
          type="button"
          variant="secondary"
          onClick={() => {
            setAdded(null);
            commit({});
          }}
        >
          Clear
        </Button>
        <Chip>{setCount} set</Chip>
        {added && added.length > 0 ? (
          <Chip title={added.join(", ")}>+{added.length} backfilled</Chip>
        ) : null}
      </div>

      {err ? <p className="error-banner">{err}</p> : null}

      <input
        className="field"
        placeholder="Filter buildings…"
        value={filter}
        onChange={(e) => setFilter(e.target.value)}
      />

      <div className="max-h-72 overflow-y-auto rounded-lg border border-wos-border-subtle">
        {shown.map((b) => {
          const cur = levels[b.id] ?? "";
          const isSet = cur && cur !== "0";
          return (
            <div
              key={b.id}
              className="flex items-center justify-between gap-2 border-b border-wos-border-subtle px-3 py-1.5 last:border-b-0"
            >
              <span
                className={`truncate text-sm ${isSet ? "font-medium text-wos-text" : "text-wos-text-muted"}`}
                title={b.id}
              >
                {b.name}
              </span>
              <select
                className="field w-28 shrink-0"
                value={cur}
                onChange={(e) => setLevel(b.id, e.target.value)}
              >
                <option value="">—</option>
                {b.levels.map((lv) => (
                  <option key={lv} value={lv}>
                    {lv}
                  </option>
                ))}
              </select>
            </div>
          );
        })}
        {shown.length === 0 ? (
          <p className="px-3 py-2 text-sm text-wos-text-muted">
            {catalog.length === 0 ? "Loading catalog…" : "No buildings match."}
          </p>
        ) : null}
      </div>
    </div>
  );
}
