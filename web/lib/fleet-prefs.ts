const INSTANCE_KEY = "wos.fleet.instanceId";
const PLAYER_KEY = "wos.fleet.playerId";

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
