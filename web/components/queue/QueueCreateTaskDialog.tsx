"use client";

import {
  Dialog,
  DialogBackdrop,
  DialogPanel,
  DialogTitle,
} from "@headlessui/react";
import { useEffect, useMemo, useState } from "react";
import { AppCombobox, AppListbox } from "@/components/headless";
import { createQueueTask, fetchInstances, fetchModuleScenarios } from "@/lib/api";
import type { ScenarioRow } from "@/lib/config-pages";

type Props = {
  open: boolean;
  defaultScheduledAt: number | null;
  defaultInstanceId?: string;
  defaultPlayerId?: string;
  onClose: () => void;
  onCreated: (task: { task_id: string; scheduled_at: number }) => void;
  onError: (message: string) => void;
};

function toLocalDatetimeInputValue(epochSeconds: number): string {
  const d = new Date(epochSeconds * 1000);
  const pad = (n: number) => String(n).padStart(2, "0");
  return (
    `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}` +
    `T${pad(d.getHours())}:${pad(d.getMinutes())}`
  );
}

function fromLocalDatetimeInputValue(value: string): number | null {
  const ms = new Date(value).getTime();
  return Number.isFinite(ms) ? ms / 1000 : null;
}

export function QueueCreateTaskDialog({
  open,
  defaultScheduledAt,
  defaultInstanceId,
  defaultPlayerId,
  onClose,
  onCreated,
  onError,
}: Props) {
  const [scenarios, setScenarios] = useState<ScenarioRow[]>([]);
  const [scenariosLoading, setScenariosLoading] = useState(false);
  const [instances, setInstances] = useState<string[]>([]);
  const [instancesLoading, setInstancesLoading] = useState(false);

  const [scenarioKey, setScenarioKey] = useState("");
  const [instanceId, setInstanceId] = useState("");
  const [playerId, setPlayerId] = useState("");
  const [whenLocal, setWhenLocal] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!open) return;
    setBusy(false);
    setScenarioKey("");
    setInstanceId(defaultInstanceId ?? "");
    setPlayerId(defaultPlayerId ?? "");
    setWhenLocal(
      toLocalDatetimeInputValue(defaultScheduledAt ?? Date.now() / 1000),
    );
  }, [open, defaultScheduledAt, defaultInstanceId, defaultPlayerId]);

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    setScenariosLoading(true);
    fetchModuleScenarios("all")
      .then((rows) => {
        if (!cancelled) setScenarios(rows);
      })
      .catch((err) =>
        onError(err instanceof Error ? err.message : String(err)),
      )
      .finally(() => {
        if (!cancelled) setScenariosLoading(false);
      });
    setInstancesLoading(true);
    fetchInstances()
      .then((rows) => {
        if (!cancelled) setInstances(rows);
      })
      .catch((err) =>
        onError(err instanceof Error ? err.message : String(err)),
      )
      .finally(() => {
        if (!cancelled) setInstancesLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [open, onError]);

  const selectedScenario = useMemo(
    () => scenarios.find((s) => s.key === scenarioKey) ?? null,
    [scenarios, scenarioKey],
  );
  const deviceLevel = selectedScenario?.device_level === true;

  const scenarioOptions = useMemo(
    () =>
      [...scenarios]
        .sort((a, b) => a.key.localeCompare(b.key))
        .map((s) => ({
          value: s.key,
          label: `${s.name || s.key}  ·  ${s.key}`,
        })),
    [scenarios],
  );

  const instanceOptions = useMemo(
    () => instances.map((id) => ({ value: id, label: id })),
    [instances],
  );

  const canSubmit =
    !busy &&
    scenarioKey.trim().length > 0 &&
    instanceId.trim().length > 0 &&
    (deviceLevel || playerId.trim().length > 0) &&
    fromLocalDatetimeInputValue(whenLocal) != null;

  const handleSubmit = async () => {
    const scheduledAt = fromLocalDatetimeInputValue(whenLocal);
    if (scheduledAt == null) {
      onError("Invalid scheduled time");
      return;
    }
    setBusy(true);
    try {
      const result = await createQueueTask({
        scenario_key: scenarioKey,
        instance_id: instanceId,
        player_id: deviceLevel ? "" : playerId,
        scheduled_at: scheduledAt,
      });
      onCreated({ task_id: result.task_id, scheduled_at: scheduledAt });
      onClose();
    } catch (err) {
      onError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog open={open} onClose={onClose} className="headless-dialog-root">
      <DialogBackdrop transition className="headless-dialog__backdrop" />
      <div className="headless-dialog__container">
        <DialogPanel transition className="headless-dialog__panel queue-create-dialog">
          <DialogTitle className="headless-dialog__title">
            Schedule task
          </DialogTitle>
          <div className="headless-dialog__body">
            <div className="queue-create-dialog__fields">
              <label className="queue-create-dialog__field">
                <span>When</span>
                <input
                  type="datetime-local"
                  value={whenLocal}
                  onChange={(e) => setWhenLocal(e.target.value)}
                  className="queue-create-dialog__input"
                />
              </label>

              <AppCombobox
                fullWidth
                label="Scenario"
                value={scenarioKey}
                onChange={setScenarioKey}
                options={scenarioOptions}
                loading={scenariosLoading}
                placeholder="Pick a scenario…"
              />

              <AppListbox
                label="Instance"
                value={instanceId}
                onChange={setInstanceId}
                options={instanceOptions}
                placeholder={instancesLoading ? "Loading…" : "Select instance"}
              />

              <label className="queue-create-dialog__field">
                <span>
                  Player ID
                  {deviceLevel ? (
                    <em className="muted">  (not required for device-level)</em>
                  ) : null}
                </span>
                <input
                  type="text"
                  value={playerId}
                  onChange={(e) => setPlayerId(e.target.value)}
                  placeholder={deviceLevel ? "(device)" : "e.g. 123456"}
                  className="queue-create-dialog__input"
                  disabled={deviceLevel}
                />
              </label>
            </div>
          </div>
          <div className="headless-dialog__actions">
            <button
              type="button"
              className="btn-secondary"
              disabled={busy}
              onClick={onClose}
            >
              Cancel
            </button>
            <button
              type="button"
              className="btn-primary"
              disabled={!canSubmit}
              onClick={() => void handleSubmit()}
            >
              {busy ? "Scheduling…" : "Schedule"}
            </button>
          </div>
        </DialogPanel>
      </div>
    </Dialog>
  );
}
