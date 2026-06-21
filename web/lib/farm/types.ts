export type Pending = {
  username: string;
  started_at?: string;
  stage?: string;
  image_code?: string;
  slider?: string;
  slider_expected?: string;
  register_attempt?: string;
  register_max_attempts?: string;
  previous_register?: string;
} | null;

export type StartRegistrationResponse = {
  running?: boolean;
  pending?: Pending;
  pid?: number | null;
  started_at?: number | null;
  log_path?: string | null;
};

export type StartRegistrationOptions = {
  username?: string;
  existing?: boolean;
};

export type RegistrationStatus = {
  running: boolean;
  pending: Pending;
  pid: number | null;
  started_at: number | null;
  finished_at: number | null;
  exit_code: number | null;
  log_path: string | null;
  log_tail: string;
};

export type ActiveInGame = {
  fid: string;
  instances: {
    instance_id: string;
    screen: string;
    task: string;
  }[];
};

export type FarmCharacter = {
  server: string;
  fid: string;
  nickname: string;
  created_at: number | null;
  updated_at: number | null;
  note: string;
  active: ActiveInGame | null;
  /** Per-character planner role id (defaults to "balanced" when unset). */
  role: string;
};

/** A selectable planner-role profile, from `GET /api/farm/roles`. */
export type RoleOption = {
  id: string;
  label: string;
  description: string;
};

export type FarmAccount = {
  username: string;
  status: string;
  server: string;
  device_serial: string | null;
  registered_at: number | null;
  active: ActiveInGame | null;
  characters: FarmCharacter[];
};

export type CharacterEdit = {
  server: string;
  fid: string;
  nickname: string;
};

export const STATUSES = ["pending", "registered", "bound", "failed"] as const;

export function activeTitle(active?: ActiveInGame | null): string | undefined {
  if (!active) return undefined;
  const instances = active.instances
    .map((i) =>
      [i.instance_id, i.screen || null, i.task || null].filter(Boolean).join(" · "),
    )
    .join("; ");
  return instances ? `In game: ${instances}` : "In game";
}
