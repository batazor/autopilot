"use client";

import { useState } from "react";
import { Button } from "@/components/ui";
import {
  type BroadcastMessage,
  type Channel,
  type EventFlag,
  type GameScope,
  type MessageDraft,
  type TriggerKind,
  upsertMessage,
} from "@/lib/broadcast-api";

const SCOPES: GameScope[] = ["all", "wos", "kingshot"];
const CHANNELS: { value: Channel; label: string; hint: string }[] = [
  { value: "alliance", label: "Alliance chat", hint: "One account per alliance posts" },
  { value: "world", label: "World chat", hint: "Global — e.g. recruiting" },
];
const CATEGORIES = ["event", "tip", "daily", "custom"];
// Cron presets restricted to the two scheduler-supported shapes.
const CRON_PRESETS: { value: string; label: string }[] = [
  { value: "*/15 * * * *", label: "every 15 min" },
  { value: "*/30 * * * *", label: "every 30 min" },
  { value: "0 */1 * * *", label: "every hour" },
  { value: "0 */6 * * *", label: "every 6 hours" },
  { value: "0 */12 * * *", label: "every 12 hours" },
];

const MAX_LEN = 200;

const BLANK: MessageDraft = {
  title: "",
  text: "",
  category: "custom",
  game_scope: "all",
  channel: "alliance",
  trigger_kind: "cron",
  cron: "0 */12 * * *",
  cond: "",
  cooldown_minutes: 0,
  priority: 100,
  enabled: true,
};

function toDraft(m: BroadcastMessage): MessageDraft {
  const { created_at: _c, updated_at: _u, trigger_label: _t, ...rest } = m;
  return rest;
}

export function MessageEditor({
  initial,
  eventFlags,
  onSaved,
  onCancel,
}: {
  /** Existing message to edit, or `null`/undefined to create a new one. */
  initial?: BroadcastMessage | null;
  eventFlags: EventFlag[];
  onSaved: (m: BroadcastMessage) => void;
  onCancel: () => void;
}) {
  const [draft, setDraft] = useState<MessageDraft>(initial ? toDraft(initial) : BLANK);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const set = <K extends keyof MessageDraft>(key: K, value: MessageDraft[K]) =>
    setDraft((d) => ({ ...d, [key]: value }));

  const submit = async () => {
    setSaving(true);
    setError(null);
    try {
      const saved = await upsertMessage(draft);
      onSaved(saved);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  };

  const labelCls = "block text-xs font-medium uppercase tracking-wide text-wos-text-muted mb-1";
  const inputCls =
    "w-full rounded-lg border border-wos-border-subtle bg-wos-surface px-2.5 py-1.5 text-sm text-wos-text focus:outline-none focus:ring-2 focus:ring-emerald-400/50";

  return (
    <div className="rounded-2xl border border-wos-border-subtle bg-wos-surface p-4">
      <h3 className="mb-3 text-sm font-semibold text-wos-text">
        {initial ? `Edit: ${initial.title}` : "New message"}
      </h3>

      <div className="grid gap-3">
        <div>
          <label className={labelCls}>Title</label>
          <input
            className={inputCls}
            value={draft.title}
            onChange={(e) => set("title", e.target.value)}
            placeholder="Short label (dashboard only)"
          />
        </div>

        <div>
          <label className={labelCls}>
            Message text · {draft.text.length}/{MAX_LEN}
          </label>
          <textarea
            className={`${inputCls} min-h-[72px] resize-y`}
            value={draft.text}
            maxLength={MAX_LEN}
            onChange={(e) => set("text", e.target.value)}
            placeholder="The exact message posted to alliance chat (any language)"
          />
        </div>

        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          <div>
            <label className={labelCls}>Category</label>
            <select
              className={inputCls}
              value={draft.category}
              onChange={(e) => set("category", e.target.value)}
            >
              {CATEGORIES.map((c) => (
                <option key={c} value={c}>
                  {c}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label className={labelCls}>Game</label>
            <select
              className={inputCls}
              value={draft.game_scope}
              onChange={(e) => set("game_scope", e.target.value as GameScope)}
            >
              {SCOPES.map((s) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label className={labelCls}>Priority</label>
            <input
              type="number"
              className={inputCls}
              value={draft.priority}
              onChange={(e) => set("priority", Number(e.target.value))}
            />
          </div>
          <div>
            <label className={labelCls}>Cooldown (min)</label>
            <input
              type="number"
              min={0}
              className={inputCls}
              value={draft.cooldown_minutes}
              onChange={(e) => set("cooldown_minutes", Number(e.target.value))}
            />
          </div>
        </div>

        <div>
          <label className={labelCls}>Chat channel</label>
          <div className="flex flex-wrap gap-2">
            {CHANNELS.map((c) => (
              <button
                key={c.value}
                type="button"
                onClick={() => set("channel", c.value)}
                title={c.hint}
                className={`rounded-lg border px-3 py-1.5 text-sm ${
                  draft.channel === c.value
                    ? "border-emerald-400/60 bg-emerald-500/15 text-wos-text"
                    : "border-wos-border-subtle text-wos-text-muted"
                }`}
              >
                {c.label}
              </button>
            ))}
          </div>
          <p className="mt-1 text-xs text-wos-text-muted">
            {draft.channel === "world"
              ? "Posts to world/global chat — one account across the fleet posts it."
              : "Posts to alliance chat — one eligible account per alliance posts it."}
          </p>
        </div>

        <div>
          <label className={labelCls}>Trigger</label>
          <div className="flex gap-2">
            {(["cron", "event"] as TriggerKind[]).map((k) => (
              <button
                key={k}
                type="button"
                onClick={() => set("trigger_kind", k)}
                className={`rounded-lg border px-3 py-1.5 text-sm capitalize ${
                  draft.trigger_kind === k
                    ? "border-emerald-400/60 bg-emerald-500/15 text-wos-text"
                    : "border-wos-border-subtle text-wos-text-muted"
                }`}
              >
                {k === "cron" ? "Schedule (cron)" : "Event"}
              </button>
            ))}
          </div>
        </div>

        {draft.trigger_kind === "cron" ? (
          <div>
            <label className={labelCls}>Schedule</label>
            <select
              className={inputCls}
              value={draft.cron}
              onChange={(e) => set("cron", e.target.value)}
            >
              {CRON_PRESETS.map((p) => (
                <option key={p.value} value={p.value}>
                  {p.label}
                </option>
              ))}
            </select>
          </div>
        ) : (
          <div>
            <label className={labelCls}>Post while this event is active</label>
            <select
              className={inputCls}
              value={draft.cond}
              onChange={(e) => set("cond", e.target.value)}
            >
              <option value="">— pick an event —</option>
              {eventFlags.map((f) => (
                <option key={f.flag} value={`${f.flag} == 1`}>
                  {f.label} ({f.flag})
                </option>
              ))}
            </select>
            <input
              className={`${inputCls} mt-2`}
              value={draft.cond}
              onChange={(e) => set("cond", e.target.value)}
              placeholder="or a custom condition, e.g. event_bear_hunt == 1"
            />
          </div>
        )}

        {error && (
          <div className="rounded-lg border border-rose-500/40 bg-rose-500/10 p-2 text-sm text-rose-200">
            {error}
          </div>
        )}

        <div className="flex justify-end gap-2">
          <Button variant="secondary" onClick={onCancel}>
            Cancel
          </Button>
          <Button variant="primary" pending={saving} onClick={submit}>
            {initial ? "Save changes" : "Create"}
          </Button>
        </div>
      </div>
    </div>
  );
}
