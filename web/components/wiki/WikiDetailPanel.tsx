"use client";

import type { WikiDetail } from "@/lib/wiki";

function WikiLink({ url }: { url: string }) {
  if (!url) return null;
  return (
    <p className="meta">
      <a href={url} target="_blank" rel="noreferrer">
        Wiki page →
      </a>
    </p>
  );
}

const DEFAULT_OMIT = ["levels", "stats", "skills"];

function KeyValueTable({
  data,
  omit = DEFAULT_OMIT,
}: {
  data: Record<string, unknown>;
  omit?: string[];
}) {
  const rows = Object.entries(data).filter(
    ([k]) => !k.startsWith("_") && !omit.includes(k),
  );
  if (!rows.length) return null;
  return (
    <table className="data-table">
      <tbody>
        {rows.map(([k, v]) => (
          <tr key={k}>
            <td>
              <code>{k}</code>
            </td>
            <td style={{ wordBreak: "break-word" }}>
              {typeof v === "object" ? JSON.stringify(v) : String(v ?? "—")}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function BuildingRequirements({ body }: { body: Record<string, unknown> }) {
  const req = body.requirements_by_level;
  if (!req || typeof req !== "object") return null;
  const rows: Array<Record<string, string | number>> = [];
  for (const [lvl, row] of Object.entries(req as Record<string, unknown>)) {
    if (typeof row !== "object" || !row) continue;
    const r = row as Record<string, unknown>;
    const cost = Array.isArray(r.build_cost)
      ? (r.build_cost as Array<Record<string, unknown>>)
          .map((c) => `${c.item}:${c.amount}`)
          .join(", ")
      : "";
    rows.push({
      level: Number(lvl),
      prerequisites: String(r.prerequisites ?? ""),
      build_cost: cost,
      construction_time: String(r.construction_time ?? ""),
      building_power: String(r.building_power ?? ""),
    });
  }
  if (!rows.length) return null;
  rows.sort((a, b) => Number(a.level) - Number(b.level));
  return (
    <>
      <h3>Requirements</h3>
      <div className="data-table-wrap">
        <table className="data-table">
          <thead>
            <tr>
              <th>Lvl</th>
              <th>Prerequisites</th>
              <th>Cost</th>
              <th>Time</th>
              <th>Power</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={String(r.level)}>
                <td>{r.level}</td>
                <td>{r.prerequisites}</td>
                <td>{r.build_cost}</td>
                <td>{r.construction_time}</td>
                <td>{r.building_power}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}

function HeroLevels({ body }: { body: Record<string, unknown> }) {
  const levels = body.levels as Record<string, unknown> | undefined;
  const table = levels?.table as Record<string, Record<string, unknown>> | undefined;
  if (!table) return null;
  const lvKeys = Object.keys(table)
    .map(Number)
    .filter((n) => !Number.isNaN(n))
    .sort((a, b) => a - b);
  const stats = new Set<string>();
  for (const lv of lvKeys) {
    const row = table[String(lv)] ?? table[lv];
    if (row) Object.keys(row).forEach((s) => stats.add(s));
  }
  const statList = [...stats];
  return (
    <>
      <h3>Levels</h3>
      <div className="data-table-wrap">
        <table className="data-table">
          <thead>
            <tr>
              <th>stat</th>
              {lvKeys.map((lv) => (
                <th key={lv}>L{lv}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {statList.map((stat) => (
              <tr key={stat}>
                <td>
                  <code>{stat}</code>
                </td>
                {lvKeys.map((lv) => {
                  const row = table[String(lv)] ?? table[lv];
                  const v = row?.[stat];
                  return <td key={lv}>{v != null ? String(v) : "—"}</td>;
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}

// Keys PetDetail renders itself, so the generic KeyValueTable skips them.
const PET_OMIT = ["skill", "troop_bonus", "rarity", "unlock", "max_refinement"];

function PetDetail({ body }: { body: Record<string, unknown> }) {
  const skill = body.skill as Record<string, unknown> | undefined;
  const bonus = body.troop_bonus as Record<string, unknown> | undefined;
  const facts: [string, unknown][] = [
    ["Rarity", body.rarity],
    ["Unlock", body.unlock],
    ["Max refinement", body.max_refinement],
  ].filter(([, v]) => v != null && v !== "") as [string, unknown][];
  const skillValues = Array.isArray(skill?.values) ? (skill!.values as unknown[]) : [];
  return (
    <>
      {facts.length ? (
        <table className="data-table">
          <tbody>
            {facts.map(([k, v]) => (
              <tr key={k}>
                <td>{k}</td>
                <td style={{ wordBreak: "break-word" }}>{String(v)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}

      {skill ? (
        <>
          <h3>
            Skill{skill.name ? <> — {String(skill.name)}</> : null}
          </h3>
          {skill.effect ? <p className="meta">{String(skill.effect)}</p> : null}
          <table className="data-table">
            <tbody>
              {skill.cooldown ? (
                <tr>
                  <td>Cooldown</td>
                  <td>{String(skill.cooldown)}</td>
                </tr>
              ) : null}
              {skillValues.length ? (
                <tr>
                  <td>Skill levels</td>
                  <td>{skillValues.map((v) => String(v)).join(" / ")}</td>
                </tr>
              ) : null}
            </tbody>
          </table>
        </>
      ) : null}

      {bonus && (bonus.stat || bonus.max_attack || bonus.max_defense) ? (
        <>
          <h3>Troop bonus</h3>
          <table className="data-table">
            <tbody>
              {bonus.stat ? (
                <tr>
                  <td>Boosts</td>
                  <td>{String(bonus.stat)}</td>
                </tr>
              ) : null}
              {bonus.max_attack ? (
                <tr>
                  <td>Attack (max)</td>
                  <td>{String(bonus.max_attack)}</td>
                </tr>
              ) : null}
              {bonus.max_defense ? (
                <tr>
                  <td>Defense (max)</td>
                  <td>{String(bonus.max_defense)}</td>
                </tr>
              ) : null}
            </tbody>
          </table>
        </>
      ) : null}
    </>
  );
}

export function WikiDetailPanel({ detail }: { detail: WikiDetail | null }) {
  if (!detail) {
    return <p className="meta">Select an entry from the list or tiles.</p>;
  }
  const { summary, body, entity } = detail;
  return (
    <div>
      <h2>
        {summary.name || "(unnamed)"}{" "}
        <code style={{ fontSize: "0.85rem" }}>{summary.id}</code>
      </h2>
      {summary.source !== "core" ? (
        <p className="meta">📦 module: {summary.source}</p>
      ) : null}
      <WikiLink url={summary.wiki_url} />
      {entity === "buildings" ? <BuildingRequirements body={body} /> : null}
      {entity === "heroes" ? <HeroLevels body={body} /> : null}
      {entity === "pets" ? <PetDetail body={body} /> : null}
      <h3>Fields</h3>
      <KeyValueTable data={body} omit={entity === "pets" ? PET_OMIT : DEFAULT_OMIT} />
      <details style={{ marginTop: "1rem" }}>
        <summary className="meta">Raw YAML (JSON)</summary>
        <pre
          style={{
            fontSize: "0.75rem",
            overflow: "auto",
            maxHeight: 320,
            background: "#12151e",
            padding: "0.75rem",
            borderRadius: 6,
          }}
        >
          {JSON.stringify(body, null, 2)}
        </pre>
      </details>
    </div>
  );
}
