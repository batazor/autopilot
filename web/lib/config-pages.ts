export type ScenarioRow = {
  key: string;
  name: string;
  enabled: boolean | null;
  device_level: boolean;
  steps: number;
  source: string;
  path: string;
};

export type ModuleRow = {
  id: string;
  storage_key: string;
  title: string;
  description: string;
  wiki: boolean;
  core: boolean;
  rel_path: string;
  scenarios_dir: string | null;
  has_analyze: boolean;
  scenario_count: number;
  enabled_on: number;
  enabled_off: number;
  scenarios: ScenarioRow[];
};

export type PlayerAssignment = {
  player_id: string;
  assigned_scenario: string | null;
};

export type GalleryItem = {
  rel: string;
  name: string;
  group: string;
  screen_ids: string[];
  size_bytes: number;
};

export type AnalyzeIssue = {
  manifest: string;
  rule: string;
  severity: string;
  source: string;
  message: string;
};

export type AdbDeviceRow = {
  name: string;
  adb_serial: string;
  instance_id: string;
  bluestacks_window_title: string;
};

export type AdbStatus = {
  adb_executable: string;
  devices_yaml: string;
  settings_yaml: string;
  configured: AdbDeviceRow[];
  live_devices: Array<{ serial: string; line: string }>;
  scan_error: string | null;
};

export type BalanceFileMeta = { id: string; filename: string };

export type OptimizerMeta = {
  gamers: Array<{ id: string; nickname: string }>;
  instances: string[];
  profiles: Array<{ id: string; description: string }>;
  active_profile_id: string;
  heroes: Array<{ id: string; name: string }>;
  default_playground_state: Record<string, unknown>;
};

export type OptimizerSolveResult = {
  metrics: {
    status: string;
    objective: number;
    selected_count: number;
    rejected_count: number;
    pruned_count: number;
    profile_id: string;
    profile_description: string;
  };
  plan: Array<Record<string, unknown>>;
  candidates: Array<Record<string, unknown>>;
  resources: Array<Record<string, unknown>>;
  next_command: {
    candidate_id: string;
    headline: string;
    reasons: string[];
    dispatch: { dsl_scenario: string; set_node: string; region: string | null };
  } | null;
  gamer_id?: string;
};

export type ScenarioFileEntry = {
  rel: string;
  stem: string;
  module: string;
  path: string;
};

export type EditableModuleEntry = {
  key: string;
  title: string;
  scenarios_dir: string;
};

export type ScenarioTreeNode = {
  value: string;
  title: string;
  is_dir: boolean;
  children?: ScenarioTreeNode[];
};
