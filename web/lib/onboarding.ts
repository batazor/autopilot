export type OnboardingState = {
  device_added_at: string | null;
  bot_started_at: string | null;
  first_scenario_at: string | null;
  first_approval_at: string | null;
  first_ocr_at: string | null;
  approvals_disabled_at: string | null;
};

export type EnvHealthEntry = {
  ok: boolean;
  error?: string;
  path?: string;
  version?: string;
  latency_ms?: number;
};

export type EnvHealth = {
  redis: EnvHealthEntry;
  tesseract: EnvHealthEntry;
  adb: EnvHealthEntry;
};

async function get<T>(path: string): Promise<T> {
  const res = await fetch(path, { cache: "no-store" });
  if (!res.ok) {
    throw new Error(`${path}: ${res.status}${await errorSuffix(res)}`);
  }
  return res.json() as Promise<T>;
}

async function errorSuffix(res: Response): Promise<string> {
  const text = (await res.text().catch(() => "")).trim();
  const detail = parseErrorDetail(text);
  if (detail) return ` — ${detail}`;
  if (res.status >= 500) {
    return " — Onboarding API failed unexpectedly. Check the API logs and retry.";
  }
  return text ? ` — ${text}` : "";
}

function parseErrorDetail(text: string): string {
  if (!text || text === "Internal Server Error") return "";
  try {
    const parsed = JSON.parse(text) as unknown;
    if (
      parsed &&
      typeof parsed === "object" &&
      "detail" in parsed
    ) {
      const detail = (parsed as { detail?: unknown }).detail;
      if (typeof detail === "string") return detail;
      if (detail != null) return JSON.stringify(detail);
    }
  } catch {
    return text;
  }
  return text;
}

export function fetchOnboardingState(): Promise<OnboardingState> {
  return get<OnboardingState>("/api/onboarding/state");
}

export function fetchEnvHealth(): Promise<EnvHealth> {
  return get<EnvHealth>("/api/onboarding/env-health");
}

const WIZARD_SEEN_KEY = "wos:onboarding:wizardSeen";
const CHECKLIST_DISMISSED_KEY = "wos:onboarding:checklistDismissed";
const CHECKLIST_CELEBRATED_KEY = "wos:onboarding:checklistCelebrated";

export function wizardSeen(): boolean {
  if (typeof window === "undefined") return true;
  return window.localStorage.getItem(WIZARD_SEEN_KEY) === "1";
}

export function markWizardSeen(): void {
  if (typeof window === "undefined") return;
  window.localStorage.setItem(WIZARD_SEEN_KEY, "1");
}

export function checklistDismissed(): boolean {
  if (typeof window === "undefined") return true;
  return window.localStorage.getItem(CHECKLIST_DISMISSED_KEY) === "1";
}

export function markChecklistDismissed(): void {
  if (typeof window === "undefined") return;
  window.localStorage.setItem(CHECKLIST_DISMISSED_KEY, "1");
}

export function checklistCelebrated(): boolean {
  if (typeof window === "undefined") return true;
  return window.localStorage.getItem(CHECKLIST_CELEBRATED_KEY) === "1";
}

export function markChecklistCelebrated(): void {
  if (typeof window === "undefined") return;
  window.localStorage.setItem(CHECKLIST_CELEBRATED_KEY, "1");
}
