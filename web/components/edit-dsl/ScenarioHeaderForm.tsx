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

  /** Set a string field, deleting the key when the trimmed value is empty. */
  const patchStr = (key: keyof ScenarioDocument, raw: string) => {
    const next = { ...doc };
    const v = raw.trim();
    if (v) (next as Record<string, unknown>)[key] = v;
    else delete (next as Record<string, unknown>)[key];
    onChange(next);
  };

  const nodeOpts = ["", ...meta.fsm_nodes];
  const nameEmpty = !String(doc.name ?? "").trim();

  return (
    <section className="edit-scenario-header">
      {/* ── Definition: what the scenario is and when it's eligible ── */}
      <fieldset className="edit-scenario-fieldset">
        <legend className="edit-scenario-fieldset__legend">Definition</legend>

        <div className="form-grid-2">
          <label className="field-row">
            <span>name</span>
            <input
              value={String(doc.name ?? "")}
              placeholder="Scenario name"
              aria-invalid={nameEmpty || collisions.length > 0}
              onChange={(e) => patch({ name: e.target.value })}
            />
            {nameEmpty && <span className="error-banner">Name is required.</span>}
            {collisions.length > 0 && (
              <span className="error-banner">
                Duplicate name — also used by {collisions.map((c) => `\`${c}\``).join(", ")}
              </span>
            )}
          </label>

          <SelectWithFreetext
            label="node"
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
        <p className="field-help">
          <code>node</code> is the FSM screen the bot routes to before running steps.
        </p>

        <label className="field-row">
          <span>cond</span>
          <input
            value={String(doc.cond ?? "")}
            placeholder='e.g. active_player == ""'
            onChange={(e) => patchStr("cond", e.target.value)}
          />
          <span className="field-row__hint">
            Root guard — the scenario is skipped unless this is true.
          </span>
        </label>

        <div className="edit-scenario-icon-row">
          <label className="field-row min-w-0 flex-1">
            <span>icon</span>
            <input
              value={iconSlug}
              placeholder="e.g. 7-day, first_purchase"
              onChange={(e) => patchStr("icon", e.target.value)}
            />
            <span className="field-row__hint">
              Slug → <code>references/events/event.&lt;slug&gt;.png</code>
            </span>
          </label>
          {iconSlug && (
            <div className="edit-scenario-icon-preview">
              {iconOk !== false ? (
                // eslint-disable-next-line @next/next/no-img-element
                <img
                  src={`/api/edit-dsl/event-icon?slug=${encodeURIComponent(iconSlug)}`}
                  alt=""
                  width={56}
                  height={56}
                  onLoad={() => setIconOk(true)}
                  onError={() => setIconOk(false)}
                />
              ) : (
                <span className="muted px-1 text-center text-[10px] leading-tight">
                  not found
                </span>
              )}
            </div>
          )}
        </div>
      </fieldset>

      {/* ── Run behavior: scheduling & dispatch flags ── */}
      <fieldset className="edit-scenario-fieldset">
        <legend className="edit-scenario-fieldset__legend">Run behavior</legend>

        <div className="form-grid-2">
          <div className="edit-scenario-toggle">
            <AppCheckbox
              inline
              checked={Boolean(doc.enabled)}
              onChange={(checked) => patch({ enabled: checked })}
              label="enabled"
            />
            <span className="field-row__hint">Scenario is allowed to run.</span>
          </div>
          <div className="edit-scenario-toggle">
            <AppCheckbox
              inline
              checked={Boolean(doc.device_level)}
              onChange={(checked) => patch({ device_level: checked })}
              label="device_level"
            />
            <span className="field-row__hint">Runs per device, not per account.</span>
          </div>
        </div>

        <div className="form-grid-2">
          <label className="field-row">
            <span>priority</span>
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
            <span className="field-row__hint">Higher runs first · 0 = default.</span>
          </label>

          <label className="field-row">
            <span>cron</span>
            <input
              value={String(doc.cron ?? "")}
              placeholder="*/5 * * * *"
              onChange={(e) => patchStr("cron", e.target.value)}
            />
            <span className="field-row__hint">Schedule · blank = manual only.</span>
          </label>
        </div>
      </fieldset>
    </section>
  );
}
