"use client";

import { useEffect, useState } from "react";
import { fetchEditScenarioNameCollisions } from "@/lib/api";
import type { ScenarioDocument } from "@/lib/edit-dsl/dsl";
import { AppCheckbox } from "@/components/headless";
import { SelectWithFreetext } from "./SelectWithFreetext";
import type { EditorMeta } from "./StepCard";

type Props = {
  doc: ScenarioDocument;
  rel: string;
  meta: EditorMeta;
  collisions: string[];
  onCollisionsChange: (rels: string[]) => void;
  onChange: (doc: ScenarioDocument) => void;
};

export function ScenarioHeaderForm({
  doc,
  rel,
  meta,
  collisions,
  onCollisionsChange,
  onChange,
}: Props) {
  const [iconOk, setIconOk] = useState<boolean | null>(null);
  const iconSlug = String(doc.icon ?? "").trim();

  useEffect(() => {
    setIconOk(null);
  }, [iconSlug]);

  useEffect(() => {
    const name = String(doc.name ?? "").trim();
    if (!name) {
      onCollisionsChange([]);
      return;
    }
    fetchEditScenarioNameCollisions(rel, name)
      .then(onCollisionsChange)
      .catch(() => onCollisionsChange([]));
  }, [doc.name, rel, onCollisionsChange]);

  const patch = (patch: Partial<ScenarioDocument>) => onChange({ ...doc, ...patch });

  const nodeOpts = ["", ...meta.fsm_nodes];

  return (
    <section className="edit-scenario-header">
      <div className="form-grid-2">
        <label className="field-row">
          <span>name</span>
          <input
            value={String(doc.name ?? "")}
            onChange={(e) => patch({ name: e.target.value })}
          />
          {!String(doc.name ?? "").trim() && (
            <span className="error-banner">Scenario name is required.</span>
          )}
          {collisions.length > 0 && (
            <span className="error-banner">
              Duplicate name — also used by: {collisions.map((c) => `\`${c}\``).join(", ")}
            </span>
          )}
        </label>
        <SelectWithFreetext
          label="node (FSM target before steps)"
          value={String(doc.node ?? "")}
          options={nodeOpts}
          onChange={(v) => {
            const next = { ...doc };
            if (v.trim()) next.node = v;
            else delete next.node;
            onChange(next);
          }}
        />
      </div>

      <label className="field-row">
        <span>cond (root guard, optional)</span>
        <input
          value={String(doc.cond ?? "")}
          placeholder='e.g. active_player == ""'
          onChange={(e) => {
            const next = { ...doc };
            const v = e.target.value.trim();
            if (v) next.cond = v;
            else delete next.cond;
            onChange(next);
          }}
        />
      </label>

      <div className="form-grid-2 edit-scenario-icon-row">
        <label className="field-row">
          <span>icon slug (references/events/event.&lt;slug&gt;.png)</span>
          <input
            value={iconSlug}
            placeholder="e.g. 7-day, first_purchase"
            onChange={(e) => {
              const next = { ...doc };
              const v = e.target.value.trim();
              if (v) next.icon = v;
              else delete next.icon;
              onChange(next);
            }}
          />
        </label>
        <div className="edit-scenario-icon-preview">
          {iconSlug && iconOk !== false && (
            // eslint-disable-next-line @next/next/no-img-element
            <img
              src={`/api/edit-dsl/event-icon?slug=${encodeURIComponent(iconSlug)}`}
              alt=""
              width={64}
              height={64}
              onLoad={() => setIconOk(true)}
              onError={() => setIconOk(false)}
            />
          )}
          {iconSlug && iconOk === false && (
            <span className="muted">No event icon found</span>
          )}
        </div>
      </div>

      <div className="form-grid-4">
        <AppCheckbox
          className="field-row checkbox-row"
          checked={Boolean(doc.enabled)}
          onChange={(checked) => patch({ enabled: checked })}
          label="enabled"
        />
        <AppCheckbox
          className="field-row checkbox-row"
          checked={Boolean(doc.device_level)}
          onChange={(checked) => patch({ device_level: checked })}
          label="device_level"
        />
        <label className="field-row">
          <span>priority (0 = default)</span>
          <input
            type="number"
            min={0}
            step={1000}
            value={Number(doc.priority ?? 0) || ""}
            placeholder="0"
            onChange={(e) => {
              const next = { ...doc };
              const p = parseInt(e.target.value, 10) || 0;
              if (p) next.priority = p;
              else delete next.priority;
              onChange(next);
            }}
          />
        </label>
        <label className="field-row">
          <span>cron (optional)</span>
          <input
            value={String(doc.cron ?? "")}
            placeholder="*/5 * * * *"
            onChange={(e) => {
              const next = { ...doc };
              const v = e.target.value.trim();
              if (v) next.cron = v;
              else delete next.cron;
              onChange(next);
            }}
          />
        </label>
      </div>
    </section>
  );
}
