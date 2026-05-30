const INSTANCE_KEY = "wos.fleet.instanceId";
const PLAYER_KEY = "wos.fleet.playerId";

// Same-tab change signal. ``storage`` events only fire in *other* tabs, so the
// Bot-control device carousel (which lives in the sidebar, outside
// FleetContextProvider) can't observe page-driven selection changes without an
// explicit in-tab event. Both the carousel and the page dropdowns write through
// ``saveFleetInstanceId``, so dispatching here keeps every selector in sync.
const INSTANCE_EVENT = "wos.fleet.instanceId.changed";

export function loadFleetInstanceId(): string {
  if (typeof window === "undefined") return "";
  try {
    return window.localStorage.getItem(INSTANCE_KEY)?.trim() ?? "";
  } catch {
    return "";
  }
}

export function saveFleetInstanceId(instanceId: string): void {
  if (typeof window === "undefined") return;
  try {
    if (instanceId) window.localStorage.setItem(INSTANCE_KEY, instanceId);
    else window.localStorage.removeItem(INSTANCE_KEY);
  } catch {
    /* quota / private mode */
  }
  try {
    window.dispatchEvent(new CustomEvent(INSTANCE_EVENT, { detail: instanceId }));
  } catch {
    /* CustomEvent unsupported — same-tab listeners simply won't update */
  }
}

/**
 * Subscribe to fleet-instance selection changes (same tab via CustomEvent,
 * other tabs via the ``storage`` event). The callback receives the freshly
 * persisted id. Returns an unsubscribe function.
 */
export function subscribeFleetInstanceId(cb: (id: string) => void): () => void {
  if (typeof window === "undefined") return () => {};
  const onChange = () => cb(loadFleetInstanceId());
  const onStorage = (e: StorageEvent) => {
    if (e.key === null || e.key === INSTANCE_KEY) cb(loadFleetInstanceId());
  };
  window.addEventListener(INSTANCE_EVENT, onChange as EventListener);
  window.addEventListener("storage", onStorage);
  return () => {
    window.removeEventListener(INSTANCE_EVENT, onChange as EventListener);
    window.removeEventListener("storage", onStorage);
  };
}

export function loadFleetPlayerId(): string {
  if (typeof window === "undefined") return "";
  try {
    return window.localStorage.getItem(PLAYER_KEY)?.trim() ?? "";
  } catch {
    return "";
  }
}

export function saveFleetPlayerId(playerId: string): void {
  if (typeof window === "undefined") return;
  try {
    if (playerId) window.localStorage.setItem(PLAYER_KEY, playerId);
    else window.localStorage.removeItem(PLAYER_KEY);
  } catch {
    /* ignore */
  }
}
