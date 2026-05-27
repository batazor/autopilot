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

export type AdbDeviceRow = {
  name: string;
  adb_serial: string;
  instance_id: string;
  bluestacks_window_title: string;
  /** Explicit value from devices.yaml (empty string if unset). */
  screenshot_backend: string;
  /** What the dispatcher will actually use after the smart default kicks in. */
  screenshot_backend_effective: string;
  /** Same shape as screenshot_backend but for tap/swipe events (minitouch vs adb). */
  input_backend: string;
  input_backend_effective: string;
};

export type AdbStatus = {
  adb_executable: string;
  devices_yaml: string;
  settings_yaml: string;
  configured: AdbDeviceRow[];
  live_devices: Array<{ serial: string; line: string }>;
  scan_error: string | null;
};

export type AdbResetDisplayResult = {
  ok: boolean;
  serial: string;
  wm_size: string;
  wm_density: string;
};

export type MinicapStatus = {
  serial: string;
  abi: string | null;
  sdk: string | null;
  binary_installed: boolean;
  library_installed: boolean;
  binary_size: number | null;
  library_size: number | null;
  last_error: string | null;
  installed: boolean;
};

export type MinicapInstallResult = MinicapStatus & { ok: boolean };

export type MinitouchStatus = {
  serial: string;
  abi: string | null;
  sdk: string | null;
  binary_installed: boolean;
  binary_size: number | null;
  last_error: string | null;
  installed: boolean;
};

export type MinitouchInstallResult = MinitouchStatus & { ok: boolean };

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

export type LicenseFingerprint = {
  fingerprint: string;
  components: Record<string, string>;
};

export type LicenseState =
  | "active"
  | "missing"
  | "expired"
  | "invalid"
  | "machine_mismatch";

export type LicenseStatus = {
  active: boolean;
  state: LicenseState;
  reason: string | null;
  sub: string | null;
  tier: string | null;
  features: string[];
  expires_at: string | null;
  days_left: number | null;
  machine_id: string | null;
  max_devices: number | null;
  max_players_per_device: number | null;
  admin_enabled: boolean;
  license_file: string;
};

export type LicenseIssueRequest = {
  sub: string;
  machine_id: string;
  days: number;
  tier: string;
  features: string[];
  max_devices: number;
  max_players_per_device: number;
};

export type LicenseIssueResult = {
  token: string;
  payload: Record<string, unknown>;
};

export type LicenseImportResult = {
  ok: boolean;
  license_file: string;
  status: LicenseStatus;
};
