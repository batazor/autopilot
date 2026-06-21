export type DreamscapeNewCaptureReason = "unknown_scene" | "new_word";

export type DreamscapeNewCapture = {
  id: string;
  ref: string;
  reason: DreamscapeNewCaptureReason;
  createdAt: number;
  instanceId: string;
  mode: "solo" | "multiplayer";
  levelName: string;
  sceneSlug: string | null;
  sceneTitle: string | null;
  words: string[];
};

const STORAGE_KEY = "dreamscape:new-captures:v1";
const EVENT_NAME = "dreamscape-new-captures";

function isCapture(value: unknown): value is DreamscapeNewCapture {
  const v = value as Partial<DreamscapeNewCapture>;
  return (
    Boolean(v) &&
    typeof v.id === "string" &&
    typeof v.ref === "string" &&
    (v.reason === "unknown_scene" || v.reason === "new_word") &&
    typeof v.createdAt === "number" &&
    typeof v.instanceId === "string" &&
    (v.mode === "solo" || v.mode === "multiplayer") &&
    typeof v.levelName === "string" &&
    Array.isArray(v.words)
  );
}

function emitChange(): void {
  window.dispatchEvent(new Event(EVENT_NAME));
}

export function dreamscapeNewCapturesEventName(): string {
  return EVENT_NAME;
}

export function loadDreamscapeNewCaptures(): DreamscapeNewCapture[] {
  if (typeof window === "undefined") return [];
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    const parsed = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed)
      ? parsed.filter(isCapture).sort((a, b) => b.createdAt - a.createdAt)
      : [];
  } catch {
    return [];
  }
}

export function addDreamscapeNewCapture(capture: DreamscapeNewCapture): void {
  if (typeof window === "undefined") return;
  const current = loadDreamscapeNewCaptures();
  const next = [capture, ...current.filter((c) => c.id !== capture.id)].slice(0, 50);
  window.localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
  emitChange();
}

export function hasDreamscapeNewCapture(
  predicate: (capture: DreamscapeNewCapture) => boolean,
): boolean {
  return loadDreamscapeNewCaptures().some(predicate);
}

export function removeDreamscapeNewCapture(id: string): void {
  if (typeof window === "undefined") return;
  const next = loadDreamscapeNewCaptures().filter((c) => c.id !== id);
  window.localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
  emitChange();
}
