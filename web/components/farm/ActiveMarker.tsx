import { Icon } from "@/components/ui";
import { type ActiveInGame, activeTitle } from "@/lib/farm/types";

/** Small "in game" badge shown next to an account/character that is live. */
export function ActiveMarker({ active }: { active: ActiveInGame }) {
  return (
    <span
      className="inline-flex h-5 w-5 items-center justify-center rounded-full border border-emerald-300/50 bg-emerald-400/15 text-emerald-200"
      title={activeTitle(active)}
      aria-label="In game"
    >
      <Icon name="play" size="sm" />
    </span>
  );
}
