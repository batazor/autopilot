"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { AppListbox, AppTabs } from "@/components/headless";
import { OptimizerResults } from "@/components/optimizer/OptimizerResults";
import { useFleet } from "@/components/FleetContextProvider";
import { ErrorBanner, useFeedback } from "@/components/feedback";
import { PageHeader } from "@/components/PageHeader";
import {
  fetchOptimizerMeta,
  optimizerApprove,
  optimizerDryRun,
  optimizerQueue,
  reloadOptimizerBalance,
  solveOptimizer,
} from "@/lib/api";
import type { OptimizerMeta, OptimizerSolveResult } from "@/lib/config-pages";

type Tab = "production" | "playground";

export default function OptimizerPage() {
  const { instanceId, setInstanceId } = useFleet();
  const { showSuccess } = useFeedback();
  const [tab, setTab] = useState<Tab>("production");
  const [meta, setMeta] = useState<OptimizerMeta | null>(null);
  const [gamerId, setGamerId] = useState("");
  const [profileId, setProfileId] = useState("");
  const [serverAge, setServerAge] = useState(14);
  const [planK, setPlanK] = useState(8);
  const [stateJson, setStateJson] = useState("");
  const [result, setResult] = useState<OptimizerSolveResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    fetchOptimizerMeta()
      .then((m) => {
        setMeta(m);
        setGamerId(m.gamers[0]?.id ?? "");
        if (m.instances.length) {
          const pick =
            instanceId && m.instances.includes(instanceId)
              ? instanceId
              : m.instances[0];
          if (pick !== instanceId) setInstanceId(pick);
        }
        setProfileId(m.active_profile_id);
        setStateJson(JSON.stringify(m.default_playground_state, null, 2));
      })
      .catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, []);

  const runSolve = useCallback(async () => {
    if (!meta) return;
    setBusy(true);
    setError(null);
    try {
      let state_flat: Record<string, unknown> | undefined;
      if (tab === "playground") {
        state_flat = JSON.parse(stateJson) as Record<string, unknown>;
      }
      const data = await solveOptimizer({
        mode: tab,
        gamer_id: tab === "production" ? gamerId : undefined,
        state_flat,
        server_age_days: serverAge,
        plan_k: planK,
        profile_id: profileId || undefined,
      });
      setResult(data);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }, [meta, tab, gamerId, stateJson, serverAge, planK, profileId]);

  useEffect(() => {
    if (meta && tab === "production") {
      runSolve();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- re-solve when production inputs change
  }, [meta, tab, gamerId, profileId, serverAge, planK]);

  const candidateId = result?.next_command?.candidate_id;

  async function handleDryRun() {
    if (!candidateId) return;
    setBusy(true);
    try {
      const r = await optimizerDryRun({
        candidate_id: candidateId,
        gamer_id: tab === "production" ? gamerId : undefined,
        state_flat:
          tab === "playground"
            ? (JSON.parse(stateJson) as Record<string, unknown>)
            : undefined,
        server_age_days: serverAge,
        profile_id: profileId || undefined,
      });
      showSuccess(`Would change ${r.changed_keys} key(s)`);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function handleApprove() {
    if (!candidateId || !gamerId) return;
    setBusy(true);
    try {
      const r = await optimizerApprove({
        candidate_id: candidateId,
        gamer_id: gamerId,
        server_age_days: serverAge,
        profile_id: profileId || undefined,
      });
      showSuccess(`Recorded ${r.persisted_keys} key(s) to db/state.yaml`);
      await runSolve();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function handleQueue() {
    if (!candidateId || !gamerId || !instanceId) return;
    setBusy(true);
    try {
      const r = await optimizerQueue({
        candidate_id: candidateId,
        gamer_id: gamerId,
        instance_id: instanceId,
        server_age_days: serverAge,
        profile_id: profileId || undefined,
      });
      showSuccess(`Task queued (${r.task_id}) · ${r.dsl_scenario}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <PageHeader title="Optimizer" fleet>
        <p className="muted">
          Production uses live <code>db/state.yaml</code>. Playground is synthetic only.{" "}
          <Link href="/balance">Balance</Link> · <Link href="/player-state">Player state</Link>
        </p>
      </PageHeader>

      <AppTabs
        tabs={[
          { key: "production", label: "Production" },
          { key: "playground", label: "Playground" },
        ]}
        selectedKey={tab}
        onChange={(key) => setTab(key as Tab)}
        renderPanels={false}
        afterTabs={
          <>
            <button
              type="button"
              className="btn-secondary"
              onClick={() =>
                reloadOptimizerBalance().then(() =>
                  showSuccess("Balance cache cleared"),
                )
              }
            >
              Reload balance
            </button>
            <button
              type="button"
              className="btn-secondary"
              disabled={busy}
              onClick={runSolve}
            >
              Re-solve
            </button>
          </>
        }
      />

      <div className="toolbar">
        {tab === "production" && meta && (
          <AppListbox
            inline
            label="Gamer"
            value={gamerId}
            onChange={setGamerId}
            options={meta.gamers.map((g) => ({
              value: g.id,
              label: `${g.nickname || g.id} · ${g.id}`,
            }))}
            minWidth={240}
          />
        )}
        {meta && (
        <AppListbox
          inline
          label="Profile"
          value={profileId}
          onChange={setProfileId}
          options={meta.profiles.map((p) => ({ value: p.id, label: p.id }))}
          minWidth={200}
        />
        )}
        <label>
          server_age_days
          <input
            type="number"
            min={0}
            max={400}
            value={serverAge}
            onChange={(e) => setServerAge(Number(e.target.value))}
          />
        </label>
        <label>
          Plan K
          <input
            type="number"
            min={1}
            max={20}
            value={planK}
            onChange={(e) => setPlanK(Number(e.target.value))}
          />
        </label>
      </div>

      {tab === "playground" && (
        <section className="panel">
          <h2>Synthetic state (JSON)</h2>
          <textarea
            className="yaml-editor"
            value={stateJson}
            onChange={(e) => setStateJson(e.target.value)}
            rows={12}
            spellCheck={false}
          />
        </section>
      )}

      <ErrorBanner message={error} />

      {result && (
        <OptimizerResults
          data={result}
          gamerId={tab === "production" ? gamerId : undefined}
          instanceId={tab === "production" ? instanceId : undefined}
          busy={busy}
          onDryRun={candidateId ? handleDryRun : undefined}
          onApprove={tab === "production" && candidateId ? handleApprove : undefined}
          onQueue={tab === "production" && candidateId ? handleQueue : undefined}
        />
      )}
    </>
  );
}
