"use client";

import { useCallback, useEffect, useState } from "react";
import {
  computeFullPlan,
  computePlanner,
  fetchPlannerMeta,
  fetchPlayerPlannerState,
  savePlayerPlannerState,
  type FullPlanResult,
  type PlannerMeta,
  type PlannerResult,
} from "@/lib/api";
import {
  buildBody,
  domainById,
  PLANNER_DOMAINS,
  valuesFromBody,
  type PlannerDomainConfig,
} from "./domains";

function initialValues(cfg: PlannerDomainConfig): Record<string, string> {
  const v: Record<string, string> = {};
  for (const f of cfg.fields) {
    v[f.key] = f.kind === "role" ? "" : String(f.default ?? "");
  }
  return v;
}

/** Drives the planner calculator page: meta, per-domain form state, compute. */
export function usePlanner() {
  const [meta, setMeta] = useState<PlannerMeta | null>(null);
  const [domain, setDomain] = useState<string>(PLANNER_DOMAINS[0].id);
  const [valuesByDomain, setValuesByDomain] = useState<
    Record<string, Record<string, string>>
  >(() => {
    const all: Record<string, Record<string, string>> = {};
    for (const d of PLANNER_DOMAINS) all[d.id] = initialValues(d);
    return all;
  });
  const [resultByDomain, setResultByDomain] = useState<
    Record<string, PlannerResult | null>
  >({});
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [fullResult, setFullResult] = useState<FullPlanResult | null>(null);
  const [fullError, setFullError] = useState<string | null>(null);

  useEffect(() => {
    fetchPlannerMeta()
      .then(setMeta)
      .catch(() => setMeta(null));
  }, []);

  const cfg = domainById(domain) ?? PLANNER_DOMAINS[0];
  const values = valuesByDomain[domain];
  const result = resultByDomain[domain] ?? null;

  const setValue = useCallback(
    (key: string, val: string) => {
      setValuesByDomain((prev) => ({
        ...prev,
        [domain]: { ...prev[domain], [key]: val },
      }));
    },
    [domain],
  );

  const reset = useCallback(() => {
    setValuesByDomain((prev) => ({ ...prev, [domain]: initialValues(cfg) }));
    setError(null);
  }, [domain, cfg]);

  const compute = useCallback(async () => {
    setError(null);
    let body: Record<string, unknown>;
    try {
      body = buildBody(cfg, values);
    } catch (e) {
      setError((e as Error).message);
      return;
    }
    setBusy(true);
    try {
      const res = await computePlanner(cfg.id, body);
      setResultByDomain((prev) => ({ ...prev, [domain]: res }));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }, [cfg, values, domain]);

  // Fill every domain's form from a player's saved planner state.
  const loadFromPlayer = useCallback(async (playerId: string) => {
    setError(null);
    setNotice(null);
    setSyncing(true);
    try {
      const state = await fetchPlayerPlannerState(playerId);
      setValuesByDomain((prev) => {
        const next = { ...prev };
        for (const [d, body] of Object.entries(state.domains)) {
          const dcfg = domainById(d);
          if (!dcfg) continue;
          const partial = valuesFromBody(dcfg, body);
          next[d] = { ...next[d], ...partial };
        }
        return next;
      });
      setNotice(`Loaded ${state.nickname || playerId}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSyncing(false);
    }
  }, []);

  // Persist every domain's current inputs back to the player's saved state.
  const saveToPlayer = useCallback(
    async (playerId: string) => {
      setError(null);
      setNotice(null);
      const domains: Record<string, Record<string, unknown>> = {};
      try {
        for (const d of PLANNER_DOMAINS) {
          domains[d.id] = buildBody(d, valuesByDomain[d.id]);
        }
      } catch (e) {
        setError((e as Error).message);
        return;
      }
      setSyncing(true);
      try {
        const res = await savePlayerPlannerState(playerId, domains);
        setNotice(`Saved ${res.saved_domains.length} domains`);
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      } finally {
        setSyncing(false);
      }
    },
    [valuesByDomain],
  );

  // Run every domain planner on the current inputs and arbitrate via the
  // coordinator. Building/research/heroes/pets bodies come from their tabs;
  // `balances` is the shared pool the coordinator spends.
  const runFull = useCallback(
    async (balances: Record<string, number>) => {
      setFullError(null);
      let bodies: Record<string, unknown>;
      try {
        bodies = {
          building: buildBody(domainById("building")!, valuesByDomain.building),
          research: buildBody(domainById("research")!, valuesByDomain.research),
          heroes: buildBody(domainById("heroes")!, valuesByDomain.heroes),
          pets: buildBody(domainById("pets")!, valuesByDomain.pets),
          balances,
        };
      } catch (e) {
        setFullError((e as Error).message);
        return;
      }
      setBusy(true);
      try {
        const res = await computeFullPlan(bodies);
        setFullResult(res);
      } catch (e) {
        setFullError(e instanceof Error ? e.message : String(e));
      } finally {
        setBusy(false);
      }
    },
    [valuesByDomain],
  );

  return {
    meta,
    domain,
    setDomain,
    cfg,
    values,
    setValue,
    result,
    error,
    busy,
    compute,
    reset,
    syncing,
    notice,
    loadFromPlayer,
    saveToPlayer,
    runFull,
    fullResult,
    fullError,
  };
}
