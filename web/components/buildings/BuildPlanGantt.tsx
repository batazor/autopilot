"use client";

import dynamic from "next/dynamic";
import { useEffect, useMemo, useState } from "react";
import "@svar-ui/react-gantt/all.css";
import { AppSwitch } from "@/components/headless";
import type { BuildPlanView } from "@/lib/types";

// SVAR Gantt touches the DOM on import — load client-only to avoid SSR errors.
const Gantt = dynamic(
  () => import("@svar-ui/react-gantt").then((m) => ({ default: m.Gantt })),
  { ssr: false },
);

// The schedule is relative game-time (seconds from t=0), not a calendar. Anchor
// it at a fixed local epoch so the Gantt's date layout reads as elapsed time;
// the scales render "Month N" / "Week N" / "Day N" / clock hours instead of real
// dates. SVAR starts weeks on Sunday, so the epoch is a Sunday — that way the
// week grid aligns to it with no stray leading "Week 0" cell.
const BASE = new Date(2001, 0, 7); // Sun 7 Jan 2001
const DAY_MS = 86_400_000;

const at = (sec: number): Date => new Date(BASE.getTime() + sec * 1000);
const daysElapsed = (d: Date): number => Math.round((d.getTime() - BASE.getTime()) / DAY_MS);
const monthsElapsed = (d: Date): number =>
  (d.getFullYear() - BASE.getFullYear()) * 12 + (d.getMonth() - BASE.getMonth());
const weeksElapsed = (d: Date): number => Math.floor(daysElapsed(d) / 7);

const BUILDING_ICON: Record<string, string> = {
  furnace: "🔥",
  embassy: "🏛️",
  storehouse: "📦",
  infirmary: "🏥",
  shelter: "🏠",
  cookhouse: "🍲",
  hero_hall: "🦸",
  infantry_camp: "🛡️",
  marksman_camp: "🏹",
  lancer_camp: "🐎",
  research_center: "🔬",
  command_center: "🎖️",
  iron_mine: "⛏️",
  sawmill: "🪵",
  coal_mine: "⚫",
  hunters_hut: "🥩",
};

function buildingIcon(id: string): string {
  return BUILDING_ICON[id] ?? BUILDING_ICON[id.replace(/^fire_crystal_/, "")] ?? "🏗️";
}

function fmtDuration(sec: number): string {
  if (sec <= 0) return "0";
  const d = Math.floor(sec / 86400);
  const h = Math.floor((sec % 86400) / 3600);
  const m = Math.floor((sec % 3600) / 60);
  const s = sec % 60;
  if (d) return [`${d}d`, h ? `${h}h` : ""].filter(Boolean).join(" ");
  if (h) return [`${h}h`, m ? `${m}m` : ""].filter(Boolean).join(" ");
  if (m) return [`${m}m`, s ? `${s}s` : ""].filter(Boolean).join(" ");
  return `${s}s`;
}

// Scale formatters reused across zoom levels (all in elapsed game-time).
const fmtMonth = (d: Date) => `Month ${monthsElapsed(d) + 1}`;
const fmtWeek = (d: Date) => `W${weeksElapsed(d) + 1}`;
const fmtDay = (d: Date) => `D${daysElapsed(d)}`;
const fmtHour = (d: Date) => (d.getHours() === 0 ? `D${daysElapsed(d)}` : `${d.getHours()}:00`);

// Coarse → fine zoom ladder. Default sits at month/week/day; zooming in
// (⌘/Ctrl + scroll) drills to day/hour so even the seconds-long first builds
// are legible; zooming out collapses to month/week for the whole road to 30.
const ZOOM_LEVELS = [
  {
    minCellWidth: 60,
    maxCellWidth: 320,
    scales: [
      { unit: "month" as const, step: 1, format: fmtMonth },
      { unit: "week" as const, step: 1, format: fmtWeek },
    ],
  },
  {
    minCellWidth: 34,
    maxCellWidth: 200,
    scales: [
      { unit: "month" as const, step: 1, format: fmtMonth },
      { unit: "week" as const, step: 1, format: fmtWeek },
      { unit: "day" as const, step: 1, format: fmtDay },
    ],
  },
  {
    minCellWidth: 28,
    maxCellWidth: 160,
    scales: [
      { unit: "week" as const, step: 1, format: fmtWeek },
      { unit: "day" as const, step: 1, format: fmtDay },
      { unit: "hour" as const, step: 3, format: fmtHour },
    ],
  },
];

// Stable references for the Gantt's object/array props — passing fresh literals
// each render makes SVAR's reactive store re-init in a loop ("max update depth").
const ZOOM_CONFIG = { level: 1, minCellWidth: 26, maxCellWidth: 320, levels: ZOOM_LEVELS };
const COL_TEXT = { id: "text", header: "Build order", width: 240, flexgrow: 1 };
const COL_TIME = { id: "dur", header: "Time", width: 78, align: "right" as const };
const COL_QUEUE = { id: "q", header: "Q", width: 46, align: "center" as const };

const cap = (s: string): string => s.charAt(0).toUpperCase() + s.slice(1);

function useGanttTheme(): string {
  const [dark, setDark] = useState(true);
  useEffect(() => {
    const read = () =>
      setDark(document.documentElement.getAttribute("data-theme") !== "light");
    read();
    const obs = new MutationObserver(read);
    obs.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ["data-theme"],
    });
    return () => obs.disconnect();
  }, []);
  return dark ? "wx-willow-dark-theme" : "wx-willow-theme";
}

export function BuildPlanGantt({ plan }: { plan: BuildPlanView }) {
  const theme = useGanttTheme();
  const [grouped, setGrouped] = useState(false);

  const multiQueue = plan.queues > 1;

  const { tasks, links, end, columns } = useMemo(() => {
    // Pad the right edge a touch so the last bar isn't flush against the border.
    const end = at(plan.total_time_s + Math.max(86400, plan.total_time_s * 0.02));

    // Milestone diamonds at the moment the goal building reaches each tier — the
    // Time column then reads the cumulative days-to-reach (e.g. Furnace 30 → 347d).
    const goalName = cap(plan.goal);
    const milestones = [10, 20, 30]
      .map((lvl) => {
        const st = plan.steps.find((s) => s.building_id === plan.goal && s.to_rank === lvl);
        return st
          ? {
              id: `ms:${lvl}`,
              text: `🎯 ${goalName} ${lvl}`,
              dur: fmtDuration(st.end_s),
              type: "milestone" as const,
              start: at(st.end_s),
              end: at(st.end_s),
            }
          : null;
      })
      .filter((m): m is NonNullable<typeof m> => m !== null);

    if (grouped) {
      // One collapsible summary row per building, its upgrades nested beneath.
      // `rollups` draws the child bars on the (collapsed) summary so the row
      // shows the real construction segments, not one idle-spanning block.
      const byBuilding = new Map<string, BuildPlanView["steps"]>();
      for (const s of plan.steps) {
        const list = byBuilding.get(s.building_id) ?? [];
        list.push(s);
        byBuilding.set(s.building_id, list);
      }
      const out: Record<string, unknown>[] = [];
      for (const b of plan.buildings) {
        const steps = byBuilding.get(b.id) ?? [];
        if (steps.length === 0) continue;
        const buildSec = steps.reduce((a, s) => a + s.duration_s, 0);
        out.push({
          id: `b:${b.id}`,
          text: `${buildingIcon(b.id)} ${b.name}`,
          dur: fmtDuration(buildSec),
          type: "summary",
          open: false,
          // No explicit span: SVAR derives it from the children, and `rollups`
          // then draws each child's real construction segment on this row.
        });
        for (const s of steps) {
          out.push({
            id: s.seq,
            parent: `b:${b.id}`,
            text: `Lv ${s.from_level}→${s.to_level}`,
            dur: fmtDuration(s.duration_s),
            q: String(s.queue + 1),
            type: s.duration_s > 0 ? "task" : "milestone",
            start: at(s.start_s),
            end: at(s.end_s),
          });
        }
      }
      const columns = multiQueue ? [COL_TEXT, COL_QUEUE, COL_TIME] : [COL_TEXT, COL_TIME];
      return { tasks: [...milestones, ...out], links: [] as Record<string, unknown>[], end, columns };
    }

    // Flat build order: one row per step, the bar exactly as long as its
    // construction time (0-second steps → milestone diamonds). Arrows chain each
    // step to the next one ON THE SAME QUEUE — "who builds after whom" per queue
    // (a single chain for 1 queue, two interleaved chains for 2).
    const tasks = plan.steps.map((s) => ({
      id: s.seq,
      text: `${buildingIcon(s.building_id)} ${s.building_name} ${s.from_level}→${s.to_level}`,
      dur: fmtDuration(s.duration_s),
      q: String(s.queue + 1),
      type: s.duration_s > 0 ? ("task" as const) : ("milestone" as const),
      start: at(s.start_s),
      end: at(s.end_s),
    }));
    const links: Record<string, unknown>[] = [];
    const lastOnQueue = new Map<number, number>();
    for (const s of plan.steps) {
      const prev = lastOnQueue.get(s.queue);
      if (prev !== undefined) {
        links.push({ id: `${prev}-${s.seq}`, source: prev, target: s.seq, type: "e2s" });
      }
      lastOnQueue.set(s.queue, s.seq);
    }
    const columns = multiQueue ? [COL_TEXT, COL_QUEUE, COL_TIME] : [COL_TEXT, COL_TIME];
    return { tasks: [...milestones, ...tasks], links, end, columns };
  }, [plan, grouped, multiQueue]);

  if (plan.steps.length === 0) {
    const msg =
      plan.reason === "goal_reached"
        ? "Already at the goal — nothing left to build on this path."
        : plan.reason === "blocked"
          ? "The plan is blocked by an unbuildable prerequisite."
          : "No build steps for this goal.";
    return <p className="muted">{msg}</p>;
  }

  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-center justify-between gap-3">
        <AppSwitch
          inline
          checked={grouped}
          onChange={setGrouped}
          label="Group by building"
          title="Switch between the flat build order (with arrows) and one collapsible row per building"
        />
        <span className="muted text-xs">
          {multiQueue ? "Q = construction queue · " : ""}⌘/Ctrl + scroll to zoom down to hours
        </span>
      </div>
      <div className={theme} style={{ height: 600 }}>
        <Gantt
          tasks={tasks}
          links={links}
          start={BASE}
          end={end}
          cellHeight={32}
          zoom={ZOOM_CONFIG}
          rollups={grouped}
          readonly
          columns={columns}
        />
      </div>
    </div>
  );
}
