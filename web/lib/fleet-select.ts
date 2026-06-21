/** Shared placeholder copy for instance/player dropdowns while fleet lists load. */

export const INSTANCE_LOADING_LABEL = "Loading instances…";
export const INSTANCE_EMPTY_LABEL = "No instances online";
export const PLAYER_LOADING_LABEL = "Loading players…";
export const PLAYER_EMPTY_LABEL = "No players configured";

export function instanceSelectPlaceholder(loading: boolean, empty: boolean): string {
  if (loading) return INSTANCE_LOADING_LABEL;
  if (empty) return INSTANCE_EMPTY_LABEL;
  return "Select instance…";
}

export function playerSelectPlaceholder(loading: boolean, empty: boolean): string {
  if (loading) return PLAYER_LOADING_LABEL;
  if (empty) return PLAYER_EMPTY_LABEL;
  return "Select player…";
}
