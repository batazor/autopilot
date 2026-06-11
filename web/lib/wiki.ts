export type WikiScope = { key: string; label: string };

export type WikiEntrySummary = {
  id: string;
  name: string;
  source: string;
  wiki_url: string;
  has_icon: boolean;
  yaml_path: string;
  // Release generation for heroes (1..N); null for non-generation (Epic/Rare)
  // heroes and for non-hero entities.
  generation?: number | null;
  // True when the hero is obtainable only through paid channels.
  paid_only?: boolean;
  // Troop class for heroes: "infantry" | "lancer" | "marksman".
  unit_class?: "infantry" | "lancer" | "marksman" | null;
};

export type WikiDetail = {
  entity: string;
  summary: WikiEntrySummary;
  body: Record<string, unknown>;
};

export type GiftCodeRow = {
  code: string;
  expires: string;
  slot_expired: boolean;
  needs_run: boolean;
  api_err: string;
  api_msg: string;
  players: Record<string, { status: string; nickname: string; label: string }>;
};

export type GiftCodesView = {
  game: string;
  codes_path: string;
  devices_path: string;
  parse_error: string | null;
  missing_codes_file: boolean;
  player_ids: string[];
  active: GiftCodeRow[];
  expired: GiftCodeRow[];
  metrics: {
    total: number;
    active: number;
    expired: number;
    needs_run: number;
    pending_slots: number;
    redeemed_slots: number;
  };
};
