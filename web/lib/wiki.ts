export type WikiScope = { key: string; label: string };

export type WikiEntrySummary = {
  id: string;
  name: string;
  source: string;
  wiki_url: string;
  has_icon: boolean;
  yaml_path: string;
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
