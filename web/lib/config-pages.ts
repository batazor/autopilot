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

export type GalleryItem = {
  rel: string;
  name: string;
  group: string;
  screen_ids: string[];
  size_bytes: number;
  mtime_ms: number;
};

export type AdbDeviceRow = {
  name: string;
  adb_serial: string;
  instance_id: string;
  bluestacks_window_title: string;
  /** Explicit value from devices.yaml (empty string if unset). */
  screenshot_backend: string;
  /** What the dispatcher will actually use after the smart default kicks in. */
  screenshot_backend_effective: string;
  /** Same shape as screenshot_backend but for tap/swipe events (adb vs scrcpy). */
  input_backend: string;
  input_backend_effective: string;
};

export type AdbDetectedGame = {
  id: string;
  label: string;
  package: string;
  beta: boolean;
  running: boolean;
};

export type AdbLiveDevice = {
  serial: string;
  canonical_serial?: string;
  line: string;
  detected_games?: AdbDetectedGame[];
};

export type AdbScanPortRange = {
  start: number | null;
  end: number | null;
  step: number;
  count: number;
};

export type AdbStatus = {
  adb_executable: string;
  devices_yaml: string;
  settings_yaml: string;
  configured: AdbDeviceRow[];
  live_devices: AdbLiveDevice[];
  scan_error: string | null;
  scan_port_range?: AdbScanPortRange;
};

export type AdbResetDisplayResult = {
  ok: boolean;
  serial: string;
  wm_size: string;
  wm_density: string;
};

export type ScrcpyStatus = {
  serial: string;
  abi: string | null;
  sdk: string | null;
  jar_installed: boolean;
  jar_size: number | null;
  last_error: string | null;
  installed: boolean;
};

export type ScrcpyInstallResult = ScrcpyStatus & { ok: boolean };

export type DeviceBackendUpdate = {
  ok: boolean;
  serial: string;
  screenshot_backend: string;
  input_backend: string;
  restart_required: boolean;
  scrcpy_install?: ScrcpyInstallResult | null;
};

export type DeviceRegisterResult = {
  ok: boolean;
  created: boolean;
  name: string;
  adb_serial: string;
  restart_required: boolean;
  removed?: string[];
  scrcpy_install?: ScrcpyInstallResult | null;
};

export type DeviceCreateBody = {
  name?: string;
  adb_serial: string;
  screenshot_backend?: string;
  input_backend?: string;
  replace_existing?: boolean;
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
