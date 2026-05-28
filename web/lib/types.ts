export type HealthView = {
  status: "ok" | "degraded";
  api: "ok";
  redis: "ok" | "unreachable";
};

export type VersionView = {
  current: { version: string; revision: string };
  remote: { tag: string; html_url: string } | null;
  update_available: boolean;
  repo: string;
  checked_at: number;
  reason: "ok" | "dev_build" | "github_unreachable";
};

export type BotStatusView = {
  running: boolean;
  mode: "supervisor" | "embedded" | null;
  pid: number | null;
};

export type OverlayRect = {
  type: "rect";
  x: number;
  y: number;
  w: number;
  h: number;
  label?: string;
  stroke?: string;
};

export type OverlayCrosshair = {
  type: "crosshair";
  x: number;
  y: number;
};

export type OverlayArrow = {
  type: "arrow";
  x1: number;
  y1: number;
  x2: number;
  y2: number;
  label?: string;
};

export type OverlayShape = OverlayRect | OverlayCrosshair | OverlayArrow;

export type NavigationApprovalInfo = {
  from: string;
  to: string;
  path: string[];
  hop_index: number;
};

export type TaskApprovalContext = {
  threshold: string;
  score: string;
  text: string;
  confidence: string;
};

export type ScenarioProgress = {
  scenario_key: string;
  scenario_label: string;
  step_current: number;
  step_total: number;
  step_iter: number;
  is_running: boolean;
  nav_target: string;
  step_summaries: string[];
  /** FSM navigation in progress — bar must not count the current step as done. */
  is_navigating?: boolean;
  completed_steps?: number;
  progress_ratio?: number;
  highlight_step_index?: number;
  /** Server-formatted label (nav vs step semantics). */
  progress_label?: string;
};

export type ClickApprovalView = {
  instance_id: string;
  has_pending: boolean;
  approval_enabled: boolean;
  heartbeat_active: boolean;
  scenario_key: string;
  scenario_label: string;
  region_label: string;
  action_type: string;
  action_label: string;
  set_node_target: string;
  trace_id: string;
  tempo_trace_url: string;
  labeling_href: string;
  diagnostic_kind: string;
  diagnostic_attempts: string;
  diagnostic_interval: string;
  navigation: NavigationApprovalInfo | null;
  task_context: TaskApprovalContext | null;
  tap_x: number | null;
  tap_y: number | null;
  overlays: OverlayShape[];
  pending: Record<string, unknown> | null;
  preview: {
    available: boolean;
    width: number;
    height: number;
    mtime?: number | null;
  };
  /** Live H.264 WebSocket stream capability for this instance.
   *  ``available: true`` only when scrcpy is currently running AND has
   *  received its codec config. The UI uses this to auto-pick WebCodecs
   *  instead of opening a doomed socket on adb/quartz/minicap devices. */
  stream: {
    available: boolean;
  };
  instance_state: Record<string, string>;
  current_screen: string;
  active_player: string;
  active_player_in_game_id: string;
  scenario_progress: ScenarioProgress;
};

export type NotificationEvent = {
  id: string;
  ts: number;
  kind: string;
  message: string;
  level: "success" | "info" | "warning" | "error";
  payload?: Record<string, unknown>;
};

export type OverviewMetrics = {
  instances: number;
  live_workers: number;
  queue: number;
  busy: number;
  locks: number;
  paused: number;
};

export type FleetPlayerRow = {
  id: string;
  who: string;
  on_device: boolean;
  nickname: string;
  in_game_id: string;
  ocr_conf: string;
  ocr_age: string;
  stove: string;
  kid: string;
  century: string;
  game: string;
};

export type FleetInstanceRow = {
  instance_id: string;
  status: string;
  active_player: string;
  node: string;
  task: string;
  uptime: string;
  alert: string;
  paused: boolean;
  players: FleetPlayerRow[];
};

export type OverviewView = {
  metrics: OverviewMetrics;
  fleet: FleetInstanceRow[];
  has_devices: boolean;
};

export type QueuePendingRow = {
  task_id: string;
  scheduled: string;
  scheduled_at: number;
  overdue: boolean;
  player_id: string;
  instance_id: string;
  scenario: string;
  scenario_key: string;
  region: string;
  priority: number;
  cooperative: boolean;
};

export type QueueRunningRow = {
  task_id: string;
  instance_id: string;
  scenario: string;
  scenario_key: string;
  active_scenario: string;
  active_scenario_label: string;
  step: number;
  player_id: string;
  region: string;
  priority?: number;
  started: string;
  nav_target: string;
};

export type QueueHistoryRow = {
  task_id: string;
  scenario: string;
  scenario_key: string;
  player_id: string;
  instance_id: string;
  priority: number;
  started_at: number;
  finished_at: number;
  duration_s: number;
  success: boolean;
  region: string;
  reason: string;
  steps: string;
  trace_id: string;
  tempo_trace_url: string;
  steps_trace: Record<string, unknown>[] | null;
};

export type QueueView = {
  pending: QueuePendingRow[];
  running: QueueRunningRow[];
  history: QueueHistoryRow[];
  pending_count: number;
  revision?: string;
};

export type QueueUnchangedResponse = {
  unchanged: true;
  revision: string;
};

export type InstanceUnchangedResponse = {
  unchanged: true;
  revision: string;
};

export type InstanceHistoryRow = {
  player_id: string;
  scenario: string;
  started_at: number;
  duration_s: number;
  success: boolean;
  detail: string;
  trace_id: string;
};

export type InstanceDetail = {
  instance_id: string;
  status: string;
  paused: boolean;
  active_player: string;
  node: string;
  task: string;
  alert: string;
  nav_error: string;
  queue_size: number;
  next_due: { task_id: string; task_type: string; scheduled_at: number } | null;
  player_ids: string[];
  runnable_scenarios: string[];
  preview_available: boolean;
  preview_mtime: number | null;
  history: InstanceHistoryRow[];
  state: Record<string, string>;
  revision?: string;
};

export type BuildingLevelRow = {
  id: string;
  building: string;
  category: string;
  level: number | string;
};

export type HeroStateRow = {
  id: string;
  hero: string;
  available: boolean;
  level: number;
  shards_current: number;
  shards_required: number;
  red_dot: boolean;
  upgrade: boolean;
  rarity: string;
  class: string;
  sub_class: string;
  seen: string;
};

/** Registry heroes not yet in player state (subset of HeroStateRow fields). */
export type HeroMissingRow = {
  id: string;
  hero: string;
  rarity: string;
  class: string;
  sub_class: string;
};

export type PlayerStateView = {
  player_id: string;
  fields: Record<string, string>;
  field_count: number;
  nickname: string;
  stove_level: string;
  kid: string;
  avatar_image: string;
  building_levels: BuildingLevelRow[];
};

export type PlayerPowerDay = {
  day: string;
  power: number;
  furnace_level: number;
  gems: number;
  arena_rank: number;
  arena_power: number;
};

export type PlayerLevelEvent = {
  day: string;
  level: number;
};

export type PlayerStatsView = {
  player_id: string;
  nickname: string;
  series: PlayerPowerDay[];
  level_events: PlayerLevelEvent[];
};

export type AllianceDay = {
  day: string;
  power: number;
  members_count: number;
  members_max: number;
};

export type AllianceStatsView = {
  alliance_name: string;
  series: AllianceDay[];
};

export type PlayerPersistedView = {
  state_path: string;
  storage?: string;
  parse_error: string | null;
  raw_yaml: string | null;
  raw_json?: string | null;
  player: {
    player_id: string;
    summary: Record<string, unknown>;
    gamer: Record<string, unknown>;
    building_levels: BuildingLevelRow[];
    buildings_hud: Record<string, string>;
    resources: Record<string, number | string>;
    recruitment: Record<string, number>;
    troops: Record<string, string | boolean>;
    alliance_block: Record<string, string | number>;
    heroes: {
      metrics: Record<string, number | boolean>;
      owned: HeroStateRow[];
      locked: HeroStateRow[];
      missing: HeroMissingRow[];
    };
  } | null;
};

export type CenturySyncResult = {
  ok: boolean;
  player_id: string;
  nickname: string;
  stove_level: number;
  kid: number;
  steps: Array<{ step: string; detail: string }>;
};

export type RoutesGraphView = "hub" | "focus" | "path" | "full";

export type RoutesGraphResponse = {
  metrics: {
    page_transitions: number;
    tree_edges: number;
    static_edges: number;
    dynamic_edges: number;
    screens: number;
  };
  nodes: Array<Record<string, unknown>>;
  edges: Array<Record<string, unknown>>;
  height: number;
  width: number;
  screens: string[];
  visible_screens?: string[];
  visible_count?: number;
  total_screens?: number;
  view?: RoutesGraphView;
  path: string[] | null;
  mode: string;
  hops: Array<{ n: string; hop: string; status: string; action: string }>;
};

export type RoutesNodeDetails = {
  node_id: string;
  incoming: number;
  outgoing: number;
  edges: Array<{ dir: string; edge: string; status: string }>;
};

export type LabelingVersionMeta = {
  id: string;
  cond: string;
  ocr: string | null;
};

export type LabelingScopeOption = {
  key: string;
  title: string;
  label: string;
  references_prefix: string;
  area_path: string;
  default_ref: string | null;
  is_all: boolean;
};

export type LabelingReferenceMeta = {
  rel: string;
  name: string;
  rel_under: string;
  title: string;
  screen_id: string;
  region_count: number;
  active_version: string | null;
  unassigned: boolean;
};

export type LabelingStaleCrop = {
  ocr: string;
  region: string;
  expected_w: number;
  expected_h: number;
  actual_w: number;
  actual_h: number;
  crop_path: string;
};

export type LabelingDocument = {
  ref: string;
  display_ref: string;
  screen_id: string;
  entry_id: number | null;
  regions: Record<string, unknown>[];
  versions: LabelingVersionMeta[];
  active_version: string | null;
  is_pending: boolean;
  basename: string;
  area_path: string;
  references_prefix?: string;
  scope?: string;
  module_key?: string;
  module_title?: string;
  redirect_version?: string | null;
};

export type OverlayRuleRow = {
  name: string;
  node: string;
  region: string;
  action: string;
  search_region: string;
  matched: boolean;
  score: number | null;
  threshold: number | null;
  reason: string;
  notes: string;
};

export type ModuleAnalyzerRun = {
  module_id: string;
  label: string;
  duration_ms: number;
  rule_count: number;
  matched_count: number;
};

export type PushScenarioCandidate = {
  scenario: string;
  rule: string;
  region: string;
  priority: number;
  selected: boolean;
  skip_reason: string;
};

export type OverlayAnalysisSummary = {
  module_runs: ModuleAnalyzerRun[];
  modules_total_ms: number;
  full_run_ms: number;
  screen_detect_ms: number;
  screen_source: string;
  push_candidates: PushScenarioCandidate[];
  has_active_player: boolean;
  simulated_no_player: boolean;
  device_level_only: boolean;
};

export type OverlayTestResult = {
  instance_id: string;
  /** Screen used for overlay ``screens`` gates (detected on frame only). */
  current_screen: string;
  detected_screen: string;
  /** Synthetic label from ``hasActivePlayer`` UI flag, not Redis. */
  active_player: string;
  preview: {
    available: boolean;
    rel: string;
    mtime: number | null;
    width: number;
    height: number;
    source?: "live" | "reference" | string;
  };
  rules: OverlayRuleRow[];
  overlays: OverlayShape[];
  total_rules: number;
  matched_count: number;
  analysis: OverlayAnalysisSummary;
};

export type ProbeCropSide = {
  available?: boolean;
  width?: number;
  height?: number;
  label?: string;
  data_url?: string;
};

export type ProbeCrops = {
  region?: string;
  resolved_region?: string;
  reference_rel?: string;
  live?: ProbeCropSide;
  template?: ProbeCropSide;
};

export type AreaRegionProbeResult = {
  instance_id: string;
  current_screen: string;
  active_player: string;
  selected_region: string;
  regions: string[];
  preview: {
    available: boolean;
    rel: string;
    mtime: number | null;
    width: number;
    height: number;
  };
  crops?: ProbeCrops | null;
  result: {
    matched?: boolean;
    region?: string;
    resolved_region?: string;
    resolved_version?: string;
    action?: string;
    search_region?: string;
    score?: number;
    score_ncc?: number;
    score_color?: number;
    score_edge?: number;
    threshold?: number;
    top_left?: number[];
    template_w?: number;
    template_h?: number;
    match_source?: string;
    reason?: string;
    detail?: string;
    template_bright_ratio?: number;
    patch_bright_ratio?: number;
    mean_saturation?: number;
    tap_x_pct?: number;
    tap_y_pct?: number;
  } | null;
  overlays: OverlayShape[];
};
