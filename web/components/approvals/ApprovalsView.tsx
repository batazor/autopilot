"use client";

import { ApprovalCanvas } from "@/components/ApprovalCanvas";
import { AppSelect } from "@/components/AppSelect";
import { AppCheckbox } from "@/components/headless";
import { PageHeader } from "@/components/PageHeader";
import { Button } from "@/components/ui";
import { SCREENSHOT_SOURCE_OPTIONS, TEST_MODULE_ALL } from "@/lib/approvals/types";
import { ApprovalToast } from "./ApprovalToast";
import { DangerButton } from "./DangerButton";
import { IdleApprovalsCard } from "./IdleApprovalsCard";
import { PendingApprovalCard } from "./PendingApprovalCard";
import { RegionProbePanel } from "./RegionProbePanel";
import { ScenarioProgressBar } from "./ScenarioProgressBar";
import { useApprovalsState } from "./useApprovalsState";

export function ApprovalsView() {
  const s = useApprovalsState();
  const {
    instanceId,
    instancesError,
    view,
    error,
    busyAction,
    imageSource,
    setImageSource,
    autoRefresh,
    setAutoRefresh,
    showPayload,
    setShowPayload,
    showReset,
    setShowReset,
    showProbe,
    setShowProbe,
    toasts,
    now,
    regionProbe,
    probeRegion,
    setProbeRegion,
    probeThreshold,
    setProbeThreshold,
    probeError,
    modules,
    testModule,
    confirmAction,
    setConfirmAction,
    imageUrl,
    probeImageUrl,
    payloadJson,
    overlaysStable,
    scenarioLabel,
    regionLabel,
    traceId,
    navigation,
    taskContext,
    actionType,
    setNodeTarget,
    currentScreen,
    activePlayer,
    inGameId,
    playerAccountKey,
    hasPending,
    stalePending,
    staleTaskLabel,
    staleWorkerTask,
    scenarioProgress,
    approvalEnabled,
    heartbeatActive,
    anyBusy,
    refresh,
    refreshProbe,
    onChangeTestModule,
    extendToast,
    dismissToast,
    onDecision,
    onToggleEnabled,
    onClearPending,
    onResetScreen,
    onResetPlayer,
    onClearQueue,
  } = s;

  return (
    <>
      <PageHeader title="Click approvals" fleet>
        Approve or reject sensitive worker actions (ADB taps / swipes / screen
        node updates) in real time. The page also writes the per-instance
        heartbeat so the worker only blocks when this page is open.
      </PageHeader>

      {/* One compact status strip replaces the old status row + "Current
          context" panel. Single source of truth for the operator: am I
          waiting, who/where am I, and are the safety rails on. */}
      <div className="approvals-status-strip">
        <span
          className={`status-pill status-pill--lg ${hasPending ? "status-pending pulse" : "status-idle"}`}
          title={hasPending ? "A worker is waiting on your decision" : "No worker action waiting"}
        >
          <span className="status-pill__dot" aria-hidden />
          {hasPending ? "Pending" : "Idle"}
        </span>

        <span className="status-fact">
          <span className="status-fact__label">Instance</span>
          <code>{instanceId || "—"}</code>
        </span>
        <span className="status-fact">
          <span className="status-fact__label">Node</span>
          <code>{currentScreen || "—"}</code>
        </span>
        <span className="status-fact">
          <span className="status-fact__label">Player</span>
          <code>{inGameId || activePlayer || "—"}</code>
          <button
            type="button"
            className="status-fact__reset"
            onClick={onResetPlayer}
            disabled={!instanceId || busyAction !== null}
            title="Clear the active-player binding so the identity probe re-detects the gamer id"
          >
            {busyAction === "reset-player" ? "Resetting…" : "Reset id"}
          </button>
          {playerAccountKey ? (
            <span className="status-fact__sub" title="Redis active_player account key">
              {playerAccountKey}
            </span>
          ) : null}
        </span>

        <span className="status-fact status-fact--rail">
          <span
            className={`heartbeat-dot ${heartbeatActive ? "heartbeat-dot--on" : "heartbeat-dot--off"}`}
            title={heartbeatActive ? "Heartbeat active (ttl ≈ 5s)" : "Heartbeat off — worker will not block"}
            aria-hidden
          />
          <span className="status-fact__label">Heartbeat</span>
          <strong className={heartbeatActive ? "text-emerald-300" : "text-wos-text-muted"}>
            {heartbeatActive ? "live" : "off"}
          </strong>
        </span>
      </div>

      {scenarioProgress ? <ScenarioProgressBar progress={scenarioProgress} /> : null}

      {error || instancesError ? (
        <div className="error-banner">{error ?? instancesError}</div>
      ) : null}

      {testModule ? (
        <div
          className="approvals-callout approvals-callout--warn"
          role="status"
          aria-live="polite"
        >
          Test mode: only <code>{testModule}</code> scenarios and analyzers run.
          Other queued tasks stay parked until you switch back to{" "}
          <strong>All modules</strong>.
        </div>
      ) : null}

      <div className="toolbar approvals-toolbar">
        {/* Promoted to a real toggle so the most important global safety
            switch isn't a 16px checkbox shoved between two dropdowns. */}
        <button
          type="button"
          role="switch"
          aria-checked={approvalEnabled}
          className={`approval-switch ${approvalEnabled ? "is-on" : "is-off"}`}
          disabled={busyAction === "toggle" || !instanceId}
          onClick={() => onToggleEnabled(!approvalEnabled)}
          title="OFF lets the worker tap/swipe without asking"
        >
          <span className="approval-switch__track">
            <span className="approval-switch__thumb" />
          </span>
          <span className="approval-switch__text">
            <span className="approval-switch__label">Approval mode</span>
            <span className="approval-switch__value">
              {approvalEnabled ? "ON · worker will ask" : "OFF · worker auto-acts"}
            </span>
          </span>
        </button>

        <AppSelect
          label="Screenshot"
          options={SCREENSHOT_SOURCE_OPTIONS}
          value={imageSource}
          onChange={(next) => setImageSource(next as typeof imageSource)}
          minWidth={210}
          isSearchable={false}
        />
        <AppSelect
          label="Module"
          options={[
            { value: TEST_MODULE_ALL, label: "All modules" },
            ...modules.map((m) => ({
              value: m.id,
              label: `${m.title || m.id} · ${m.id}`,
            })),
          ]}
          value={testModule}
          onChange={(next) => void onChangeTestModule(next)}
          minWidth={260}
          isSearchable
          disabled={!instanceId || busyAction === "test-module"}
        />
        <AppCheckbox
          inline
          className="checkbox-label"
          checked={autoRefresh}
          onChange={setAutoRefresh}
          label="Auto-refresh"
        />
        <Button onClick={refresh} disabled={anyBusy}>
          Refresh now
        </Button>
        <Button onClick={() => setShowReset((v) => !v)} aria-expanded={showReset}>
          {showReset ? "Hide reset" : "Reset…"}
        </Button>
      </div>

      {showReset ? (
        <section className="panel approvals-reset-panel">
          <h2>Reset</h2>
          <p className="meta">
            Operator escape hatches. These only affect Redis — the worker
            process keeps running.
          </p>
          <div className="approvals-reset-actions">
            <DangerButton
              label="Clear queue (all instances)"
              confirmLabel="Confirm clear queue"
              tooltip="Deletes wos:queue:* (keeps :running). Same as the Streamlit Clear queue button."
              confirming={confirmAction === "clear-queue"}
              busy={busyAction === "clear-queue"}
              disabled={anyBusy && busyAction !== "clear-queue"}
              onArm={() => setConfirmAction("clear-queue")}
              onCancel={() => setConfirmAction(null)}
              onConfirm={onClearQueue}
            />
            <DangerButton
              label="Clear pending approval"
              confirmLabel="Confirm clear pending"
              tooltip="Cancels the in-flight approval for this instance (treated as reject)"
              confirming={confirmAction === "clear-pending"}
              busy={busyAction === "clear-pending"}
              disabled={!instanceId || (anyBusy && busyAction !== "clear-pending")}
              onArm={() => setConfirmAction("clear-pending")}
              onCancel={() => setConfirmAction(null)}
              onConfirm={onClearPending}
            />
            <Button
              disabled={anyBusy || !instanceId}
              onClick={onResetScreen}
              title="Sets current_screen to empty so the detector re-classifies from scratch"
            >
              {busyAction === "reset-screen" ? "Resetting…" : "Reset node to none (unknown)"}
            </Button>
          </div>
        </section>
      ) : null}

      <div className="approvals-grid">
        <section className="panel">
          <h2>Screenshot</h2>
          <ApprovalCanvas
            imageUrl={imageUrl}
            width={view?.preview.width ?? 0}
            height={view?.preview.height ?? 0}
            overlays={overlaysStable}
            workerActive={!!view?.worker_alive}
          />
          {view?.tap_x != null && view?.tap_y != null ? (
            <p className="meta">
              Tap target: ({view.tap_x}, {view.tap_y})
            </p>
          ) : null}
        </section>

        <section className={`panel ${hasPending ? "panel--accent-pending" : ""}`}>
          <h2 className="panel-title-row">
            Approvals
            {hasPending ? (
              <span className="kbd-hint" aria-label="Keyboard shortcuts">
                <kbd>A</kbd>pprove · <kbd>S</kbd>kip · <kbd>R</kbd>eject
              </span>
            ) : null}
          </h2>
          {hasPending ? (
            <PendingApprovalCard
              view={view!}
              instanceId={instanceId}
              scenarioLabel={scenarioLabel}
              regionLabel={regionLabel}
              traceId={traceId}
              tempoTraceUrl={view?.tempo_trace_url || ""}
              labelingHref={view?.labeling_href || ""}
              navigation={navigation}
              taskContext={taskContext}
              actionType={actionType}
              setNodeTarget={setNodeTarget}
              payloadJson={payloadJson}
              showPayload={showPayload}
              onTogglePayload={() => setShowPayload((v) => !v)}
              busyAction={busyAction}
              onDecision={onDecision}
            />
          ) : (
            <IdleApprovalsCard
              busy={anyBusy}
              resetting={busyAction === "reset-screen"}
              onResetScreen={onResetScreen}
              instanceId={instanceId}
              stalePending={stalePending}
              staleWorkerTask={staleWorkerTask}
              staleTaskLabel={staleTaskLabel}
              clearingPending={busyAction === "clear-pending"}
              onClearPending={onClearPending}
            />
          )}
        </section>
      </div>

      <section className="panel approvals-probe-panel panel--spaced">
        <button
          type="button"
          className="approvals-probe-toggle"
          onClick={() => setShowProbe((v) => !v)}
          aria-expanded={showProbe}
        >
          <span className={`approvals-probe-toggle__chevron ${showProbe ? "is-open" : ""}`} aria-hidden>
            ▸
          </span>
          <span>Region probe</span>
          <span className="meta approvals-probe-toggle__meta">
            {showProbe ? "Live · SSE" : "Click to run an area.json region check"}
          </span>
        </button>
        {showProbe ? (
          <RegionProbePanel
            probe={regionProbe}
            imageUrl={probeImageUrl}
            selectedRegion={probeRegion}
            threshold={probeThreshold}
            error={probeError}
            onRegionChange={(region) => {
              setProbeRegion(region);
              setProbeThreshold(null);
            }}
            onThresholdChange={setProbeThreshold}
            onRefresh={refreshProbe}
          />
        ) : null}
      </section>

      {toasts.length ? (
        <div className="approvals-toast-stack" role="status" aria-live="polite">
          {toasts.map((t) => (
            <ApprovalToast
              key={t.id}
              toast={t}
              now={now}
              onDismiss={() => dismissToast(t.id)}
              onExtend={(extraMs) => extendToast(t.id, extraMs)}
            />
          ))}
        </div>
      ) : null}
    </>
  );
}
