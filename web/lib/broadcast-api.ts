// Typed client for the alliance-broadcast catalog API (FastAPI /api/broadcast).

export type GameScope = "wos" | "kingshot" | "all";
export type TriggerKind = "cron" | "event";
export type Channel = "alliance" | "world";

export interface BroadcastMessage {
  id: string;
  title: string;
  text: string;
  category: string;
  game_scope: GameScope;
  channel: Channel;
  trigger_kind: TriggerKind;
  cron: string;
  cond: string;
  cooldown_minutes: number;
  priority: number;
  enabled: boolean;
  created_at: number;
  updated_at: number;
  trigger_label: string;
}

export interface EventFlag {
  flag: string;
  label: string;
}

export interface SendRecord {
  message_id: string;
  game: string;
  alliance: string;
  fid: string;
  text: string;
  sent_at: number;
}

/** Editable subset the editor submits (id omitted ⇒ create). */
export type MessageDraft = Omit<
  BroadcastMessage,
  "created_at" | "updated_at" | "trigger_label" | "id"
> & { id?: string };

async function asJson<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let detail = `${res.status}`;
    try {
      const body = (await res.json()) as { detail?: string };
      if (body?.detail) detail = body.detail;
    } catch {
      /* ignore */
    }
    throw new Error(detail);
  }
  return res.json() as Promise<T>;
}

export async function fetchMessages(game?: string): Promise<BroadcastMessage[]> {
  const qs = game ? `?game=${encodeURIComponent(game)}` : "";
  const data = await asJson<{ messages: BroadcastMessage[] }>(
    await fetch(`/api/broadcast/messages${qs}`, { cache: "no-store" }),
  );
  return data.messages;
}

export async function upsertMessage(draft: MessageDraft): Promise<BroadcastMessage> {
  const data = await asJson<{ message: BroadcastMessage }>(
    await fetch(`/api/broadcast/messages`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(draft),
    }),
  );
  return data.message;
}

export async function setMessageEnabled(
  id: string,
  enabled: boolean,
): Promise<BroadcastMessage> {
  const data = await asJson<{ message: BroadcastMessage }>(
    await fetch(`/api/broadcast/messages/${encodeURIComponent(id)}/enabled`, {
      method: "PATCH",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ enabled }),
    }),
  );
  return data.message;
}

export async function deleteMessage(id: string): Promise<void> {
  await asJson<{ deleted: string }>(
    await fetch(`/api/broadcast/messages/${encodeURIComponent(id)}`, {
      method: "DELETE",
    }),
  );
}

export async function seedDefaults(): Promise<string[]> {
  const data = await asJson<{ added: string[] }>(
    await fetch(`/api/broadcast/seed`, { method: "POST" }),
  );
  return data.added;
}

export async function fetchHistory(game?: string, alliance?: string): Promise<SendRecord[]> {
  const params = new URLSearchParams();
  if (game) params.set("game", game);
  if (alliance) params.set("alliance", alliance);
  const qs = params.toString();
  const data = await asJson<{ sends: SendRecord[] }>(
    await fetch(`/api/broadcast/history${qs ? `?${qs}` : ""}`, { cache: "no-store" }),
  );
  return data.sends;
}

export async function fetchEventFlags(game = "wos"): Promise<EventFlag[]> {
  const data = await asJson<{ flags: EventFlag[] }>(
    await fetch(`/api/broadcast/event-flags?game=${encodeURIComponent(game)}`, {
      cache: "no-store",
    }),
  );
  return data.flags;
}
