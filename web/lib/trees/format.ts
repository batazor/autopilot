import type { FlowTreeNode } from "@/components/TechTreeFlow";
import type { ResearchBranchView } from "@/lib/types";

export function fmtNum(n: number): string {
  return n.toLocaleString("en-US");
}

// "5.3K" / "1.4M" / "23,000" / 270 → number (0 when unparsable).
export function parseAmount(v: string | number): number {
  if (typeof v === "number") return v;
  const s = v.trim().replace(/,/g, "");
  const m = /^(\d+(?:\.\d+)?)\s*([KMB])?$/i.exec(s);
  if (!m) return 0;
  const mult = { K: 1e3, M: 1e6, B: 1e9 }[m[2]?.toUpperCase() as "K" | "M" | "B"] ?? 1;
  return Math.round(Number(m[1]) * mult);
}

// "00:21:30" / "90:16:40" (hours can exceed 24) / "7d" / "2d 02:00:00" → seconds.
export function parseDuration(s: string | null | undefined): number {
  const t = (s ?? "").trim();
  if (!t || t === "-") return 0;
  let sec = 0;
  const d = /(\d+)\s*d/i.exec(t);
  if (d) sec += Number(d[1]) * 86400;
  const hms = /(\d+):(\d{2}):(\d{2})/.exec(t);
  if (hms) sec += Number(hms[1]) * 3600 + Number(hms[2]) * 60 + Number(hms[3]);
  return sec;
}

export function fmtDuration(sec: number): string {
  if (sec <= 0) return "—";
  const d = Math.floor(sec / 86400);
  const h = Math.floor((sec % 86400) / 3600);
  const m = Math.floor((sec % 3600) / 60);
  return [d ? `${d}d` : "", h ? `${h}h` : "", m ? `${m}m` : ""].filter(Boolean).join(" ") || "<1m";
}

export function fmtPlanDuration(sec: number): string {
  if (sec <= 0) return "—";
  const d = Math.floor(sec / 86400);
  const h = Math.floor((sec % 86400) / 3600);
  return [d ? `${d}d` : "", h ? `${h}h` : ""].filter(Boolean).join(" ") || "<1h";
}

export function branchTotalLevels(branch: ResearchBranchView): number {
  return branch.nodes.reduce((sum, n) => sum + n.levels.length, 0);
}

export function levelKey(building: string, level: number | string): string {
  return `${building}@${level}`;
}

/** Transitive prerequisite closure (incl. `id` itself) over FlowTreeNodes. */
export function pathClosure(nodes: FlowTreeNode[], id: string): Set<string> {
  const reqs = new Map(
    nodes.map((n) => [n.id, n.requires.map((r) => (typeof r === "string" ? r : r.id))]),
  );
  const seen = new Set<string>();
  const stack = [id];
  while (stack.length) {
    const cur = stack.pop()!;
    if (seen.has(cur) || !reqs.has(cur)) continue;
    seen.add(cur);
    for (const dep of reqs.get(cur)!) stack.push(dep);
  }
  return seen;
}
