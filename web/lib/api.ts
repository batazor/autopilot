import type {
  AdbResetDisplayResult,
  AdbStatus,
  MinicapStatus,
  MinicapInstallResult,
  MinitouchStatus,
  MinitouchInstallResult,
  DeviceBackendUpdate,
  AnalyzeIssue,
  BalanceFileMeta,
  OptimizerMeta,
  OptimizerSolveResult,
  EditableModuleEntry,
  ScenarioFileEntry,
  ScenarioTreeNode,
  GalleryItem,
  LicenseFingerprint,
  LicenseImportResult,
  LicenseIssueRequest,
  LicenseIssueResult,
  LicenseStatus,
  ModuleRow,
  PlayerAssignment,
  ScenarioRow,
} from "./config-pages";
import type {
  AreaRegionProbeResult,
  ClickApprovalView,
  InstanceDetail,
  LabelingDocument,
  LabelingReferenceMeta,
  LabelingScopeOption,
  LabelingStaleCrop,
  NotificationEvent,
  OverlayTestResult,
  RoutesGraphResponse,
  RoutesNodeDetails,
  BotStatusView,
  HealthView,
  OverviewView,
  PlayerStateView,
  PlayerPersistedView,
  PlayerStatsView,
  AllianceStatsView,
  CenturySyncResult,
  BuildingLevelRow,
  HeroStateRow,
  InstanceUnchangedResponse,
  QueueUnchangedResponse,
  QueueView,
} from "./types";
import type { GiftCodesView, WikiDetail, WikiEntrySummary, WikiScope } from "./wiki";

const base = "";

async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${base}${path}`, { cache: "no-store", ...init });
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(`${path}: ${res.status}${text ? ` — ${text}` : ""}`);
  }
  return res.json() as Promise<T>;
}

export async function fetchHealth(): Promise<HealthView> {
  const res = await fetch(`${base}/health`, { cache: "no-store" });
  if (!res.ok) {
    throw new Error(`health: ${res.status}`);
  }
  return res.json() as Promise<HealthView>;
}

export async function fetchBotStatus(): Promise<BotStatusView> {
  return apiFetch<BotStatusView>("/api/dev/bot");
}

export async function startLocalBot(): Promise<BotStatusView> {
  return apiFetch<BotStatusView>("/api/dev/bot/start", { method: "POST" });
}

export async function fetchInstances(): Promise<string[]> {
  const data = await apiFetch<{ instances: string[] }>("/api/instances");
  return data.instances;
}

export async function fetchOverview(): Promise<OverviewView> {
  return apiFetch<OverviewView>("/api/overview");
}

export async function toggleInstancePause(instanceId: string): Promise<{ cmd: string }> {
  return apiFetch<{ instance_id: string; cmd: string }>(
    `/api/instances/${encodeURIComponent(instanceId)}/pause-toggle`,
    { method: "POST" },
  );
}

export async function fetchInstanceDetail(
  instanceId: string,
  options?: { ifRevision?: string },
): Promise<InstanceDetail | InstanceUnchangedResponse> {
  const params = new URLSearchParams();
  if (options?.ifRevision) params.set("if_revision", options.ifRevision);
  const qs = params.toString();
  const path = `/api/instances/${encodeURIComponent(instanceId)}`;
  return apiFetch<InstanceDetail | InstanceUnchangedResponse>(
    qs ? `${path}?${qs}` : path,
  );
}

export function instancePreviewUrl(
  instanceId: string,
  /** Rolling preview mtime (or other stable revision). Omit only for one-off loads. */
  cacheKey?: number | string | null,
): string {
  const q = new URLSearchParams({
    t: String(cacheKey ?? Date.now()),
  });
  return `${base}/api/instances/${encodeURIComponent(instanceId)}/preview?${q}`;
}

export async function postInstanceCommand(
  instanceId: string,
  body: {
    cmd: "pause" | "resume" | "restart" | "switch_player" | "run_task";
    player_id?: string;
    task_type?: string;
  },
): Promise<void> {
  await apiFetch<{ ok: boolean }>(
    `/api/instances/${encodeURIComponent(instanceId)}/commands`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    },
  );
}

export async function fetchQueue(options?: {
  ifRevision?: string;
}): Promise<QueueView | QueueUnchangedResponse> {
  const params = new URLSearchParams();
  if (options?.ifRevision) params.set("if_revision", options.ifRevision);
  const qs = params.toString();
  return apiFetch<QueueView | QueueUnchangedResponse>(
    qs ? `/api/queue?${qs}` : "/api/queue",
  );
}

export async function runQueueTaskNow(taskId: string): Promise<boolean> {
  const data = await apiFetch<{ ok: boolean }>("/api/queue/run-now", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ task_id: taskId }),
  });
  return data.ok;
}

export async function removeQueueTasks(taskIds: string[]): Promise<number> {
  const data = await apiFetch<{ removed: number }>("/api/queue/remove", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ task_ids: taskIds }),
  });
  return data.removed;
}

export async function rescheduleQueueTask(
  taskId: string,
  scheduledAt: number,
): Promise<boolean> {
  const data = await apiFetch<{ ok: boolean }>("/api/queue/reschedule", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ task_id: taskId, scheduled_at: scheduledAt }),
  });
  return data.ok;
}

export async function fetchPlayers(): Promise<string[]> {
  const data = await apiFetch<{ players: string[] }>("/api/players");
  return data.players;
}

export async function fetchSuggestedPlayer(
  instanceId: string,
): Promise<string> {
  const q = new URLSearchParams({ instance_id: instanceId });
  const data = await apiFetch<{ player_id: string }>(
    `/api/players/suggest?${q}`,
  );
  return data.player_id;
}

export async function fetchPlayerState(playerId: string): Promise<PlayerStateView> {
  return apiFetch<PlayerStateView>(
    `/api/players/${encodeURIComponent(playerId)}/state`,
  );
}

export async function fetchPlayerPersisted(
  playerId: string,
): Promise<PlayerPersistedView> {
  return apiFetch<PlayerPersistedView>(
    `/api/players/${encodeURIComponent(playerId)}/persisted`,
  );
}

export async function fetchPlayerStats(playerId: string): Promise<PlayerStatsView> {
  return apiFetch<PlayerStatsView>(
    `/api/players/${encodeURIComponent(playerId)}/stats`,
  );
}

export async function fetchAlliances(): Promise<string[]> {
  const res = await apiFetch<{ alliances: string[] }>("/api/alliances");
  return res.alliances;
}

export async function fetchAllianceStats(
  allianceName: string,
): Promise<AllianceStatsView> {
  return apiFetch<AllianceStatsView>(
    `/api/alliances/${encodeURIComponent(allianceName)}/stats`,
  );
}

export async function syncPlayerFromCentury(
  playerId: string,
): Promise<CenturySyncResult> {
  return apiFetch<CenturySyncResult>(
    `/api/players/${encodeURIComponent(playerId)}/century-sync`,
    { method: "POST" },
  );
}

export interface DeletePlayerResult {
  ok: boolean;
  player_id: string;
  sqlite: Record<string, number>;
  redis_keys_deleted: number;
}

export async function deletePlayer(
  playerId: string,
): Promise<DeletePlayerResult> {
  return apiFetch<DeletePlayerResult>(
    `/api/players/${encodeURIComponent(playerId)}`,
    { method: "DELETE" },
  );
}

export async function fetchClickApproval(
  instanceId: string,
  source: "capture" | "live" = "capture",
): Promise<ClickApprovalView> {
  const q = new URLSearchParams({ source });
  return apiFetch<ClickApprovalView>(
    `/api/instances/${encodeURIComponent(instanceId)}/click-approval?${q}`,
  );
}

export function clickApprovalImageUrl(
  instanceId: string,
  source: "capture" | "live" = "capture",
): string {
  // Cache-busting belongs to the caller (e.g. via &tick=<state>). Embedding
  // Date.now() here would re-render the <img> on every parent render and
  // trigger a refetch storm even when nothing about the image changed.
  const q = new URLSearchParams({ source });
  return `${base}/api/instances/${encodeURIComponent(instanceId)}/click-approval/image?${q}`;
}

export async function submitDecision(
  instanceId: string,
  decision: "approve" | "reject" | "skip",
): Promise<boolean> {
  const data = await apiFetch<{ ok: boolean }>(
    `/api/instances/${encodeURIComponent(instanceId)}/click-approval/decision`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ decision }),
    },
  );
  return data.ok;
}

export async function setApprovalEnabled(
  instanceId: string,
  enabled: boolean,
): Promise<boolean> {
  const data = await apiFetch<{ ok: boolean; enabled: boolean }>(
    `/api/instances/${encodeURIComponent(instanceId)}/click-approval/enabled`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ enabled }),
    },
  );
  return data.enabled;
}

export async function clearPendingApproval(instanceId: string): Promise<boolean> {
  const data = await apiFetch<{ ok: boolean; cleared: boolean }>(
    `/api/instances/${encodeURIComponent(instanceId)}/click-approval/clear-pending`,
    { method: "POST" },
  );
  return data.cleared;
}

export async function resetCurrentScreen(instanceId: string): Promise<void> {
  await apiFetch<{ ok: boolean }>(
    `/api/instances/${encodeURIComponent(instanceId)}/reset-current-screen`,
    { method: "POST" },
  );
}

export async function clearQueueAll(): Promise<number> {
  const data = await apiFetch<{ removed: number }>("/api/queue/clear-all", {
    method: "POST",
  });
  return data.removed;
}

export async function fetchNotifications(
  instanceId: string,
  seenIds: Iterable<string>,
  maxAgeSeconds = 30.0,
): Promise<NotificationEvent[]> {
  const q = new URLSearchParams();
  q.set("max_age_seconds", String(maxAgeSeconds));
  for (const id of seenIds) {
    if (id) q.append("seen_id", id);
  }
  const data = await apiFetch<{ items: NotificationEvent[] }>(
    `/api/instances/${encodeURIComponent(instanceId)}/notifications?${q}`,
  );
  return data.items;
}

export async function fetchOverlayTest(
  instanceId: string,
  options: {
    onlyCurrentScreen?: boolean;
    ignoreScreenGate?: boolean;
    hasActivePlayer?: boolean;
    detailedAnalysis?: boolean;
    previewSource?: "live" | "reference";
    previewRel?: string;
  } = {},
): Promise<OverlayTestResult> {
  const q = new URLSearchParams();
  if (options.onlyCurrentScreen) q.set("onlyCurrentScreen", "true");
  if (options.ignoreScreenGate) q.set("ignoreScreenGate", "true");
  if (options.hasActivePlayer === false) q.set("hasActivePlayer", "false");
  if (options.detailedAnalysis) q.set("detailedAnalysis", "true");
  if (options.previewSource === "reference" && options.previewRel?.trim()) {
    q.set("previewSource", "reference");
    q.set("previewRel", options.previewRel.trim());
  }
  const suffix = q.size ? `?${q}` : "";
  return apiFetch<OverlayTestResult>(
    `/api/instances/${encodeURIComponent(instanceId)}/overlay-test${suffix}`,
  );
}

export function overlayTestImageUrl(
  instanceId: string,
  cacheKey?: number | string | null,
  options: {
    previewSource?: "live" | "reference";
    previewRel?: string;
  } = {},
): string {
  const q = new URLSearchParams({
    t: String(cacheKey ?? Date.now()),
  });
  if (options.previewSource === "reference" && options.previewRel?.trim()) {
    q.set("previewSource", "reference");
    q.set("previewRel", options.previewRel.trim());
  }
  return `${base}/api/instances/${encodeURIComponent(instanceId)}/overlay-test/image?${q}`;
}

export async function fetchAreaRegionProbe(
  instanceId: string,
  options: { region?: string; threshold?: number } = {},
): Promise<AreaRegionProbeResult> {
  const q = new URLSearchParams();
  if (options.region) q.set("region", options.region);
  if (options.threshold != null) q.set("threshold", String(options.threshold));
  const suffix = q.size ? `?${q}` : "";
  return apiFetch<AreaRegionProbeResult>(
    `/api/instances/${encodeURIComponent(instanceId)}/area-region-probe${suffix}`,
  );
}

function labelingScopeQuery(
  scope: string,
  extra?: Record<string, string> | null,
): string {
  const q = new URLSearchParams({ scope });
  if (extra) {
    for (const [k, v] of Object.entries(extra)) {
      if (v) q.set(k, v);
    }
  }
  const s = q.toString();
  return s ? `?${s}` : "";
}

export async function fetchLabelingScopes(): Promise<LabelingScopeOption[]> {
  const data = await apiFetch<{ scopes: LabelingScopeOption[] }>(
    "/api/labeling/scopes",
  );
  return data.scopes;
}

export async function fetchLabelingScreenIds(
  scope: string,
  current = "",
): Promise<string[]> {
  const q = new URLSearchParams({ scope });
  if (current.trim()) q.set("current", current.trim());
  const data = await apiFetch<{ screen_ids: string[] }>(
    `/api/labeling/screen-ids?${q}`,
  );
  return data.screen_ids;
}

export async function fetchLabelingReferences(
  scope: string,
): Promise<LabelingReferenceMeta[]> {
  const data = await apiFetch<{ references: LabelingReferenceMeta[] }>(
    `/api/labeling/references${labelingScopeQuery(scope)}`,
  );
  return data.references;
}

export async function fetchLabelingStaleCrops(scope: string): Promise<{
  count: number;
  stale: LabelingStaleCrop[];
}> {
  return apiFetch(`/api/labeling/stale-crops${labelingScopeQuery(scope)}`);
}

function labelingRefPath(refRel: string): string {
  return refRel
    .replace(/^\/+/, "")
    .split("/")
    .map(encodeURIComponent)
    .join("/");
}

/** Stable URL for Konva/img — pass ``cacheKey`` (e.g. imageNonce) to bust cache after refresh/capture. */
export function labelingImageUrl(refRel: string, cacheKey?: number | string): string {
  const q = new URLSearchParams();
  if (cacheKey != null && cacheKey !== "") {
    q.set("n", String(cacheKey));
  }
  const qs = q.toString();
  return `${base}/api/labeling/references/${labelingRefPath(refRel)}/image${qs ? `?${qs}` : ""}`;
}

export async function fetchLabelingDocument(
  refRel: string,
  scope: string,
  version?: string | null,
): Promise<LabelingDocument> {
  const extra: Record<string, string> = {};
  if (version) extra.version = version;
  return apiFetch<LabelingDocument>(
    `/api/labeling/references/${labelingRefPath(refRel)}${labelingScopeQuery(scope, extra)}`,
  );
}

export async function fetchRoutesGraph(params: {
  from?: string;
  to?: string;
  focus?: string;
  view?: string;
  hub_depth?: number;
}): Promise<RoutesGraphResponse> {
  const q = new URLSearchParams();
  if (params.from) q.set("from", params.from);
  if (params.to) q.set("to", params.to);
  if (params.focus) q.set("focus", params.focus);
  if (params.view) q.set("view", params.view);
  if (params.hub_depth != null) q.set("hub_depth", String(params.hub_depth));
  const suffix = q.size ? `?${q}` : "";
  return apiFetch<RoutesGraphResponse>(`/api/routes/graph${suffix}`);
}

export async function fetchRoutesEdges(
  query = "",
  statuses: string[] = ["static tap", "dynamic tap"],
): Promise<{ edges: Array<Record<string, string>>; total: number; shown: number }> {
  const q = new URLSearchParams();
  if (query) q.set("q", query);
  for (const s of statuses) q.append("status", s);
  const suffix = q.size ? `?${q}` : "";
  return apiFetch(`/api/routes/edges${suffix}`);
}

export async function fetchRoutesNode(nodeId: string): Promise<RoutesNodeDetails> {
  return apiFetch<RoutesNodeDetails>(
    `/api/routes/nodes/${encodeURIComponent(nodeId)}`,
  );
}

export type LabelingSaveRegionsResult = {
  ok: boolean;
  region_renames_synced?: { from: string; to: string; analyze?: boolean }[];
  crops_written_count?: number;
  crop_warnings?: string[];
};

export async function saveLabelingRegions(
  refRel: string,
  scope: string,
  regions: Record<string, unknown>[],
  version?: string | null,
  screenId?: string | null,
): Promise<LabelingSaveRegionsResult> {
  return apiFetch<LabelingSaveRegionsResult>(
    `/api/labeling/references/${labelingRefPath(refRel)}${labelingScopeQuery(scope)}`,
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        regions,
        version: version ?? null,
        screen_id: screenId ?? null,
      }),
    },
  );
}

export async function importLabelingPng(
  instanceId: string,
  scope: string,
  file: File,
): Promise<{ ok: boolean; ref: string }> {
  const fd = new FormData();
  fd.append("instance_id", instanceId);
  fd.append("scope", scope);
  fd.append("file", file);
  const res = await fetch(`${base}/api/labeling/import-png`, {
    method: "POST",
    body: fd,
  });
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(
      `/api/labeling/import-png: ${res.status}${text ? ` — ${text}` : ""}`,
    );
  }
  return res.json() as Promise<{ ok: boolean; ref: string }>;
}

export async function captureLabelingScreenshot(
  instanceId: string,
  scope: string,
): Promise<{ ok: boolean; ref: string }> {
  return apiFetch(`/api/labeling/capture${labelingScopeQuery(scope)}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ instance_id: instanceId }),
  });
}

export async function refreshLabelingReference(
  refRel: string,
  instanceId: string,
  scope: string,
): Promise<{ ok: boolean; ref: string }> {
  return apiFetch(`/api/labeling/refresh${labelingScopeQuery(scope)}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ref: refRel, instance_id: instanceId }),
  });
}

export async function discardLabelingCapture(
  refRel: string,
  scope: string,
): Promise<void> {
  const q = new URLSearchParams({ ref: refRel, scope });
  await apiFetch(`/api/labeling/capture?${q}`, { method: "DELETE" });
}

export async function exportLabelingCrops(
  scope: string,
): Promise<{
  ok: boolean;
  written_count: number;
  written: string[];
  warnings: string[];
  truncated: boolean;
}> {
  return apiFetch(`/api/labeling/crops${labelingScopeQuery(scope)}`, {
    method: "POST",
  });
}

export async function promoteLabelingReference(
  refRel: string,
  basename: string,
  instanceId: string,
  scope: string,
  opts?: { regions?: Record<string, unknown>[]; screenId?: string },
): Promise<{ ok: boolean; ref: string; screen_id: string; message: string }> {
  return apiFetch(`/api/labeling/promote${labelingScopeQuery(scope)}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      ref: refRel,
      basename,
      instance_id: instanceId,
      regions: opts?.regions ?? null,
      screen_id: opts?.screenId ?? null,
    }),
  });
}

export async function renameLabelingReference(
  refRel: string,
  basename: string,
  instanceId: string,
  scope: string,
): Promise<{ ok: boolean; ref: string; message: string }> {
  return apiFetch(`/api/labeling/rename${labelingScopeQuery(scope)}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ref: refRel, basename, instance_id: instanceId }),
  });
}

export async function suggestLabelingVersionId(
  refRel: string,
  scope: string,
): Promise<{ suggested_id: string }> {
  return apiFetch(
    `/api/labeling/versions/suggest${labelingScopeQuery(scope, { ref: refRel })}`,
  );
}

export async function addLabelingVersion(
  refRel: string,
  versionId: string,
  cond: string,
  scope: string,
): Promise<{ ok: boolean; version_id: string }> {
  return apiFetch(`/api/labeling/versions${labelingScopeQuery(scope)}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ref: refRel, version_id: versionId, cond }),
  });
}

export async function updateLabelingVersionCond(
  refRel: string,
  versionId: string,
  cond: string,
  scope: string,
): Promise<void> {
  await apiFetch(
    `/api/labeling/versions/${encodeURIComponent(versionId)}${labelingScopeQuery(scope)}`,
    {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ref: refRel, cond }),
    },
  );
}

export async function deleteLabelingVersion(
  refRel: string,
  versionId: string,
  scope: string,
): Promise<void> {
  await apiFetch(
    `/api/labeling/versions/${encodeURIComponent(versionId)}${labelingScopeQuery(scope, { ref: refRel })}`,
    { method: "DELETE" },
  );
}

export async function syncLabelingVersionRegions(
  refRel: string,
  versionId: string,
  scope: string,
): Promise<{ added: number; skipped: number }> {
  return apiFetch(
    `/api/labeling/versions/${encodeURIComponent(versionId)}/sync-regions${labelingScopeQuery(scope)}`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ref: refRel }),
    },
  );
}

export async function bindLabelingVersionOcr(
  refRel: string,
  versionId: string,
  ocr: string | null,
  scope: string,
): Promise<void> {
  await apiFetch(
    `/api/labeling/versions/${encodeURIComponent(versionId)}/ocr${labelingScopeQuery(scope)}`,
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ref: refRel, ocr }),
    },
  );
}

export async function fetchGiftCodes(q = ""): Promise<GiftCodesView> {
  const params = q ? `?q=${encodeURIComponent(q)}` : "";
  return apiFetch<GiftCodesView>(`/api/gift-codes${params}`);
}

export async function scrapeGiftCodes(): Promise<{ ok: boolean; new_codes: string[]; count: number }> {
  return apiFetch("/api/gift-codes/scrape", { method: "POST" });
}

export async function redeemGiftCodes(): Promise<{ ok: boolean }> {
  return apiFetch("/api/gift-codes/redeem", { method: "POST" });
}

export async function fetchWikiScopes(): Promise<WikiScope[]> {
  const data = await apiFetch<{ scopes: WikiScope[] }>("/api/wiki/scopes");
  return data.scopes;
}

export async function fetchWikiEntries(
  entity: "buildings" | "heroes" | "items",
  scope: string,
  q = "",
): Promise<{ entries: WikiEntrySummary[]; count: number }> {
  const params = new URLSearchParams({ scope });
  if (q) params.set("q", q);
  return apiFetch(`/api/wiki/${entity}?${params}`);
}

export function wikiIconUrl(entity: string, id: string): string {
  return `/api/wiki/${entity}/${encodeURIComponent(id)}/icon`;
}

export async function fetchWikiDetail(
  entity: "buildings" | "heroes" | "items",
  id: string,
  scope: string,
): Promise<WikiDetail> {
  const params = new URLSearchParams({ scope });
  return apiFetch<WikiDetail>(`/api/wiki/${entity}/${encodeURIComponent(id)}?${params}`);
}

export async function fetchWikiGearList(): Promise<{
  entries: Array<{ id: string; title: string; file: string }>;
  missing_dir: boolean;
}> {
  return apiFetch("/api/wiki/gear");
}

export async function fetchWikiGearDetail(gearId: string): Promise<{
  id: string;
  file: string;
  body: Record<string, unknown>;
}> {
  return apiFetch(`/api/wiki/gear/${encodeURIComponent(gearId)}`);
}

export type WikiFaqItem = {
  key: string;
  label: string;
  script: string;
  args?: string[];
};

export type WikiSyncEvent =
  | { type: "start"; key: string; label: string; command: string[]; progress_total_hint?: number }
  | { type: "line"; text: string }
  | { type: "progress"; done: number; total: number }
  | {
      type: "done";
      exit_code: number;
      elapsed: number;
      summary: string;
      done: number;
      total: number;
      command: string[];
    }
  | { type: "error"; message: string };

export async function fetchWikiFaq(): Promise<{
  title: string;
  sections: Array<{
    heading: string;
    text?: string;
    items?: WikiFaqItem[];
  }>;
}> {
  return apiFetch("/api/wiki/faq");
}

export async function runWikiSync(
  scriptKey: string,
  onEvent: (ev: WikiSyncEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const res = await fetch(`${base}/api/wiki/sync/${encodeURIComponent(scriptKey)}`, {
    method: "POST",
    cache: "no-store",
    signal,
  });
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(`/api/wiki/sync/${scriptKey}: ${res.status}${text ? ` — ${text}` : ""}`);
  }
  const body = res.body;
  if (!body) {
    throw new Error("sync stream: empty response body");
  }
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    const lines = buf.split("\n");
    buf = lines.pop() ?? "";
    for (const line of lines) {
      const trimmed = line.trim();
      if (!trimmed) continue;
      onEvent(JSON.parse(trimmed) as WikiSyncEvent);
    }
  }
  const tail = buf.trim();
  if (tail) {
    onEvent(JSON.parse(tail) as WikiSyncEvent);
  }
}

export async function fetchModules(scope = "all"): Promise<ModuleRow[]> {
  const data = await apiFetch<{ modules: ModuleRow[] }>(
    `/api/modules?scope=${encodeURIComponent(scope)}`,
  );
  return data.modules;
}

export async function fetchModuleScenarios(scope = "all"): Promise<ScenarioRow[]> {
  const data = await apiFetch<{ scenarios: ScenarioRow[] }>(
    `/api/modules/scenarios?scope=${encodeURIComponent(scope)}`,
  );
  return data.scenarios;
}

export async function setScenarioEnabled(
  key: string,
  enabled: boolean,
): Promise<void> {
  await apiFetch(`/api/modules/scenarios/${encodeURIComponent(key)}/enabled`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ enabled }),
  });
}

export async function reloadScenarios(): Promise<number> {
  const data = await apiFetch<{ loaded: number }>(
    "/api/modules/scenarios/reload",
    { method: "POST" },
  );
  return data.loaded;
}

export async function fetchPlayerAssignments(): Promise<PlayerAssignment[]> {
  const data = await apiFetch<{ players: PlayerAssignment[] }>(
    "/api/modules/players",
  );
  return data.players;
}

export async function setPlayerAssignment(
  playerId: string,
  scenarioId: string | null,
): Promise<void> {
  await apiFetch(
    `/api/modules/players/${encodeURIComponent(playerId)}/assignment`,
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ scenario_id: scenarioId }),
    },
  );
}

export async function fetchGallery(
  scope = "all",
  q = "",
): Promise<{ items: GalleryItem[]; count: number }> {
  const params = new URLSearchParams({ scope });
  if (q) params.set("q", q);
  return apiFetch(`/api/gallery?${params}`);
}

export function galleryImageUrl(rel: string): string {
  return `${base}/api/gallery/image?path=${encodeURIComponent(rel)}&t=${Date.now()}`;
}

export async function fetchAnalyzeAudit(
  scope = "all",
): Promise<{
  scope: string;
  manifest_count: number;
  issue_count: number;
  issues: AnalyzeIssue[];
}> {
  return apiFetch(`/api/analyze/audit?scope=${encodeURIComponent(scope)}`);
}

export async function fetchAdbStatus(): Promise<AdbStatus> {
  return apiFetch<AdbStatus>("/api/adb");
}

export async function resetAdbDeviceDisplay(serial: string): Promise<AdbResetDisplayResult> {
  return apiFetch<AdbResetDisplayResult>(
    `/api/adb/devices/${encodeURIComponent(serial)}/reset-display`,
    { method: "POST" },
  );
}

export async function fetchMinicapStatus(serial: string): Promise<MinicapStatus> {
  return apiFetch<MinicapStatus>(
    `/api/adb/devices/${encodeURIComponent(serial)}/minicap`,
  );
}

export async function installMinicap(serial: string): Promise<MinicapInstallResult> {
  return apiFetch<MinicapInstallResult>(
    `/api/adb/devices/${encodeURIComponent(serial)}/minicap/install`,
    { method: "POST" },
  );
}

export async function fetchMinitouchStatus(serial: string): Promise<MinitouchStatus> {
  return apiFetch<MinitouchStatus>(
    `/api/adb/devices/${encodeURIComponent(serial)}/minitouch`,
  );
}

export async function installMinitouch(serial: string): Promise<MinitouchInstallResult> {
  return apiFetch<MinitouchInstallResult>(
    `/api/adb/devices/${encodeURIComponent(serial)}/minitouch/install`,
    { method: "POST" },
  );
}

export async function updateDeviceBackend(
  serial: string,
  body: { screenshot_backend?: string; input_backend?: string },
): Promise<DeviceBackendUpdate> {
  return apiFetch<DeviceBackendUpdate>(
    `/api/adb/devices/${encodeURIComponent(serial)}/backend`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    },
  );
}

export async function runDebugScenario(body: {
  instance_id: string;
  scenario_key: string;
  player_id?: string;
  priority?: number;
}): Promise<{ ok: boolean; task_id: string }> {
  return apiFetch("/api/debug/run", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export async function fetchBalanceFiles(): Promise<BalanceFileMeta[]> {
  const data = await apiFetch<{ files: BalanceFileMeta[] }>("/api/balance");
  return data.files;
}

export async function fetchBalanceFile(
  fileId: string,
): Promise<{ id: string; path: string; content: unknown }> {
  return apiFetch(`/api/balance/${encodeURIComponent(fileId)}`);
}

export async function fetchOptimizerMeta(): Promise<OptimizerMeta> {
  return apiFetch<OptimizerMeta>("/api/optimizer/meta");
}

export async function reloadOptimizerBalance(): Promise<void> {
  await apiFetch("/api/optimizer/reload-balance", { method: "POST" });
}

export async function solveOptimizer(body: {
  mode: "production" | "playground";
  gamer_id?: string;
  state_flat?: Record<string, unknown>;
  server_age_days?: number;
  plan_k?: number;
  profile_id?: string;
}): Promise<OptimizerSolveResult> {
  return apiFetch<OptimizerSolveResult>("/api/optimizer/solve", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export async function optimizerDryRun(body: {
  candidate_id: string;
  gamer_id?: string;
  state_flat?: Record<string, unknown>;
  server_age_days?: number;
  profile_id?: string;
}): Promise<{ changed_keys: number; diff: Record<string, unknown> }> {
  return apiFetch("/api/optimizer/dry-run", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export async function optimizerApprove(body: {
  candidate_id: string;
  gamer_id: string;
  server_age_days?: number;
  profile_id?: string;
}): Promise<{ ok: boolean; persisted_keys: number }> {
  return apiFetch("/api/optimizer/approve", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export async function optimizerQueue(body: {
  candidate_id: string;
  gamer_id: string;
  instance_id: string;
  server_age_days?: number;
  profile_id?: string;
}): Promise<{ ok: boolean; task_id: string; dsl_scenario: string }> {
  return apiFetch("/api/optimizer/queue", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export async function fetchEditDslCatalog(
  scope = "all",
): Promise<{
  files: ScenarioFileEntry[];
  tree: ScenarioTreeNode[];
  modules: EditableModuleEntry[];
}> {
  return apiFetch(`/api/edit-dsl/catalog?scope=${encodeURIComponent(scope)}`);
}

export async function fetchEditDslMeta(): Promise<{
  regions: string[];
  fsm_nodes: string[];
  exec_names: string[];
  scenario_keys: string[];
}> {
  return apiFetch("/api/edit-dsl/meta");
}

export type EditScenarioDocument = Record<string, unknown>;

export async function fetchEditScenarioFile(rel: string): Promise<{
  rel: string;
  yaml: string;
  document: EditScenarioDocument;
  valid: boolean;
  validation_error: string;
}> {
  return apiFetch(`/api/edit-dsl/file?rel=${encodeURIComponent(rel)}`);
}

export async function saveEditScenarioFile(rel: string, yaml: string): Promise<void> {
  await apiFetch(`/api/edit-dsl/file?rel=${encodeURIComponent(rel)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ yaml }),
  });
}

export async function saveEditScenarioDocument(
  rel: string,
  document: EditScenarioDocument,
): Promise<void> {
  await apiFetch(`/api/edit-dsl/file?rel=${encodeURIComponent(rel)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ document }),
  });
}

export async function validateEditScenarioYaml(
  yaml: string,
): Promise<{ valid: boolean; error: string; preview: string }> {
  return apiFetch("/api/edit-dsl/validate", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ yaml }),
  });
}

export async function validateEditScenarioDocument(
  document: EditScenarioDocument,
): Promise<{ valid: boolean; error: string; preview: string }> {
  return apiFetch("/api/edit-dsl/validate", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ document }),
  });
}

export async function fetchEditScenarioNameCollisions(
  rel: string,
  name: string,
): Promise<string[]> {
  const q = new URLSearchParams({ rel, name });
  const data = await apiFetch<{ collisions: string[] }>(
    `/api/edit-dsl/name-collisions?${q}`,
  );
  return data.collisions;
}

export async function createEditDslFile(body: {
  module: string;
  file_key: string;
  template_rel?: string;
}): Promise<{ rel: string }> {
  return apiFetch("/api/edit-dsl/create", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export async function fetchLicenseFingerprint(): Promise<LicenseFingerprint> {
  return apiFetch<LicenseFingerprint>("/api/license/fingerprint");
}

export async function fetchLicenseStatus(): Promise<LicenseStatus> {
  return apiFetch<LicenseStatus>("/api/license/status");
}

export async function issueLicense(
  body: LicenseIssueRequest,
  adminToken: string,
): Promise<LicenseIssueResult> {
  return apiFetch<LicenseIssueResult>("/api/license/issue", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-Admin-Token": adminToken,
    },
    body: JSON.stringify(body),
  });
}

export async function importLicenseFile(
  file: File,
): Promise<LicenseImportResult> {
  const fd = new FormData();
  fd.append("file", file);
  // Don't set Content-Type — the browser fills in multipart boundary automatically.
  return apiFetch<LicenseImportResult>("/api/license/import", {
    method: "POST",
    body: fd,
  });
}
