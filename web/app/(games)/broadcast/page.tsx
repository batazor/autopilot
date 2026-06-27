"use client";

import { Suspense, useCallback, useEffect, useState } from "react";
import {
  FleetContextProvider,
  useFleetOptional,
} from "@/components/FleetContextProvider";
import { PageHeader } from "@/components/PageHeader";
import { MessageEditor } from "@/components/broadcast/MessageEditor";
import { Button, PageLoading, Pill, Spinner, Toggle } from "@/components/ui";
import {
  type BroadcastMessage,
  type EventFlag,
  type SendRecord,
  deleteMessage,
  fetchEventFlags,
  fetchHistory,
  fetchMessages,
  seedDefaults,
  sendNow,
  setMessageEnabled,
} from "@/lib/broadcast-api";

function fmtAgo(ts: number): string {
  const secs = Math.max(0, Date.now() / 1000 - ts);
  if (secs < 90) return "just now";
  if (secs < 3600) return `${Math.round(secs / 60)}m ago`;
  if (secs < 86_400) return `${Math.round(secs / 3600)}h ago`;
  return `${Math.round(secs / 86_400)}d ago`;
}

function scopeTone(scope: string): "ok" | "busy" | "neutral" {
  if (scope === "wos") return "ok";
  if (scope === "kingshot") return "busy";
  return "neutral";
}

function MessageRow({
  m,
  busy,
  onToggle,
  onEdit,
  onDelete,
  onSendNow,
}: {
  m: BroadcastMessage;
  busy: boolean;
  onToggle: (next: boolean) => void;
  onEdit: () => void;
  onDelete: () => void;
  onSendNow: () => void;
}) {
  return (
    <div
      className={`rounded-xl border border-wos-border-subtle bg-wos-surface p-3 ${
        m.enabled ? "" : "opacity-60"
      }`}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-medium text-wos-text">{m.title}</span>
            <Pill tone={m.channel === "world" ? "busy" : "live"}>
              {m.channel === "world" ? "world" : "alliance"}
            </Pill>
            <Pill tone={scopeTone(m.game_scope)}>{m.game_scope}</Pill>
            <Pill tone="neutral">{m.category}</Pill>
            <span className="text-xs text-wos-text-muted">{m.trigger_label}</span>
          </div>
          <p className="mt-1 truncate text-sm text-wos-text-secondary">{m.text}</p>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <Toggle
            checked={m.enabled}
            disabled={busy}
            onChange={onToggle}
            aria-label="Enabled"
          />
          <Button variant="secondary" onClick={onSendNow} title="Post this once on the selected instance">
            Send now
          </Button>
          <Button variant="secondary" onClick={onEdit}>
            Edit
          </Button>
          <Button variant="danger" pending={busy} onClick={onDelete}>
            Delete
          </Button>
        </div>
      </div>
    </div>
  );
}

function BroadcastPageContent() {
  const [messages, setMessages] = useState<BroadcastMessage[] | null>(null);
  const [eventFlags, setEventFlags] = useState<EventFlag[]>([]);
  const [history, setHistory] = useState<SendRecord[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [editing, setEditing] = useState<BroadcastMessage | null | "new">(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [seeding, setSeeding] = useState(false);
  const instanceId = useFleetOptional()?.instanceId ?? "";

  const load = useCallback(() => {
    fetchMessages()
      .then((m) => {
        setMessages(m);
        setError(null);
      })
      .catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)));
    fetchHistory().then(setHistory).catch(() => undefined);
  }, []);

  useEffect(() => {
    load();
    fetchEventFlags("wos").then(setEventFlags).catch(() => undefined);
  }, [load]);

  const onSaved = (saved: BroadcastMessage) => {
    setEditing(null);
    setMessages((prev) => {
      const rest = (prev ?? []).filter((m) => m.id !== saved.id);
      return [saved, ...rest];
    });
  };

  const onToggle = async (m: BroadcastMessage, next: boolean) => {
    setBusyId(m.id);
    try {
      const updated = await setMessageEnabled(m.id, next);
      setMessages((prev) => (prev ?? []).map((x) => (x.id === m.id ? updated : x)));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusyId(null);
    }
  };

  const onDelete = async (m: BroadcastMessage) => {
    setBusyId(m.id);
    try {
      await deleteMessage(m.id);
      setMessages((prev) => (prev ?? []).filter((x) => x.id !== m.id));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusyId(null);
    }
  };

  const onSendNow = async (m: BroadcastMessage) => {
    setError(null);
    setNotice(null);
    if (!instanceId) {
      setError("Pick an instance (top-right) to send a test message to.");
      return;
    }
    try {
      await sendNow(m.id, instanceId);
      setNotice(`Queued "${m.title}" on ${instanceId} — watch the device / history.`);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  const onSeed = async () => {
    setSeeding(true);
    try {
      await seedDefaults();
      load();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSeeding(false);
    }
  };

  return (
    <div className="space-y-4">
      <PageHeader
        title="Alliance broadcast"
        fleet={{ showPlayer: false }}
        actions={
          <div className="flex gap-2">
            <Button variant="secondary" pending={seeding} onClick={onSeed}>
              Add starter templates
            </Button>
            <Button variant="primary" onClick={() => setEditing("new")}>
              New message
            </Button>
          </div>
        }
      >
        Reminders the bot posts into <strong>alliance</strong> or{" "}
        <strong>world</strong> chat — on a schedule or while an in-game event is
        live. Alliance messages post once per alliance; world messages (e.g.
        recruiting) post once across the fleet. All cooldown-deduped; texts can
        be any language.
      </PageHeader>

      {error && (
        <div className="rounded-lg border border-rose-500/40 bg-rose-500/10 p-3 text-sm text-rose-200">
          {error}
        </div>
      )}

      {notice && (
        <div className="rounded-lg border border-emerald-500/40 bg-emerald-500/10 p-3 text-sm text-emerald-200">
          {notice}
        </div>
      )}

      {editing && (
        <MessageEditor
          initial={editing === "new" ? null : editing}
          eventFlags={eventFlags}
          onSaved={onSaved}
          onCancel={() => setEditing(null)}
        />
      )}

      {!messages && !error && (
        <div className="flex items-center gap-2 text-sm text-wos-text-muted">
          <Spinner /> Loading…
        </div>
      )}

      {messages && messages.length === 0 && !editing && (
        <div className="rounded-2xl border border-wos-border-subtle bg-wos-surface p-6 text-sm text-wos-text-muted">
          No messages yet. Click <strong>Add starter templates</strong> for a
          ready-made set, or <strong>New message</strong> to write your own.
        </div>
      )}

      <div className="grid gap-2">
        {(messages ?? []).map((m) => (
          <MessageRow
            key={m.id}
            m={m}
            busy={busyId === m.id}
            onToggle={(next) => onToggle(m, next)}
            onEdit={() => setEditing(m)}
            onDelete={() => onDelete(m)}
            onSendNow={() => onSendNow(m)}
          />
        ))}
      </div>

      {history.length > 0 && (
        <section className="rounded-2xl border border-wos-border-subtle bg-wos-surface p-4">
          <h2 className="mb-2 text-sm font-semibold text-wos-text">Recent posts</h2>
          <ul className="space-y-1 text-sm text-wos-text-secondary">
            {history.slice(0, 20).map((h, i) => (
              <li key={`${h.message_id}-${h.sent_at}-${i}`} className="flex justify-between gap-3">
                <span className="truncate">
                  <span className="text-wos-text-muted">[{h.alliance}]</span> {h.text}
                </span>
                <span className="shrink-0 text-wos-text-muted">{fmtAgo(h.sent_at)}</span>
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}

export default function BroadcastPage() {
  return (
    <Suspense fallback={<PageLoading />}>
      <FleetContextProvider>
        <BroadcastPageContent />
      </FleetContextProvider>
    </Suspense>
  );
}
